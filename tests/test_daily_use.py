import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from daily_use import choose_profile, review_file, build_start_command

DESIGN = (b'M5\nS0\nG00 G17 G40 G21 G54\nG54\nG90\nM4\n'
          b'G0 X20Y300\nG1 X50Y300S300F600\nG2 X70Y320I0J20S300F600\n'
          b'G1 X20Y320S300F600\nG1 X20Y300S300F600\nM5\nS0\nM2\n')
ZERO = (b'M5\nS0\nG00 G17 G40 G21 G54\nG54\nG90\nM4\n'
        b'G1 X10S0F60\nG1 Y390\nG1 X0\nG1 Y400\nM5\nS0\nM2\n')


def write(raw):
    directory = tempfile.mkdtemp()
    path = Path(directory) / 'job.gc'
    path.write_bytes(raw)
    return path


class LauncherTests(unittest.TestCase):
    def test_zero_power_file_gets_the_strictest_profile(self):
        profile, why = choose_profile(write(ZERO))
        self.assertEqual(profile, 'xy-linear-zero')
        self.assertIn('strictest', why)

    def test_powered_design_needs_declared_ceilings_before_a_profile_is_chosen(self):
        profile, why = choose_profile(write(DESIGN))
        self.assertIsNone(profile)
        self.assertIn('Nonzero power is forbidden', why)
        self.assertIn('--power-ceiling', why)

    def test_powered_design_picks_the_general_profile_with_ceilings(self):
        profile, why = choose_profile(write(DESIGN), None, 300, 600)
        self.assertEqual(profile, 'xy-general-power')
        self.assertIn('declared ceilings', why)

    def test_explicit_profile_wins(self):
        self.assertEqual(choose_profile(write(ZERO), 'xy-job-zero')[0], 'xy-job-zero')

    def test_review_returns_the_digest_it_pins(self):
        path = write(DESIGN)
        digest, reviewed = review_file(path, 'xy-general-power', 300, 600)
        self.assertEqual(digest, hashlib.sha256(DESIGN).hexdigest())
        self.assertEqual(reviewed.summary()['power'], 300)

    def test_review_refuses_power_above_the_declared_ceiling(self):
        with self.assertRaises(ValueError):
            review_file(write(DESIGN), 'xy-general-power', 299, 600)

    def test_command_carries_every_required_flag_and_no_none_values(self):
        path = write(DESIGN)
        digest, reviewed = review_file(path, 'xy-general-power', 300, 600)
        command = build_start_command(path, 'xy-general-power', digest, reviewed,
                                      attestation='--laser-power-isolated', seconds=600)
        self.assertIn('--allow-powered-marking', command)
        self.assertIn('--allow-reviewed-arc-file', command)
        self.assertIn('--allow-reviewed-rapid-file', command)
        self.assertIn('--laser-power-isolated', command)
        for flag in ('--power-ceiling', '--feed-ceiling', '--file-profile', '--seconds'):
            value = command[command.index(flag) + 1]
            self.assertNotEqual(value, 'None')
        self.assertEqual(command[command.index('--feed-ceiling') + 1], '600')
        self.assertEqual(command[command.index('--expected-job-sha256') + 1], digest)

    def test_launcher_module_pulls_in_no_device_transport(self):
        for name in ('bleak', 'serial'):
            self.assertNotIn(name, sys.modules,
                             f'{name} must not be imported by the daily launcher')

    def test_doctor_reports_each_link_of_the_daily_chain(self):
        from daily_use import doctor
        checks = doctor()
        self.assertGreaterEqual(len(checks), 4)
        for check in checks:
            self.assertEqual(set(check), {'check', 'ok', 'detail'})
            self.assertIsInstance(check['ok'], bool)
            self.assertTrue(check['detail'])
        names = ' '.join(check['check'] for check in checks)
        self.assertIn('4002', names)
        self.assertIn('session', names)

    def test_doctor_explains_that_jog_is_translated_not_dropped(self):
        from daily_use import doctor
        text = ' '.join(c['detail'] for c in doctor())
        self.assertIn('$J=', text)
        self.assertIn('bounded relative move', text)
        self.assertNotIn('unsupported', text)


if __name__ == '__main__':
    unittest.main()


class TransparentEntryTests(unittest.TestCase):
    """The transparent mode needs its own daily entry: it removes the review gate."""

    def run_tool(self, *argv):
        import subprocess
        return subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]
                              / 'tools' / 'daily_use.py'), 'transparent', *argv],
                              capture_output=True, text=True)

    def test_dry_run_assembles_the_transparent_command(self):
        result = self.run_tool('--laser-isolated')
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout.strip().splitlines()[0])
        self.assertTrue(payload['usb_equivalent'])
        self.assertFalse(payload['reviewed_job_required'])
        self.assertFalse(payload['whitelist'])
        self.assertFalse(payload['run'])
        command = payload['command']
        for flag in ('--transparent', '--allow-transparent-bridge', '--execute',
                     '--allow-initialization', '--laser-power-isolated'):
            self.assertIn(flag, command)
        self.assertNotIn('--laser-output-authorized', command)
        self.assertIn('dry-run', result.stderr)

    def test_one_laser_attestation_is_required(self):
        for argv in ([], ['--laser-isolated', '--output-authorized']):
            result = self.run_tool(*argv)
            self.assertEqual(result.returncode, 2)
            self.assertIn('exactly one', result.stderr)
