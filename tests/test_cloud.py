import json
import unittest

from codex_bridge.cloud import probe_host
from codex_bridge.rpc import RpcError


class FakeHost:
    def __init__(self, fail=None, malformed=False, cleanup_fail=False, relay_status="connected"):
        self.relay_status = relay_status
        self.calls = []
        self.fail = fail
        self.enabled = False
        self.malformed = malformed
        self.cleanup_fail = cleanup_fail

    async def call(self, method, params):
        self.calls.append((method, params))
        if method == self.fail:
            raise RpcError('HTTP 403 access denied secret-token-should-not-leak')
        if method == 'remoteControl/enable':
            self.enabled = True
            return {'status': 'connecting'}
        if method == 'remoteControl/status/read':
            return {'status': self.relay_status if self.enabled else 'disabled'}
        if method == 'remoteControl/pairing/start':
            return {'manualPairingCode': 'PRIVATE-CODE', 'pairingCode': 'secret-artifact'}
        if method == 'remoteControl/pairing/status':
            return {} if self.malformed else {'claimed': False}
        if method == 'remoteControl/disable':
            if self.cleanup_fail:
                raise RpcError('cleanup failed')
            self.enabled = False
            return {'status': 'disabled'}
        raise AssertionError(method)


class CloudTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_success_does_not_claim_a2a_or_leak_secrets(self):
        server = FakeHost()
        events = []
        report = await probe_host(server, events.append)
        self.assertEqual(report['host_probe'], 'passed')
        self.assertEqual(report['cleanup'], 'passed')
        self.assertFalse(report['a2a_verified'])
        self.assertFalse(report['pairing_claimed'])
        output = json.dumps([events, report])
        self.assertNotIn('PRIVATE-CODE', output)
        self.assertNotIn('secret-artifact', output)
        mutations = [(m, p) for m, p in server.calls if m.endswith(('/enable', '/disable'))]
        self.assertEqual(mutations, [
            ('remoteControl/enable', {'ephemeral': True}),
            ('remoteControl/disable', {'ephemeral': True}),
        ])

    async def test_enable_failure_still_attempts_cleanup_and_redacts_error(self):
        server = FakeHost(fail='remoteControl/enable')
        report = await probe_host(server, lambda _: None)
        self.assertEqual(report['failed_stage'], 'enable_ephemeral')
        self.assertEqual(report['error_category'], 'access_denied')
        self.assertEqual(server.calls[-1][0], 'remoteControl/disable')
        self.assertNotIn('secret-token', json.dumps(report))

    async def test_pairing_failure_cleans_up(self):
        report = await probe_host(FakeHost(fail='remoteControl/pairing/start'), lambda _: None)
        self.assertEqual(report['host_probe'], 'failed')
        self.assertEqual(report['cleanup'], 'passed')
        self.assertFalse(report['pairing_code_issued'])

    async def test_bad_status_is_not_claimed_or_success(self):
        report = await probe_host(FakeHost(malformed=True), lambda _: None)
        self.assertEqual(report['host_probe'], 'failed')
        self.assertEqual(report['failed_stage'], 'pairing_status')

    async def test_cleanup_failure_is_reported(self):
        report = await probe_host(FakeHost(cleanup_fail=True), lambda _: None)
        self.assertEqual(report['cleanup'], 'failed')

    async def test_initial_status_failure_does_not_change_settings(self):
        server = FakeHost(fail='remoteControl/status/read')
        report = await probe_host(server, lambda _: None)
        self.assertEqual(report['cleanup'], 'not_needed')
        self.assertEqual(len(server.calls), 1)

    async def test_pairing_success_with_relay_error_is_not_host_success(self):
        report = await probe_host(FakeHost(relay_status="errored"), lambda _: None)
        self.assertTrue(report['pairing_code_issued'])
        self.assertEqual(report['host_probe'], 'failed')
        self.assertEqual(report['failed_stage'], 'relay_connect')
        self.assertEqual(report['relay_status'], 'errored')
        self.assertEqual(report['cleanup'], 'passed')

    async def test_relay_timeout_still_disables_ephemeral_control(self):
        report = await probe_host(FakeHost(relay_status="connecting"), lambda _: None, timeout=0.001)
        self.assertEqual(report['host_probe'], 'failed')
        self.assertEqual(report['error_category'], 'timeout')
        self.assertEqual(report['cleanup'], 'passed')


class DiagnosticTests(unittest.TestCase):
    def test_only_structured_relay_http_status_is_exposed(self):
        from codex_bridge.cloud import transport_diagnostic
        lines = [json.dumps({'fields': {
            'message': 'failed to connect to app-server remote control websocket',
            'error': 'HTTP error: 409 Conflict PRIVATE-CODE secret-token',
            'environment_id': 'private-host-id',
        }})]
        result = transport_diagnostic(lines)
        self.assertEqual(result, {'http_status': 409, 'category': 'conflict'})
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(transport_diagnostic(['raw secret logs']), {})
