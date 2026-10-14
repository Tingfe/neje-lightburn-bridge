import sys
import unittest
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from controlled_reviewed_job import ControlledReviewedJob

GOOD = '<Idle|MPos:20.000,300.000,0.000,0.000|Bf:100,1500|FS:0,0|Pn:P|WCO:0,0,0,0>'
BASE = '<Idle|MPos:0.000,400.000,0.000,0.000|Bf:100,1500|FS:0,0|Pn:P|WCO:0,0,0,0>'
DESIGN_BOUNDS = ((0, 100), (250, 400), (0, 0), (0, 0))
BASE_BOUNDS = ((0, 10), (390, 400), (0, 0), (0, 0))


class _Pipe:
    cancelling = False
    phase = 'running'
    ordinary_allowed = True
    internal_results = deque()


def job(bounds, end):
    return ControlledReviewedJob(_Pipe(), None, lambda *a, **k: None,
                                 job_commands=(b'M5',), position_bounds=bounds,
                                 end_position=end)


class DeclaredEndpointTests(unittest.TestCase):
    """A general daily-use design ends where it ends, so completion must verify the
    endpoint the reviewed file declared instead of assuming X0/Y400."""

    def test_declared_endpoint_is_verified(self):
        runner = job(DESIGN_BOUNDS, (20, 300, 0, 0))
        runner.submitted = 1
        runner.endpoint_query = True
        runner.check_status(GOOD)
        self.assertFalse(runner.endpoint_query)

    def test_declared_endpoint_mismatch_is_refused(self):
        runner = job(DESIGN_BOUNDS, (20, 300, 0, 0))
        runner.submitted = 1
        runner.endpoint_query = True
        with self.assertRaises(RuntimeError):
            runner.check_status(BASE)

    def test_default_endpoint_still_requires_x0y400(self):
        runner = job(BASE_BOUNDS, (0, 400, 0, 0))
        runner.submitted = 1
        runner.endpoint_query = True
        runner.check_status(BASE)
        self.assertFalse(runner.endpoint_query)

    def test_default_endpoint_is_not_widened_by_the_new_field(self):
        runner = job(DESIGN_BOUNDS, (0, 400, 0, 0))
        runner.submitted = 1
        runner.endpoint_query = True
        with self.assertRaises(RuntimeError):
            runner.check_status(GOOD)

    def test_endpoint_default_is_the_historical_baseline(self):
        runner = job(BASE_BOUNDS, (0, 400, 0, 0))
        self.assertEqual(runner.end_position, (0, 400, 0, 0))


if __name__ == '__main__':
    unittest.main()
