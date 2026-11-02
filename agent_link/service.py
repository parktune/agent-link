"""Keep the hub / spoke daemon running: a systemd user service, or a detached process as fallback."""
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import time

from .state import default_socket, private_directory, state_root

UNIT = '''[Unit]
Description=agent-link {kind}

[Service]
ExecStart={command}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
'''


def executable(name):
    """Absolute path of a console script next to the running interpreter (the repo's .venv/bin)."""
    return str(Path(sys.executable).with_name(name))


def daemon_argv(kind, host_name=None):
    if kind == 'hub':
        return [executable('agent-link'), 'hub', 'serve']
    return [executable('agent-link'), 'daemon'] + (['--host-name', host_name] if host_name else [])


def _systemd(kind, argv):
    if os.environ.get('AGENT_HUB_NO_SYSTEMD') or os.environ.get('AGENT_HUB_STATE') or not shutil.which('systemctl'):
        return False  # custom state dirs (tests, loopback checks) must never become user services
    unit = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'systemd/user' / f'agent-link-{kind}.service'
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(UNIT.format(kind=kind, command=shlex.join(argv)))
    for command in (['daemon-reload'], ['enable', '--now', unit.name], ['restart', unit.name]):
        if subprocess.run(['systemctl', '--user', *command], capture_output=True, timeout=30).returncode:
            unit.unlink(missing_ok=True)
            subprocess.run(['systemctl', '--user', 'daemon-reload'], capture_output=True, timeout=30)
            return False
    return True


def _background(kind, argv):
    log = private_directory(state_root()) / f'{kind}.out'
    with open(log, 'ab') as handle:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=handle, stderr=handle, start_new_session=True)


def alive(socket_path=None):
    """True when a daemon accepts connections (a leftover socket file from a dead one does not count)."""
    probe = socket.socket(socket.AF_UNIX)
    try:
        probe.connect(os.fspath(socket_path or default_socket()))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def lingering():
    user = os.environ.get('USER', '')
    result = subprocess.run(['loginctl', 'show-user', user, '-p', 'Linger', '--value'], capture_output=True,
                            text=True, timeout=10) if shutil.which('loginctl') and user else None
    return result is None or result.stdout.strip() != 'no'


def start(kind, host_name=None, socket_path=None, wait=15):
    """Start the daemon and wait for its socket. Returns how it was started."""
    argv = daemon_argv(kind, host_name)
    how = 'systemd' if _systemd(kind, argv) else None
    if how is None:
        _background(kind, argv)
        how = 'background'
    socket_path = Path(socket_path or default_socket())
    deadline = time.monotonic() + wait
    while not alive(socket_path):
        if time.monotonic() > deadline:
            raise SystemExit(f'agent-link {kind} did not start; see {state_root()}/{kind}.out or '
                             f'`journalctl --user -u agent-link-{kind}`')
        time.sleep(0.1)
    return how
