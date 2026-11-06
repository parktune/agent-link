"""Scenarios from spec chapter 14 that run without models."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from unittest import mock

from agent_link.protocol import PROTOCOL_VERSION, ProtocolError, encode, read_frame
from agent_link.spoke import INVITE_ATTEMPTS
from tests.support import Environment, until


class SameHostTests(Environment):
    async def test_s2_s10_same_host_delivery_skips_the_hub_and_works_without_it(self):
        await self.server('gpu1', allow_origination='host')
        a = await self.session('gpu1', 'claude', 'alpha')
        b = await self.session('gpu1', 'claude', 'beta')
        c = await self.session('gpu1', 'claude', 'gamma')
        result = await a.send('beta', 'hello beta')
        self.assertEqual((result['status'], result['route']), ('delivered', 'local'))
        self.assertEqual([m['content'] for m in b.received], ['hello beta'])
        self.assertEqual(c.received, [])  # S2: only the addressed session
        self.assertEqual(b.received[0]['to'], 'gpu1:claude:beta')

    async def test_s12_explicit_own_host_missing_is_not_sent_up_and_names_are_unique(self):
        await self.server('gpu1', allow_origination='all')
        a = await self.session('gpu1', 'claude', 'alpha')
        await self.session('gpu1', 'claude', 'head')
        with self.assertRaisesRegex(Exception, 'not_found'):
            await a.send('gpu1:claude:ghost', 'x')
        self.assertEqual((await a.send('head', 'x'))['route'], 'local')
        self.assertEqual(self.spokes['gpu1'].store.outbox_stats()['items'], 0)  # nothing went to the hub

    async def test_origination_none_only_allows_replies(self):
        await self.server('gpu1', allow_origination='none')
        a = await self.session('gpu1', 'claude', 'alpha')
        await self.session('gpu1', 'claude', 'beta')
        with self.assertRaisesRegex(Exception, 'origination_denied'):
            await a.send('beta', 'unsolicited')
        with self.assertRaisesRegex(Exception, 'unauthorized'):
            await a.send('beta', 'fake reply', 'reply', '01900000-0000-7000-8000-000000000000')


class MailboxTests(Environment):
    async def test_s5_s17_closed_box_only_and_failed_push_sessions_get_mailbox_files(self):
        await self.server('gpu1', allow_origination='host')
        a = await self.session('gpu1', 'claude', 'alpha')
        boxed = await self.session('gpu1', 'codex', 'boxed', delivery='box')
        failing = await self.session('gpu1', 'claude', 'failing', fail=True)
        closed = await self.session('gpu1', 'claude', 'closed')
        await closed.stop()
        self.sessions.remove(closed)
        await until(lambda: self.spokes['gpu1'].agents['gpu1:claude:closed'].conn is None)
        self.assertEqual((await a.send('codex:boxed', 'b'))['status'], 'stored')
        self.assertEqual((await a.send('failing', 'f'))['status'], 'stored')
        self.assertEqual((await a.send('closed', 'c'))['status'], 'stored_offline')
        files = sorted(p.relative_to(self.root / 'gpu1' / 'box').parts[0] for p in (self.root / 'gpu1' / 'box').rglob('*.md'))
        self.assertEqual(files, ['claude-closed', 'claude-failing', 'codex-boxed'])
        text = next((self.root / 'gpu1/box/codex-boxed/received').glob('*.md')).read_text()
        self.assertNotIn('account', text)
        self.assertIn('from: gpu1:claude:alpha', text)
        messages = await boxed.box()
        self.assertEqual([m['content'] for m in messages], ['b'])
        self.assertEqual(await boxed.box(), [])  # moved to received/read/
        self.assertTrue(list((self.root / 'gpu1/box/codex-boxed/received/read').glob('*.md')))

    async def test_registry_survives_daemon_restart_so_mail_still_arrives(self):
        spoke = await self.server('gpu1', allow_origination='host')
        await self.session('gpu1', 'claude', 'beta')
        for session in self.sessions:
            await session.stop()
        self.sessions.clear()
        await spoke.close()
        self.spokes.pop('gpu1')
        await self.server('gpu1', allow_origination='host')
        a = await self.session('gpu1', 'claude', 'alpha')
        self.assertEqual((await a.send('beta', 'later'))['status'], 'stored_offline')
        found = self.spokes['gpu1'].store.agent_for_session('session-beta')
        self.assertEqual(found['name'], 'beta')


class HubTests(Environment):
    async def two_hosts(self, **config):
        await self.server('gpu1', **config)
        await self.server('gpu2', **config)
        self.enroll('gpu1')
        self.enroll('gpu2')
        await self.start_hub()

    async def test_s1_round_trip_local_to_server_and_reply_back(self):
        await self.two_hosts()
        main = await self.session('local', 'claude', 'main')
        head = await self.session('gpu1', 'claude', 'project-head')
        await self.connected('gpu1')
        listed = {a['id'] for a in self.hub.list_agents()}
        self.assertTrue({'local:claude:main', 'gpu1:claude:project-head'} <= listed)
        sent = await main.send('gpu1:claude:project-head', '현황 보고 부탁해')
        self.assertEqual((sent['status'], sent['route']), ('delivered', 'hub'))
        request = head.received[0]
        self.assertEqual(request['from'], 'local:claude:main')
        reply = await head.send(request['from'], '보고: 정상', 'reply', request['id'])
        self.assertEqual(reply['status'], 'delivered')
        await until(lambda: main.received)
        self.assertEqual((main.received[0]['content'], main.received[0]['in_reply_to'], main.received[0]['hops']),
                         ('보고: 정상', request['id'], 1))
        self.assertEqual([m['status'] for m in self.hub.store.recent()], ['delivered', 'delivered'])
        self.assertNotIn('content', self.hub.store.recent()[0])

    async def test_s9_reply_to_reply_is_refused(self):
        await self.two_hosts()
        main = await self.session('local', 'claude', 'main')
        head = await self.session('gpu1', 'claude', 'head')
        await self.connected('gpu1')
        await main.send('gpu1:claude:head', 'q')
        await head.send('local:claude:main', 'a', 'reply', head.received[0]['id'])
        await until(lambda: main.received)
        for type in ('reply', 'request'):
            with self.assertRaisesRegex(Exception, 'reply_to_reply'):
                await main.send('gpu1:claude:head', 'thanks', type, main.received[0]['id'])

    async def test_s9_hop_limit_on_request_chains(self):
        await self.two_hosts(max_hops=1, allow_origination='all')
        main = await self.session('local', 'claude', 'main')
        head = await self.session('gpu1', 'claude', 'head')
        await self.connected('gpu1')
        await main.send('gpu1:claude:head', 'q')
        follow = await head.send('local:claude:main', 'sub-question', 'request', head.received[0]['id'])
        self.assertEqual(follow['status'], 'delivered')
        await until(lambda: main.received)
        self.assertEqual(main.received[0]['hops'], 1)
        with self.assertRaisesRegex(Exception, 'hops_exceeded'):
            await main.send('gpu1:claude:head', 'deeper', 'request', main.received[0]['id'])

    async def test_s11_omitted_host_goes_up_to_the_hub(self):
        await self.two_hosts(allow_origination='all')
        a = await self.session('gpu1', 'claude', 'alpha')
        trainer = await self.session('gpu2', 'codex', 'trainer')
        await self.connected('gpu1')
        await self.connected('gpu2')
        result = await a.send('trainer', 'start')
        self.assertEqual(result['route'], 'hub')
        await until(lambda: trainer.received)
        self.assertEqual(trainer.received[0]['to'], 'gpu2:codex:trainer')

    async def test_hub_status_lists_hosts_and_agents(self):
        await self.two_hosts()
        await self.session('local', 'claude', 'main')
        await self.session('gpu1', 'claude', 'head')
        await self.connected('gpu1')
        await self.connected('gpu2')
        from agent_link.client import SpokeClient
        client = SpokeClient(self.root / 'hub.sock')
        await client.connect()
        try:
            status = await client.request('status')
        finally:
            await client.close()
        self.assertEqual((status['role'], status['host'], status['hosts_connected']), ('hub', 'local', 2))
        self.assertEqual({a['id'] for a in status['agents']}, {'local:claude:main', 'gpu1:claude:head'})

    async def test_servers_see_agents_on_other_hosts_and_reach_them_by_name(self):
        await self.two_hosts()
        main = await self.session('local', 'claude', 'agent-link-test')
        head = await self.session('gpu1', 'claude', 'head')
        trainer = await self.session('gpu2', 'codex', 'trainer')
        await self.connected('gpu1')
        await self.connected('gpu2')
        gpu2 = self.spokes['gpu2']
        await until(lambda: {a['id'] for a in gpu2.directory} == {'local:claude:agent-link-test', 'gpu1:claude:head'})
        self.assertEqual(set(gpu2.directory[0]), {'id', 'host', 'tool', 'name', 'status', 'delivery'})  # no cwd
        listed = {a['id'] for a in gpu2.list_agents()}
        self.assertIn('gpu2:codex:trainer', listed)
        self.assertIn('local:claude:agent-link-test', listed)
        # A server session asks the PC session by name alone (default policy allows it).
        sent = await trainer.send('agent-link-test', '자료 요청')
        self.assertEqual(sent['route'], 'hub')
        await until(lambda: main.received)
        self.assertEqual(main.received[0]['from'], 'gpu2:codex:trainer')
        await head.stop()
        self.sessions.remove(head)
        await until(lambda: any(a['id'] == 'gpu1:claude:head' and a['status'] == 'offline' for a in gpu2.directory))

    async def test_unknown_session_on_remote_host_is_not_found(self):
        await self.two_hosts()
        main = await self.session('local', 'claude', 'main')
        await self.connected('gpu1')
        with self.assertRaisesRegex(Exception, 'not_found'):
            await main.send('gpu1:claude:nobody', 'x')

    async def test_s6_one_unreachable_host_does_not_affect_others(self):
        self.broken.add('gpu1')
        await self.two_hosts()
        main = await self.session('local', 'claude', 'main')
        worker = await self.session('gpu2', 'claude', 'worker')
        await self.connected('gpu2')
        self.assertEqual((await main.send('gpu2:claude:worker', 'ok?'))['status'], 'delivered')
        self.assertFalse(self.hub.links['gpu1'].connected)
        with self.assertRaisesRegex(Exception, 'not_found|offline'):
            await main.send('gpu1:claude:head', 'x')
        self.broken.discard('gpu1')
        self.hub.links['gpu1'].transport = self.transport({'name': 'gpu1'})
        await until(lambda: self.hub.links['gpu1'].connected, timeout=15)
        self.assertEqual(worker.received[0]['content'], 'ok?')

    async def test_s7_s16_hub_restart_resumes_outbox_without_duplicates(self):
        await self.two_hosts()
        main = await self.session('local', 'claude', 'main')
        head = await self.session('gpu1', 'claude', 'head')
        await self.connected('gpu1')
        await main.send('gpu1:claude:head', 'q')
        request = head.received[0]
        await main.stop()
        self.sessions.remove(main)
        await self.stop_hub()
        queued = await head.send('local:claude:main', 'answer while hub is down', 'reply', request['id'])
        self.assertEqual(queued['status'], 'queued')
        await self.start_hub()
        main = await self.session('local', 'claude', 'main')
        await self.connected('gpu1')
        await until(lambda: self.spokes['gpu1'].store.outbox_stats()['items'] == 0)
        # The reply reached the hub after restart; main was online again, so it was pushed once.
        self.assertEqual(self.hub.store.message(queued['id'])['status'], 'delivered')
        self.assertEqual([m['content'] for m in main.received], ['answer while hub is down'])
        # Replaying the same outbox entry (e.g. lost ack) is acknowledged without a second delivery.
        ack = await self.hub.handle_outbox('gpu1', {'op': 'outbox', 'seq': 1, 'msg': {}})
        self.assertEqual(ack, {'op': 'ack', 'seq': 1})
        self.assertEqual(len(main.received), 1)

class EndpointSecurityTests(Environment):
    async def endpoint(self, frames):
        process = await asyncio.create_subprocess_exec(
            __import__('sys').executable, '-m', 'agent_link', 'endpoint', '--socket',
            str(self.root / 'gpu1' / 's.sock'), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        for frame in frames:
            process.stdin.write(encode(frame))
        await process.stdin.drain()
        replies = [await asyncio.wait_for(read_frame(process.stdout), 10)]
        process.stdin.close()
        await process.wait()
        return replies[0]

    async def test_s15_tokens_revocation_and_allowlist(self):
        spoke = await self.server('gpu1')
        token = self.enroll('gpu1')
        hello = {'op': 'hello', 'v': PROTOCOL_VERSION}
        self.assertEqual((await self.endpoint([hello]))['code'], 'unauthorized')
        self.assertEqual((await self.endpoint([{**hello, 'token': 'x' * 43}]))['code'], 'unauthorized')
        self.assertEqual((await self.endpoint([{**hello, 'token': token}]))['op'], 'hello_ok')
        self.assertEqual((await self.endpoint([{'op': 'register', 'tool': 'claude', 'name': 'x'}]))['code'], 'unauthorized')
        self.assertEqual((await self.endpoint([{'op': 'deliver', 'msg': {}}]))['code'], 'unauthorized')
        spoke.store.revoke()
        self.assertEqual((await self.endpoint([{**hello, 'token': token}]))['code'], 'unauthorized')

    async def test_s15_shell_like_frames_are_rejected_after_hello(self):
        await self.server('gpu1')
        token = self.enroll('gpu1')
        process = await asyncio.create_subprocess_exec(
            __import__('sys').executable, '-m', 'agent_link', 'endpoint', '--socket',
            str(self.root / 'gpu1' / 's.sock'), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        process.stdin.write(encode({'op': 'hello', 'v': PROTOCOL_VERSION, 'token': token}))
        process.stdin.write(encode({'op': 'exec', 'command': 'id'}))
        await process.stdin.drain()
        frames = []
        while (frame := await asyncio.wait_for(read_frame(process.stdout), 10)) is not None:
            frames.append(frame['op'] if frame['op'] != 'error' else frame['code'])
        await process.wait()
        self.assertIn('unauthorized', frames)  # hello_ok may or may not arrive before the endpoint closes

    async def test_s14_invitation_expiry_and_attempt_limit(self):
        spoke = await self.server('gpu1')
        code, _ = spoke.store.invite(now=0)
        with self.assertRaisesRegex(Exception, 'expired'):
            spoke.store.enroll(code, 'x' * 43)
        code, _ = spoke.store.invite()
        for _ in range(INVITE_ATTEMPTS):
            with self.assertRaisesRegex(Exception, 'rejected'):
                spoke.store.enroll('AAAA-AAAA-AAAA-AAAA', 'x' * 43)
        with self.assertRaisesRegex(Exception, 'closed'):
            spoke.store.enroll(code, 'x' * 43)
        self.assertFalse(spoke.store.check_token('x' * 43))


class HubIdentityTests(Environment):
    async def endpoint_session(self, frames, ssh_connection):
        import os, sys
        env = {**os.environ, 'SSH_CONNECTION': ssh_connection}
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'agent_link', 'endpoint', '--socket', str(self.root / 'gpu1' / 's.sock'),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, env=env)
        for frame in frames:
            process.stdin.write(encode(frame))
        await process.stdin.drain()
        reply = await asyncio.wait_for(read_frame(process.stdout), 10)
        return process, reply

    async def test_status_shows_which_hub_is_connected_and_from_where(self):
        spoke = await self.server('gpu1')
        code, _ = spoke.store.invite()
        token = 'T' * 43
        process, reply = await self.endpoint_session(
            [{'op': 'enroll', 'code': code, 'token': token, 'hub': {'name': 'my-pc', 'user': 'alice', 'x': 'drop'},
              'peer': 'forged'}], '10.0.0.7 51234 10.0.0.1 22')
        self.assertEqual(reply['op'], 'enroll_ok')
        await process.wait()
        self.assertEqual(spoke.store.enrolled_hubs()[0]['name'], 'my-pc')
        self.assertNotIn('x', spoke.store.enrolled_hubs()[0])
        process, reply = await self.endpoint_session(
            [{'op': 'hello', 'v': PROTOCOL_VERSION, 'token': token, 'hub': {'name': 'my-pc', 'user': 'alice'},
              'peer': '6.6.6.6:1'}], '10.0.0.7 51300 10.0.0.1 22')
        self.assertEqual(reply['op'], 'hello_ok')
        await until(lambda: spoke.link is not None)
        info = spoke.link.info
        self.assertEqual((info['name'], info['user'], info['from']), ('my-pc', 'alice', '10.0.0.7:51300'))
        self.assertIn('connected_since', info)
        process.stdin.close()
        await process.wait()

    async def test_tokens_from_before_identity_still_work(self):
        spoke = await self.server('gpu1')
        token = self.enroll('gpu1')  # enrolled without hub identity
        process, reply = await self.endpoint_session([{'op': 'hello', 'v': PROTOCOL_VERSION, 'token': token}], '')
        self.assertEqual(reply['op'], 'hello_ok')
        await until(lambda: spoke.link is not None)
        self.assertNotIn('name', spoke.link.info)
        self.assertIsNone(spoke.link.info['from'])
        process.stdin.close()
        await process.wait()


class PairedNameTests(Environment):
    """The host name chosen at pairing is kept on the server, so a restarted daemon cannot drift from the hub."""

    endpoint_session = HubIdentityTests.endpoint_session

    async def test_pairing_records_the_name_and_a_restart_keeps_it(self):
        spoke = await self.server('gpu1')
        code, _ = spoke.store.invite()
        process, reply = await self.endpoint_session([{'op': 'enroll', 'code': code, 'token': 'T' * 43}], '')
        self.assertEqual(reply, {'op': 'enroll_ok', 'host': 'gpu1'})
        await process.wait()
        self.assertEqual(spoke.store.paired_host(), 'gpu1')
        from agent_link import cli
        with tempfile.TemporaryDirectory() as state, mock.patch.dict(os.environ, {'AGENT_HUB_STATE': state}):
            # `agent-link up` without a name (or with the hostname) still comes back as the paired name.
            self.assertEqual(cli.daemon_host_name(spoke.store, 'old-hostname'), 'gpu1')
            Path(state, 'config.toml').write_text('host_name = "chosen"\n')
            self.assertEqual(cli.daemon_host_name(spoke.store, 'old-hostname'), 'chosen')  # an explicit setting wins
        fresh = await self.server('gpu2')
        with tempfile.TemporaryDirectory() as state, mock.patch.dict(os.environ, {'AGENT_HUB_STATE': state}):
            self.assertEqual(cli.daemon_host_name(fresh.store, 'gpu-server-2'), 'gpu-server-2')  # first pairing: the hub's name
            self.assertIsNone(cli.daemon_host_name(fresh.store))  # nothing at all: the short hostname

    async def test_a_token_from_before_0_5_7_learns_the_name_from_the_hub(self):
        spoke = await self.server('gpu1')
        token = self.enroll('gpu1')  # stored without a host name
        self.assertIsNone(spoke.store.paired_host())
        process, reply = await self.endpoint_session(
            [{'op': 'hello', 'v': PROTOCOL_VERSION, 'token': token, 'host': 'gpu1'}], '')
        self.assertEqual(reply['op'], 'hello_ok')
        self.assertEqual(spoke.store.paired_host(), 'gpu1')
        process.stdin.close()
        await process.wait()

    async def test_a_daemon_under_another_name_explains_how_to_fix_it(self):
        spoke = await self.server('gpu1')  # restarted under its hostname after pairing as gpu-server-2
        code, _ = spoke.store.invite()
        spoke.store.enroll(code, 'T' * 43, host='gpu-server-2')
        process, reply = await self.endpoint_session(
            [{'op': 'hello', 'v': PROTOCOL_VERSION, 'token': 'T' * 43, 'host': 'gpu-server-2'}], '')
        self.assertEqual(reply['code'], 'host_mismatch')
        self.assertIn('runs as "gpu1" but was paired with the hub as "gpu-server-2"', reply['detail'])
        self.assertIn('agent-link up --restart', reply['detail'])
        self.assertIsNone(spoke.link)
        await process.wait()

    def test_the_hub_names_a_mismatch_from_an_older_server(self):
        from agent_link.hub import HostLink
        link = HostLink.__new__(HostLink)
        link.name = 'gpu-server-2'
        link.check_hello({'op': 'hello_ok', 'host': 'gpu-server-2'})
        with self.assertRaisesRegex(ProtocolError, 'host_mismatch.*"old-hostname".*"gpu-server-2"'):
            link.check_hello({'op': 'hello_ok', 'host': 'old-hostname'})
        with self.assertRaisesRegex(ProtocolError, 'unauthorized'):
            link.check_hello({'op': 'error'})


class RemoveTests(Environment):
    async def ask(self, where, op, **fields):
        from agent_link.client import SpokeClient
        client = SpokeClient(self.root / ('hub.sock' if where == 'local' else f'{where}/s.sock'))
        await client.connect()
        try:
            return await client.request(op, **fields)
        finally:
            await client.close()

    async def setup_hosts(self):
        await self.server('gpu1')
        await self.server('gpu2')
        self.enroll('gpu1')
        self.enroll('gpu2')
        await self.start_hub()

    async def test_a_name_is_unique_per_tool_on_one_host(self):
        await self.server('gpu1', allow_origination='all')
        a = await self.session('gpu1', 'claude', 'alpha')
        await self.session('gpu1', 'claude', 'head')
        with self.assertRaisesRegex(Exception, 'name_taken'):
            await self.session('gpu1', 'claude', 'head', session_id='another-session')
        await self.session('gpu1', 'codex', 'head')  # another tool may share the name
        with self.assertRaisesRegex(Exception, 'ambiguous.*gpu1:claude:head, gpu1:codex:head.*full ID'):
            await a.send('head', 'x')
        self.assertEqual((await a.send('codex:head', 'x'))['route'], 'local')

    async def test_the_same_tool_and_name_may_live_on_different_hosts(self):
        await self.setup_hosts()
        main = await self.session('local', 'claude', 'main')
        head = await self.session('gpu1', 'claude', 'head')
        alpha = await self.session('gpu1', 'claude', 'alpha')
        await self.connected('gpu1')
        await self.connected('gpu2')
        await until(lambda: any(a['id'] == 'gpu1:claude:head' for a in self.spokes['gpu2'].directory))
        other = await self.session('gpu2', 'claude', 'head', session_id='another-session')  # another host: allowed
        with self.assertRaisesRegex(Exception, 'name_taken'):  # host, tool and name all the same: refused
            await self.session('gpu2', 'claude', 'head', session_id='third-session')
        await until(lambda: any(a['id'] == 'gpu2:claude:head' for a in self.hub.presence.get('gpu2', [])))
        with self.assertRaisesRegex(Exception, 'ambiguous.*gpu1:claude:head, gpu2:claude:head.*full ID'):
            await main.send('head', 'x')  # none on the sender's host, two elsewhere
        self.assertEqual((await main.send('gpu2:claude:head', 'to gpu2'))['route'], 'hub')
        self.assertEqual((await alpha.send('claude:head', 'same host'))['route'], 'local')  # the sender's host wins
        await until(lambda: other.received and head.received)
        self.assertEqual(([m['content'] for m in head.received], [m['content'] for m in other.received]),
                         (['same host'], ['to gpu2']))
        again = await head.client.request('register', tool='claude', name='head', session_id='session-head')
        self.assertEqual(again['id'], 'gpu1:claude:head')

    async def test_unregister_checks_every_host_and_takes_full_ids(self):
        await self.setup_hosts()
        await self.session('local', 'claude', 'main')
        await self.session('gpu1', 'claude', 'head')
        await self.session('gpu2', 'claude', 'head')
        await self.connected('gpu1')
        await self.connected('gpu2')
        await until(lambda: len([a for a in self.hub.all_agents() if a['name'] == 'head']) == 2)
        for where in ('local', 'gpu1'):  # also from a server that has one of them
            with self.assertRaisesRegex(Exception, 'ambiguous.*gpu1:claude:head, gpu2:claude:head'):
                await self.ask(where, 'remove', target='head')
        self.assertEqual((await self.ask('gpu1', 'remove', target='gpu2:claude:head'))['removed'], 'gpu2:claude:head')
        self.assertEqual((await self.ask('gpu2', 'remove', target='local:claude:main'))['removed'], 'local:claude:main')
        await until(lambda: len([a for a in self.hub.all_agents() if a['name'] == 'head']) == 1)
        self.assertEqual((await self.ask('local', 'remove', target='head'))['removed'], 'gpu1:claude:head')

    async def test_unregister_offline_takes_closed_sessions_of_connected_hosts_only(self):
        from agent_link.cli import offline_sessions
        await self.setup_hosts()
        await self.session('local', 'claude', 'main')
        await self.session('gpu1', 'claude', 'head')
        closed = [await self.session('local', 'claude', 'old'), await self.session('gpu1', 'codex', 'stale'),
                  await self.session('gpu2', 'claude', 'gone')]
        for session in closed:
            await session.stop()
            self.sessions.remove(session)
        await self.connected('gpu1')
        await self.connected('gpu2')
        await until(lambda: not any(a['status'] == 'online' for a in self.hub.presence.get('gpu2', [])))
        self.hub.host_offline('gpu2')  # gpu2 drops off: its sessions cannot be reached to unregister
        await until(lambda: any(a.get('reachable') is False for a in self.spokes['gpu1'].directory))
        info = await self.ask('local', 'status')
        self.assertEqual(offline_sessions(info), (['gpu1:codex:stale', 'local:claude:old'], ['gpu2']))
        self.assertEqual(offline_sessions(info, 'gpu1'), (['gpu1:codex:stale'], []))
        spoke = self.spokes['gpu1']
        from_server = {'role': 'spoke', 'host': 'gpu1', 'hub_connected': True,
                       'agents': [a.public() for a in spoke.agents.values()], 'others': spoke.directory}
        self.assertEqual(offline_sessions(from_server), (['gpu1:codex:stale', 'local:claude:old'], ['gpu2']))
        self.assertEqual(offline_sessions({**from_server, 'hub_connected': False}),
                         (['gpu1:codex:stale'], ['gpu2', 'local']))  # without the hub: only this server
        for identifier in offline_sessions(info)[0]:
            await self.ask('local', 'remove', target=identifier)
        await until(lambda: 'gpu1:codex:stale' not in spoke.agents)
        self.assertEqual(sorted(self.hub.local.agents), ['local:claude:main'])
        self.assertEqual(offline_sessions(await self.ask('local', 'status'))[0], [])

    async def test_remove_by_name_from_any_machine(self):
        await self.setup_hosts()
        main = await self.session('local', 'claude', 'main')
        head = await self.session('gpu1', 'claude', 'head')
        withdrawn = []
        head.client.on_withdrawn = withdrawn.append
        await self.connected('gpu1')
        await self.connected('gpu2')
        # From the PC: a session on a server.
        reply = await self.ask('local', 'remove', target='head')
        self.assertEqual((reply['removed'], reply['was']), ('gpu1:claude:head', 'open'))
        await until(lambda: 'gpu1:claude:head' not in self.spokes['gpu1'].agents)
        await until(lambda: withdrawn == ['removed'])
        with self.assertRaisesRegex(Exception, 'not_found'):
            await main.send('head', 'x')
        # From a server: a session on the PC goes up through the hub.
        await until(lambda: any(a['id'] == 'local:claude:main' for a in self.spokes['gpu2'].directory))
        reply = await self.ask('gpu2', 'remove', target='main')
        self.assertEqual(reply['removed'], 'local:claude:main')
        self.assertNotIn('local:claude:main', self.hub.local.agents)
        with self.assertRaisesRegex(Exception, 'not_found'):
            await self.ask('local', 'remove', target='nobody')

    async def test_closed_sessions_expire_after_15_days_unopened_or_without_messages(self):
        spoke = await self.server('gpu1')
        unopened = await self.session('gpu1', 'claude', 'unopened')
        quiet = await self.session('gpu1', 'claude', 'quiet')
        talker = await self.session('gpu1', 'claude', 'talker')
        open_quiet = await self.session('gpu1', 'claude', 'open-quiet')
        for session in (unopened, quiet, talker):
            await session.stop()
            self.sessions.remove(session)
        await until(lambda: all(spoke.agents[f'gpu1:claude:{n}'].conn is None for n in ('unopened', 'quiet', 'talker')))
        day = 86400
        spoke.agents['gpu1:claude:unopened'].last_seen -= 16 * day
        for name in ('quiet', 'talker', 'open-quiet'):
            spoke.agents[f'gpu1:claude:{name}'].last_message -= 16 * day
        await open_quiet.send('talker', 'still there?')  # a message counts for the receiver and the sender
        spoke.expire()
        self.assertEqual(set(spoke.agents), {'gpu1:claude:talker', 'gpu1:claude:open-quiet'})  # open: never expires
        self.assertEqual({a['id'] for a in spoke.store.known_agents()}, set(spoke.agents))
        self.assertEqual((spoke.config.offline_expiry_days, spoke.config.idle_expiry_days), (15, 15))
        self.assertIsNone(spoke.lookup('session-quiet'))  # nothing kept: registering again is up to the user

    async def test_a_second_connection_of_the_same_session_takes_the_registration_over(self):
        spoke = await self.server('gpu1')
        stale = await self.session('gpu1', 'claude', 'head', session_id='sid-1')
        withdrawn = []
        stale.client.on_withdrawn = withdrawn.append
        live = await self.session('gpu1', 'claude', 'head', session_id='sid-1')  # same session ID: not name_taken
        self.assertIs(spoke.agents['gpu1:claude:head'].conn.agent, spoke.agents['gpu1:claude:head'])
        await until(lambda: withdrawn)  # the stale one is told, so it stops registering again
        peer = await self.session('gpu1', 'claude', 'peer')
        self.assertEqual((await peer.send('head', 'hi'))['status'], 'delivered')
        self.assertEqual([m['content'] for m in live.received], ['hi'])
        self.assertEqual(stale.received, [])
        with self.assertRaisesRegex(Exception, 'name_taken'):  # another session still may not take the name
            await self.session('gpu1', 'claude', 'head', session_id='sid-2')

    async def test_reopening_a_session_does_not_count_as_message_traffic(self):
        spoke = await self.server('gpu1')
        head = await self.session('gpu1', 'claude', 'head')
        spoke.agents['gpu1:claude:head'].last_message -= 10 * 86400
        before = spoke.agents['gpu1:claude:head'].last_message
        await head.client.request('register', tool='claude', name='head', session_id='session-head')
        self.assertEqual(spoke.agents['gpu1:claude:head'].last_message, before)
        self.assertEqual(spoke.store.known_agents()[0]['last_message'], before)
