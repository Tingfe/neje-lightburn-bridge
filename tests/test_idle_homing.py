import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from homing_recovery_probe import require_stopped


def report(state, x, y, feed=0, power=0):
    return f'<{state}|MPos:{x:.3f},{y:.3f},0.000,0.000|Bf:100,1500|FS:{feed},{power}|Pn:P>\n'


class PreconditionTests(unittest.TestCase):
    """The gate changed only in which stopped states it accepts."""

    def test_alarm_neighborhood_still_passes_unchanged(self):
        a = report('Alarm', 2.025, 400)
        state, position = require_stopped(a, a)
        self.assertEqual(state, 'Alarm')
        self.assertEqual(position[1], 400.0)

    def test_alarm_still_requires_zero_feed_and_the_recorded_neighborhood(self):
        moving = report('Alarm', 2.025, 400, feed=300)
        with self.assertRaises(ValueError):
            require_stopped(moving, moving)
        far = report('Alarm', 120, 400)
        with self.assertRaises(ValueError):
            require_stopped(far, far)

    def test_idle_with_unknown_reference_is_refused_without_the_new_authorization(self):
        reset = report('Idle', 0, 0)
        with self.assertRaises(ValueError):
            require_stopped(reset, reset)

    def test_idle_with_unknown_reference_is_accepted_only_when_authorized(self):
        reset = report('Idle', 0, 0)
        state, position = require_stopped(reset, reset, allow_idle=True)
        self.assertEqual(state, 'Idle')
        self.assertEqual(position, (0.0, 0.0, 0.0, 0.0))

    def test_idle_authorization_does_not_bypass_zero_feed_and_power(self):
        moving = report('Idle', 0, 0, feed=300)
        with self.assertRaises(ValueError):
            require_stopped(moving, moving, allow_idle=True)

    def test_changing_status_is_still_refused(self):
        with self.assertRaises(ValueError):
            require_stopped(report('Idle', 0, 0), report('Idle', 1, 0), allow_idle=True)

    def test_run_state_is_never_accepted(self):
        run = report('Run', 5, 400)
        for allow in (False, True):
            with self.assertRaises(ValueError):
                require_stopped(run, run, allow_idle=allow)




class HomingFormTests(unittest.TestCase):
    """The axis-selective forms returned ok without establishing a position on this
    firmware, so the plain whole-axis $H is the default and the old form is opt-in."""

    def offline_plan(self, *argv):
        import subprocess
        return subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]
                              / 'tools' / 'homing_recovery_probe.py'), *argv],
                              capture_output=True, text=True)

    def test_default_plan_sends_the_working_whole_axis_form(self):
        import json
        plan = json.loads(self.offline_plan('--allow-xy-homing').stdout)
        self.assertEqual(plan['homing'], ['$H'])

    def test_axis_selective_form_is_opt_in_only(self):
        import json
        plan = json.loads(self.offline_plan('--allow-xy-homing',
                                            '--axis-selective-home').stdout)
        self.assertEqual(plan['homing'], ['$HX', '$HY'])

    def test_homing_always_needs_the_movement_authorization(self):
        result = self.offline_plan('--execute', '--laser-power-isolated',
                                   '--allow-idle-homing', '--log', '/tmp/never.jsonl')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('movement', result.stderr)


if __name__ == '__main__':
    unittest.main()
