"""Owner-private, transactional mailbox for the SSH transport. Never executes text."""
import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import time
import uuid

NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
KINDS = frozenset({'task_request', 'task_accepted', 'task_progress', 'task_completed', 'task_failed', 'agent_message'})
STATUSES = {'task_accepted': 'accepted', 'task_progress': 'running', 'task_completed': 'completed', 'task_failed': 'failed'}
BATCH = 16
WIRE_LIMIT = 262144


def name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError('Use 1–64 ASCII letters, digits, dot, underscore or hyphen; start with a letter/digit')
    return value


def identifier(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('Canonical UUID required')
    return value


def private_directory(path):
    # Same owner-only persistence pattern as ssh-remote-tools; no Tailscale import.
    if path.is_symlink():
        raise RuntimeError('State directory cannot be a symlink')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == 'nt':
        windows_acl(path, directory=True)
    else:
        path.chmod(0o700)


def windows_powershell_env():
    # A pwsh parent may export PS7 module paths incompatible with Windows PS5.1.
    # Let the child reconstruct its own built-in module search path.
    environment = dict(os.environ)
    for key in list(environment):
        if key.lower() == 'psmodulepath':
            environment.pop(key)
    return environment


def windows_acl(path, *, directory=False):
    # Replace the DACL, rather than retaining any preexisting explicit Everyone ACE.
    path_literal = str(path).replace("'", "''")
    inherit = 'OICI' if directory else ''
    script = (
        "$ErrorActionPreference='Stop'; "
        "$owner=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
        f"$acl=Get-Acl -LiteralPath '{path_literal}'; "
        f"$sddl='D:P(A;{inherit};FA;;;' + $owner + ')(A;{inherit};FA;;;SY)'; "
        "$acl.SetSecurityDescriptorSddlForm($sddl,[System.Security.AccessControl.AccessControlSections]::Access); "
        f"Set-Acl -LiteralPath '{path_literal}' -AclObject $acl"
    )
    encoded = base64.b64encode(script.encode('utf-16-le')).decode()
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, timeout=15, env=windows_powershell_env())
    if result.returncode:
        detail = result.stderr.decode('utf-8', errors='replace').replace(str(path), '<state>')
        raise RuntimeError('Cannot restrict Windows state permissions: ' + detail[-1500:])


def default_root():
    if os.name == 'nt':
        return Path(os.environ['LOCALAPPDATA']) / 'codex-bridge'
    return Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'codex-bridge'


