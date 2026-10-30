"""Unverified address candidates for exchanging pairing information, never a relay."""
import ipaddress
import json
import os
import pwd
import socket
import subprocess
import sys
import urllib.request

PUBLIC_IP_URL = 'https://api.ipify.org'


def connection_info(public_host=None):
    private = []
    interface_public = []
    local_error = None
    try:
        result = subprocess.run(['ip', '-json', '-4', 'address', 'show', 'up'],
                                capture_output=True, text=True, timeout=3, check=True)
        for interface in json.loads(result.stdout):
            label = interface['ifname']
            if label == 'lo' or label.startswith(('docker', 'br-', 'veth', 'virbr')):
                continue
            for item in interface.get('addr_info', []):
                address = ipaddress.ip_address(item.get('local', ''))
                if address.version == 4 and address.is_private and not (address.is_loopback or address.is_link_local or address.is_unspecified):
                    private.append({'address': str(address), 'interface': label, 'verified_from_peer': False})
                elif address.version == 4 and address.is_global:
                    interface_public.append({'address': str(address), 'interface': label, 'source': 'local_interface', 'verified_from_peer': False, 'inbound_mapping_verified': False})
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        local_error = 'Could not enumerate LAN interfaces; provide an approved private address.'

    public = interface_public
    if public_host:
        # An administrator-provided inbound endpoint may differ from the outbound NAT address.
        from .security_store import name
        public.append({'address': name(public_host), 'source': 'user_supplied',
                       'verified_from_peer': False, 'inbound_mapping_verified': False})
    lookup_error = None
    try:
        with urllib.request.urlopen(PUBLIC_IP_URL, timeout=5) as response:
            address = ipaddress.ip_address(response.read(128).decode('ascii').strip())
        if address.version != 4 or not address.is_global:
            raise ValueError('Not a global IPv4 address')
        public.append({'address': str(address), 'source': 'https_egress_lookup',
                       'verified_from_peer': False, 'inbound_mapping_verified': False})
    except (OSError, ValueError, UnicodeError):
        lookup_error = 'Public IP lookup failed; ask for an approved public SSH hostname/IP. Do not invent an address.'
    return {'private_addresses': private, 'private_lookup_error': local_error,
            'public_addresses': public, 'public_lookup_error': lookup_error,
            'public_lookup_service': PUBLIC_IP_URL,
            'hostname_candidate': socket.getfqdn(),
            'ssh_user_candidate': pwd.getpwuid(os.getuid()).pw_name,
            'ssh_port_assumption': 22,
            'remote_python': os.path.abspath(sys.executable),
            'policy': 'Try private and public candidates from B separately. A public egress IP may be a NAT gateway or HTTP proxy, not this host. Verify the intended host key; never auto-trust a reachable endpoint. Nondefault SSH users/ports require an approved SSH config alias.'}
