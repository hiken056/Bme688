import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]

# Run the real shell script with simulated system services and networking.
FAKE_COMMAND = r'''
import json, os, pathlib, shutil, sys
path = pathlib.Path(os.environ['AP_TEST_STATE'])
state = json.loads(path.read_text())
command = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
state['calls'].append([command, *args])
result = ''
code = 0
if command == 'hostnamectl':
    result = 'bme688-03'
elif command == 'id':
    result = 'nogroup'
elif command == 'install':
    positional = []
    mode = 0o755
    directory = '-d' in args
    index = 0
    while index < len(args):
        item = args[index]
        if item in ('-m', '-o', '-g'):
            if item == '-m':
                mode = int(args[index + 1], 8)
            index += 2
        elif item.startswith('-'):
            index += 1
        else:
            positional.append(pathlib.Path(item))
            index += 1
    for target in positional if directory else positional[-1:]:
        assert target.is_relative_to(path.parent), target
        if directory:
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(positional[0], target)
        target.chmod(mode)
elif command == 'journalctl':
    result = 'Simulated AP startup failure'
elif command == 'dnsmasq':
    code = 1 if state.get('configuration_failure') else 0
elif command == 'hostapd_cli':
    if args[-1] == 'status':
        result = 'state=ENABLED' if state.get('ready', True) else 'state=DISABLED'
    elif args[-1] == 'all_sta':
        result = 'flags=[AUTH][ASSOC][AUTHORIZED]' if state.get('authorized') else ''
    else:
        raise RuntimeError(args)
elif command == 'systemd-run':
    if '--unit=bme688-ap-rollback' in args and state.get('timer_failure'):
        code = 1
    elif '--unit=bme688-ap-activate' in args and state.get('activation_timer_failure'):
        code = 1
    else:
        state['timers'].append(args)
elif command == 'systemctl':
    action = args[0]
    if action == 'is-enabled':
        code = 0 if state.get('enabled') else 1
    elif action == 'is-active':
        code = 0 if args[-1] in state['services'] else 3
    elif action == 'start':
        state['active'] = '--'
        state['managed'] = False
        if state.get('activation_failure'):
            code = 1
        else:
            state['services'] = ['bme688-ap.service', 'bme688-ap-dns.service']
    elif action == 'stop':
        state['services'] = [name for name in state['services'] if name not in args[1:]]
    elif action == 'enable':
        state['enabled'] = True
    elif action == 'disable':
        state['enabled'] = False
    elif action != 'daemon-reload':
        raise RuntimeError(args)
elif command == 'nmcli':
    if args[:2] == ['--wait', '45']:
        args = args[2:]
    if args == ['radio', 'wifi']:
        result = 'enabled'
    elif args[:2] == ['-g', 'GENERAL.CON-UUID']:
        result = state['active']
    elif args[:2] == ['-g', 'WIFI-PROPERTIES.AP']:
        result = state.get('capability', 'yes')
    elif args[:2] == ['-g', 'connection.autoconnect']:
        result = state['profiles'][args[-1]]['autoconnect']
    elif args[:3] == ['device', 'set', 'wlan0']:
        state['managed'] = args[-1] == 'yes'
    elif args[:2] == ['connection', 'up']:
        if state.get('rollback_failure'):
            code = 1
        else:
            state['active'] = args[3]
    else:
        raise RuntimeError(args)
elif command not in ('flock', 'ip', 'iw', 'sleep', 'hostapd'):
    raise RuntimeError(command)
path.write_text(json.dumps(state))
if result:
    print(result)
sys.exit(code)
'''


class AccessPointSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state_file = self.root / 'fake-state.json'
        self.state_file.write_text(json.dumps({
            'active': 'old', 'managed': True, 'enabled': False, 'services': [],
            'profiles': {'old': {'name': 'home-wifi', 'autoconnect': 'yes'}},
            'calls': [], 'timers': [],
        }))
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ('nmcli', 'systemd-run', 'systemctl', 'hostnamectl', 'journalctl',
                     'flock', 'hostapd', 'hostapd_cli', 'dnsmasq', 'iw', 'ip',
                     'install', 'id', 'sleep'):
            path = self.bin / name
            path.write_text('#!' + sys.executable + '\n' + FAKE_COMMAND)
            path.chmod(0o755)
        self.script = self.root / 'setup_ap.sh'
        source = (PROJECT / 'setup_ap.sh').read_text()
        source = source.replace('if (( EUID != 0 )); then', 'if false; then')
        for original, name in (
            ('/run/bme688-ap-setup', 'transaction'),
            ('/run/lock/bme688-ap-setup.lock', 'lock'),
            ('/etc/bme688-ap', 'conf'),
            ('/etc/systemd/system', 'units'),
            ('/usr/local/lib/bme688-ap/setup.sh', 'installed/setup.sh'),
            ('/var/lib/bme688-ap', 'leases'),
        ):
            source = source.replace(original, str(self.root / name))
        (self.root / 'units').mkdir()
        self.script.write_text(source)
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'],
                        AP_TEST_STATE=str(self.state_file))

    def state(self):
        return json.loads(self.state_file.read_text())

    def set_state(self, **values):
        state = self.state()
        state.update(values)
        self.state_file.write_text(json.dumps(state))

    def run_script(self, mode=None, success=True):
        result = subprocess.run(['bash', str(self.script)] + ([mode] if mode else []),
                                env=self.env, capture_output=True, text=True, timeout=30)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_stage_preserves_network_and_arms_rollback_before_activation(self):
        self.run_script()
        state = self.state()
        self.assertEqual(state['active'], 'old')
        self.assertTrue(state['managed'])
        self.assertFalse(state['enabled'])
        self.assertEqual(list(state['profiles']), ['old'])
        self.assertIn('--unit=bme688-ap-rollback', state['timers'][0])
        self.assertIn('--unit=bme688-ap-activate', state['timers'][1])

    def test_generated_config_uses_only_plain_wpa2_psk_and_aes(self):
        self.run_script()
        config = (self.root / 'conf/hostapd.conf').read_text().splitlines()
        for line in ('ssid=bme688-03', 'wpa_passphrase=utcnbme688', 'wpa=2',
                     'wpa_key_mgmt=WPA-PSK', 'rsn_pairwise=CCMP', 'ieee80211w=0'):
            self.assertIn(line, config)
        self.assertFalse(any('SHA256' in line or 'SAE' in line or 'TKIP' in line for line in config))
        self.assertEqual((self.root / 'conf/hostapd.conf').stat().st_mode & 0o777, 0o600)
        dns = (self.root / 'conf/dnsmasq.conf').read_text()
        self.assertIn('dhcp-option=6,10.42.0.1', dns)
        self.assertIn('except-interface=lo', dns)
        self.assertIn('bind-dynamic', dns)

    def test_services_use_installed_worker_and_restart_on_failure(self):
        self.run_script()
        unit = (self.root / 'units/bme688-ap.service').read_text()
        self.assertIn(str(self.root / 'installed/setup.sh') + ' --prepare', unit)
        self.assertIn(str(self.root / 'installed/setup.sh') + ' --release', unit)
        self.assertNotIn('transaction/worker.sh', unit)
        self.assertIn('Restart=on-failure', unit)
        dns_unit = (self.root / 'units/bme688-ap-dns.service').read_text()
        self.assertIn('PartOf=bme688-ap.service', dns_unit)
        self.assertIn('After=bme688-ap.service', dns_unit)

    def test_prepare_releases_only_wlan0_from_networkmanager(self):
        self.run_script('--prepare')
        self.assertFalse(self.state()['managed'])
        calls = self.state()['calls']
        self.assertIn(['ip', '-4', 'address', 'flush', 'dev', 'wlan0'], calls)
        self.assertNotIn('systemctl', [call[0] for call in calls])

    def test_activation_failure_restores_networkmanager_and_previous_network(self):
        self.run_script()
        self.set_state(activation_failure=True)
        self.run_script('--activate', success=False)
        self.assertEqual(self.state()['active'], 'old')
        self.assertTrue(self.state()['managed'])
        self.assertFalse(self.state()['services'])
        self.assertTrue((self.root / 'transaction/activation.log').exists())

    def test_unconfirmed_ap_rolls_back(self):
        self.run_script()
        self.run_script('--activate')
        self.run_script('--rollback')
        self.assertEqual(self.state()['active'], 'old')
        self.assertTrue(self.state()['managed'])
        self.assertFalse(self.state()['enabled'])
        self.assertFalse(self.state()['services'])

    def test_confirm_requires_successfully_authenticated_client(self):
        self.run_script()
        self.run_script('--activate')
        self.run_script('--confirm', success=False)
        self.assertFalse(self.state()['enabled'])
        self.assertTrue((self.root / 'transaction/pending').exists())
        self.set_state(authorized=True)
        self.run_script('--confirm')
        self.assertTrue(self.state()['enabled'])
        self.run_script('--rollback')
        self.assertFalse(self.state()['managed'])
        self.assertEqual(list(self.state()['profiles']), ['old'])

    def test_cannot_confirm_without_dhcp(self):
        self.run_script()
        self.run_script('--activate')
        self.set_state(services=['bme688-ap.service'], authorized=True)
        self.run_script('--confirm', success=False)
        self.assertFalse(self.state()['enabled'])

    def test_cannot_switch_if_rollback_timer_fails(self):
        self.set_state(timer_failure=True)
        self.run_script(success=False)
        self.assertEqual(self.state()['active'], 'old')
        self.assertFalse(self.state()['timers'])
        self.assertTrue(self.state()['managed'])

    def test_invalid_dns_configuration_does_not_disconnect(self):
        self.set_state(configuration_failure=True)
        self.run_script(success=False)
        self.assertEqual(self.state()['active'], 'old')
        self.assertFalse(self.state()['timers'])

    def test_failed_activation_timer_leaves_rollback_armed(self):
        self.set_state(activation_timer_failure=True)
        self.run_script(success=False)
        self.assertEqual(len(self.state()['timers']), 1)
        self.run_script('--rollback')
        self.assertEqual(self.state()['active'], 'old')

    def test_failed_rollback_can_retry(self):
        self.run_script()
        self.run_script('--activate')
        self.set_state(rollback_failure=True)
        self.run_script('--rollback', success=False)
        self.assertTrue((self.root / 'transaction/pending').exists())
        self.set_state(rollback_failure=False)
        self.run_script('--rollback')
        self.assertEqual(self.state()['active'], 'old')

    def test_pending_test_cannot_be_replaced(self):
        self.run_script()
        self.run_script(success=False)
        self.assertEqual(len(self.state()['timers']), 2)

    def test_no_working_backup_refuses_setup(self):
        self.set_state(active='--')
        self.run_script(success=False)
        self.assertFalse(self.state()['timers'])
        self.assertFalse((self.root / 'conf').exists())

    def test_unsupported_radio_refuses_setup(self):
        self.set_state(capability='no')
        self.run_script(success=False)
        self.assertFalse(self.state()['timers'])

    def test_existing_hostapd_service_is_not_overwritten(self):
        self.set_state(services=['hostapd.service'])
        self.run_script(success=False)
        self.assertFalse((self.root / 'conf').exists())

    def test_confirmed_ap_is_not_overwritten(self):
        self.set_state(enabled=True)
        self.run_script(success=False)
        self.assertFalse((self.root / 'conf').exists())

    def test_reboot_before_confirmation_does_not_enable_new_ap(self):
        self.run_script()
        self.run_script('--activate')
        self.assertFalse(self.state()['enabled'])
        # /run and runtime device ownership are reset by a reboot.
        shutil.rmtree(self.root / 'transaction')
        self.set_state(active='old', managed=True, services=[], timers=[])
        self.run_script()
        self.assertEqual(self.state()['active'], 'old')


if __name__ == '__main__':
    unittest.main()