def digest(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def validate_message(message):
    if not isinstance(message, dict) or set(message) != {'id', 'task_id', 'type', 'text', 'sender', 'recipient'}:
        raise ValueError('Invalid message fields')
    for field in ('id', 'task_id', 'sender', 'recipient'):
        identifier(message[field])
    if message['type'] not in KINDS or not isinstance(message['text'], str) or len(message['text'].encode()) > 8192:
        raise ValueError('Unsupported message kind or text larger than 8192 bytes')


class Store:
    def __init__(self, root=None, profile='default'):
        self.root = (Path(root) if root is not None else default_root()) / name(profile)
        private_directory(self.root)
        self.path = self.root / 'security.sqlite3'
        if self.path.is_symlink():
            raise RuntimeError('State database cannot be a symlink')
        if os.name == 'nt' and self.path.exists():
            windows_acl(self.path)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS peers(id TEXT PRIMARY KEY, alias TEXT UNIQUE NOT NULL,
                    direction TEXT NOT NULL, secret TEXT NOT NULL, route TEXT, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, sender TEXT NOT NULL, recipient TEXT NOT NULL,
                    task_id TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL,
                    delivered INTEGER NOT NULL DEFAULT 0, seen INTEGER NOT NULL DEFAULT 0, needs_ack INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, digest TEXT NOT NULL, sender TEXT NOT NULL, recipient TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, peer TEXT NOT NULL, owned INTEGER NOT NULL,
                    status TEXT NOT NULL, text TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at REAL NOT NULL, action TEXT NOT NULL, reference TEXT NOT NULL);
            ''')
        if os.name == 'nt':
            windows_acl(self.path)
        else:
            self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _audit(db, action, reference):
        db.execute('INSERT INTO audit(at,action,reference) VALUES (?,?,?)', (time.time(), action, reference))
        db.execute('DELETE FROM audit WHERE id <= (SELECT MAX(id)-10000 FROM audit)')

    def initialize(self, display_name):
        name(display_name)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute("SELECT value FROM settings WHERE key='identity'").fetchone()
            if current:
                return json.loads(current[0])
            identity = {'agent_id': str(uuid.uuid4()), 'name': display_name, 'protocol': 1}
            db.execute('INSERT INTO settings VALUES (?,?)', ('identity', json.dumps(identity)))
            self._audit(db, 'initialize', identity['agent_id'])
        return identity

    def identity(self):
        with self.db() as db:
            row = db.execute("SELECT value FROM settings WHERE key='identity'").fetchone()
        if not row:
            raise RuntimeError('Initialize this security profile first')
        return json.loads(row[0])

    def pair_code(self):
        self.identity()
        raw = base64.b32encode(secrets.token_bytes(10)).decode()
        with self.db() as db:
            invitation = {'hash': digest(raw), 'expires': time.time() + 300, 'attempts': 0}
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('invitation', json.dumps(invitation)))
            self._audit(db, 'pair_code_created', '')
        return '-'.join(raw[i:i+4] for i in range(0, 16, 4))

    def enroll(self, agent_id, display_name, code):
        identifier(agent_id)
        name(display_name)
        own = self.identity()
        if agent_id == own['agent_id']:
            raise ValueError('Cannot pair an agent with itself')
        if not isinstance(code, str):
            raise ValueError('Invalid enrollment credential')
        normalized = code.upper().replace('-', '').replace(' ', '')
        error = None
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM settings WHERE key='invitation'").fetchone()
            invitation = json.loads(row[0]) if row else None
            if not invitation or invitation['expires'] <= time.time() or invitation['attempts'] >= 5:
                raise PermissionError('Pairing window closed')
            if not hmac.compare_digest(invitation['hash'], digest(normalized)):
                invitation['attempts'] += 1
                db.execute("UPDATE settings SET value=? WHERE key='invitation'", (json.dumps(invitation),))
                self._audit(db, 'pair_code_rejected', agent_id)
                error = PermissionError('Pairing code rejected')
            else:
                existing = db.execute('SELECT revoked FROM peers WHERE id=?', (agent_id,)).fetchone()
                if existing and not existing['revoked']:
                    raise RuntimeError('Peer already exists; revoke locally before re-pairing')
                alias = display_name
                if db.execute('SELECT id FROM peers WHERE alias=? AND id<>?', (alias, agent_id)).fetchone():
                    alias = agent_id
                token = secrets.token_urlsafe(32)
                db.execute('INSERT OR REPLACE INTO peers VALUES (?,?,?,?,?,0)', (agent_id, alias, 'inbound', digest(token), None))
                db.execute("DELETE FROM settings WHERE key='invitation'")
                self._audit(db, 'peer_enrolled', agent_id)
        if error:
            raise error
        return {**own, 'token': token}

    def add_route(self, alias, remote, route):
        name(alias)
        identifier(remote['agent_id'])
        token = remote.get('token', '')
        if not re.fullmatch('[A-Za-z0-9_-]{43}', token):
            raise ValueError('Invalid remote credential')
        if remote['agent_id'] == self.identity()['agent_id']:
            raise ValueError('Cannot pair an agent with itself')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT id,revoked FROM peers WHERE alias=? OR id=?', (alias, remote['agent_id'])).fetchall()
            if any(not row['revoked'] for row in old):
                raise RuntimeError('Peer exists; revoke before replacing a route')
            db.execute('DELETE FROM peers WHERE alias=? OR id=?', (alias, remote['agent_id']))
            db.execute('INSERT INTO peers VALUES (?,?,?,?,?,0)',
                       (remote['agent_id'], alias, 'ssh', token, json.dumps(route)))
            self._audit(db, 'ssh_route_added', remote['agent_id'])

    def peers(self, internal=False):
        with self.db() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM peers WHERE revoked=0 ORDER BY alias')]
        if internal:
            return rows
        return [{k: row[k] for k in ('id', 'alias', 'direction')} for row in rows]

    def peer(self, alias):
        matches = [p for p in self.peers(internal=True) if alias in (p['id'], p['alias'])]
        if len(matches) != 1:
            raise ValueError('Choose one active peer alias from security peers')
        return matches[0]

    @staticmethod
    def wire(row):
        return {'id': row['id'], 'sender': row['sender'], 'recipient': row['recipient'],
                'task_id': row['task_id'], 'type': row['kind'], 'text': row['text']}

    def _insert(self, db, message, *, incoming, needs_ack=False):
        validate_message(message)
        old = db.execute('SELECT * FROM messages WHERE id=?', (message['id'],)).fetchone()
        if old:
            if self.wire(old) != message:
                raise ValueError('Message ID collision')
            return
        receipt = db.execute('SELECT digest FROM receipts WHERE id=?', (message['id'],)).fetchone()
        fingerprint = digest(json.dumps(message, sort_keys=True, ensure_ascii=False))
        if receipt:
            if receipt[0] != fingerprint:
                raise ValueError('Message ID collision')
            return
        if db.execute('SELECT COUNT(*) FROM messages').fetchone()[0] >= 10000:
            raise RuntimeError('Mailbox full; prune locally acknowledged/delivered messages')
        kind = message['type']
        peer_id = message['sender'] if incoming else message['recipient']
        task = db.execute('SELECT * FROM tasks WHERE id=?', (message['task_id'],)).fetchone()
        if kind == 'task_request':
            if task:
                raise ValueError('Task ID already exists')
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?)', (message['task_id'], peer_id, not incoming, 'requested', ''))
        elif kind in STATUSES:
            if not task or task['peer'] != peer_id or bool(task['owned']) != incoming:
                raise ValueError('Task response has no matching request or peer')
            if task['status'] in ('completed', 'failed'):
                raise ValueError('Task is already terminal')
            next_status = STATUSES[kind]
            if task['status'] == 'running' and next_status == 'accepted':
                raise ValueError('Invalid task state regression')
            db.execute('UPDATE tasks SET status=?, text=? WHERE id=?', (next_status, message['text'], message['task_id']))
        db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (message['id'], message['sender'], message['recipient'], message['task_id'], kind,
                    message['text'], time.time(), incoming, False, needs_ack))
        db.execute('INSERT INTO receipts VALUES (?,?,?,?)', (message['id'], fingerprint, message['sender'], message['recipient']))
        self._audit(db, 'message_received' if incoming else 'message_queued', message['id'])

    def send(self, alias, text, kind='task_request', task_id=None):
        peer = self.peer(alias)
        own = self.identity()
        message = {'id': str(uuid.uuid4()), 'task_id': task_id or str(uuid.uuid4()),
                   'sender': own['agent_id'], 'recipient': peer['id'], 'type': kind, 'text': text}
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._insert(db, message, incoming=False)
        return message

    def inbox(self):
        own = self.identity()['agent_id']
        with self.db() as db:
            return [self.wire(row) for row in db.execute('SELECT * FROM messages WHERE recipient=? AND seen=0 ORDER BY rowid LIMIT 50', (own,))]

    def ack(self, message_id):
        identifier(message_id)
        with self.db() as db:
            db.execute('UPDATE messages SET seen=1 WHERE id=? AND recipient=?', (message_id, self.identity()['agent_id']))

    def task(self, task_id):
        identifier(task_id)
        with self.db() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise ValueError('Unknown task ID')
        return dict(row)

    def revoke(self, alias):
        peer = self.peer(alias)
        with self.db() as db:
            db.execute("UPDATE peers SET revoked=1, secret='' WHERE id=?", (peer['id'],))
            self._audit(db, 'peer_revoked', peer['id'])

    def prune(self):
        own = self.identity()['agent_id']
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            cursor = db.execute('DELETE FROM messages WHERE delivered=1 AND (sender=? OR seen=1) AND needs_ack=0', (own,))
            count = cursor.rowcount
            db.execute("UPDATE tasks SET text='' WHERE status IN ('completed','failed') AND NOT EXISTS (SELECT 1 FROM messages WHERE messages.task_id=tasks.id)")
            self._audit(db, 'messages_pruned', str(count))
        return {'deleted_messages': count, 'replay_tombstones_retained': True}

    def pending(self, peer_id):
        own = self.identity()['agent_id']
        with self.db() as db:
            messages = [self.wire(row) for row in db.execute('SELECT * FROM messages WHERE sender=? AND recipient=? AND delivered=0 ORDER BY rowid LIMIT ?', (own, peer_id, BATCH))]
            acks = [row[0] for row in db.execute('SELECT id FROM messages WHERE sender=? AND recipient=? AND needs_ack=1 ORDER BY rowid LIMIT ?', (peer_id, own, BATCH))]
        return messages, acks

    def exchange(self, caller, token, messages, acks):
        own = self.identity()['agent_id']
        identifier(caller)
        if not isinstance(token, str) or len(token) > 128:
            raise PermissionError('Peer credential rejected')
        self._batch(messages, acks)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            peer = db.execute('SELECT * FROM peers WHERE id=? AND direction=? AND revoked=0', (caller, 'inbound')).fetchone()
            if not peer or not hmac.compare_digest(peer['secret'], digest(token)):
                raise PermissionError('Peer credential rejected')
            for message in messages:
                validate_message(message)
                if message['sender'] != caller or message['recipient'] != own:
                    raise PermissionError('Message identity mismatch')
                self._insert(db, message, incoming=True)
            for mid in acks:
                identifier(mid)
                row = db.execute('SELECT sender,recipient FROM messages WHERE id=?', (mid,)).fetchone()
                if row:
                    if row['sender'] != own or row['recipient'] != caller:
                        raise PermissionError('Acknowledgment belongs to a different route')
                    db.execute('UPDATE messages SET delivered=1 WHERE id=?', (mid,))
                else:
                    receipt = db.execute('SELECT sender,recipient FROM receipts WHERE id=?', (mid,)).fetchone()
                    if not receipt or receipt['sender'] != own or receipt['recipient'] != caller:
                        raise ValueError('Unknown acknowledgment or route mismatch')
            outgoing = [self.wire(row) for row in db.execute('SELECT * FROM messages WHERE sender=? AND recipient=? AND delivered=0 ORDER BY rowid LIMIT ?', (own, caller, BATCH))]
        return {'agent_id': own, 'accepted': [m['id'] for m in messages], 'acked': acks, 'messages': outgoing}

    @staticmethod
    def _batch(messages, acks):
        if not isinstance(messages, list) or not isinstance(acks, list) or len(messages) > BATCH or len(acks) > BATCH:
            raise ValueError('Exchange batch limit exceeded')

    def apply_exchange(self, peer_id, sent, acks, response):
        own = self.identity()['agent_id']
        expected = {'agent_id', 'accepted', 'acked', 'messages'}
        if not isinstance(response, dict) or set(response) != expected or response['agent_id'] != peer_id:
            raise ValueError('Unexpected peer identity or response fields')
        if response['accepted'] != [m['id'] for m in sent] or response['acked'] != acks:
            raise ValueError('Unexpected exchange receipt')
        self._batch(response['messages'], response['acked'])
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT 1 FROM peers WHERE id=? AND direction='ssh' AND revoked=0", (peer_id,)).fetchone():
                raise PermissionError('Peer was revoked during exchange')
            for message in response['messages']:
                validate_message(message)
                if message['sender'] != peer_id or message['recipient'] != own:
                    raise PermissionError('Message identity mismatch')
                self._insert(db, message, incoming=True, needs_ack=True)
            db.executemany('UPDATE messages SET delivered=1 WHERE id=? AND sender=? AND recipient=?',
                           [(m['id'], own, peer_id) for m in sent])
            db.executemany('UPDATE messages SET needs_ack=0 WHERE id=? AND sender=? AND recipient=?',
                           [(mid, peer_id, own) for mid in acks])
