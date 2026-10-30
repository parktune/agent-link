import io
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from codex_bridge.connection_info import connection_info


class ConnectionInfoTests(unittest.TestCase):
    def interfaces(self):
        return SimpleNamespace(stdout=json.dumps([
            {'ifname': 'lo', 'addr_info': [{'local': '127.0.0.1'}]},
            {'ifname': 'eth0', 'addr_info': [{'local': '10.1.2.3'}]},
            {'ifname': 'docker0', 'addr_info': [{'local': '172.17.0.1'}]},
            {'ifname': 'eth1', 'addr_info': [{'local': '93.184.216.34'}]},
        ]))

    def test_both_address_types_and_no_false_reachability_claim(self):
        with patch('codex_bridge.connection_info.subprocess.run', return_value=self.interfaces()), \
             patch('codex_bridge.connection_info.urllib.request.urlopen', return_value=io.BytesIO(b'8.8.8.8')):
            info = connection_info('approved.example.com')
        self.assertEqual([p['address'] for p in info['private_addresses']], ['10.1.2.3'])
        self.assertEqual({p['source'] for p in info['public_addresses']},
                         {'local_interface', 'user_supplied', 'https_egress_lookup'})
        self.assertTrue(all(not p['verified_from_peer'] for p in info['private_addresses'] + info['public_addresses']))
        self.assertTrue(all(not p['inbound_mapping_verified'] for p in info['public_addresses']))

    def test_public_lookup_failure_preserves_private_and_approved_public(self):
        with patch('codex_bridge.connection_info.subprocess.run', return_value=self.interfaces()), \
             patch('codex_bridge.connection_info.urllib.request.urlopen', side_effect=TimeoutError()):
            info = connection_info('approved.example.com')
        self.assertTrue(info['private_addresses'])
        self.assertIn('approved.example.com', [p['address'] for p in info['public_addresses']])
        self.assertIn('lookup failed', info['public_lookup_error'])

    def test_nonpublic_lookup_response_is_rejected(self):
        with patch('codex_bridge.connection_info.subprocess.run', return_value=self.interfaces()), \
             patch('codex_bridge.connection_info.urllib.request.urlopen', return_value=io.BytesIO(b'127.0.0.1')):
            info = connection_info()
        self.assertNotIn('https_egress_lookup', [p['source'] for p in info['public_addresses']])
        self.assertIsNotNone(info['public_lookup_error'])
