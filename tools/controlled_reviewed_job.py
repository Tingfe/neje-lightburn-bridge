"""Fixed-job staging while the controlled pipeline exclusively owns reception.

Unlike the older serial finish check, endpoint queries do not take over the
session: Pause/Stop can still preempt them. Modal read-back and restoration use
the same ledger with private responses throughout completion.
"""
import math
import re
import time

from motion_parity_probe import parse_status
from reviewed_job_file import MAX_ACTIVE_SECONDS
from reviewed_pipeline_job import ReviewedPipelineJob


def require_empty_startup(text):
    blocks = [line for line in text.splitlines() if line.startswith('$N')]
    if sorted(blocks) != ['$N0=', '$N1=']:
        raise RuntimeError('Reset review requires both startup blocks read back empty')


class ControlledReviewedJob(ReviewedPipelineJob):
    def __init__(self, *args, clock=time.monotonic, active_timeout=45,
                 position_bounds=((0,10),(400,400),(0,0),(0,0)), power_ceiling=0,
                 end_position=(0,400,0,0), **kwargs):
        super().__init__(*args, **kwargs)
        if not math.isfinite(active_timeout) or not 1<=active_timeout<=MAX_ACTIVE_SECONDS:
            raise ValueError(f'Active job timeout must be finite and within 1..{MAX_ACTIVE_SECONDS} seconds')
        self.active_timeout = active_timeout
        if len(position_bounds)!=4 or any(len(pair)!=2 or not all(math.isfinite(v) for v in pair)
                or pair[0]>pair[1] for pair in position_bounds):
            raise ValueError('Four finite ordered position bounds required')
        self.position_bounds = tuple(tuple(pair) for pair in position_bounds)
        self.power_ceiling = power_ceiling
        # Defaults to the historical X0/Y400 baseline; a general daily-use design
        # declares its own endpoint and that is what completion verifies.
        self.end_position = tuple(end_position)
        self.clock = clock
        self.aborted = False
        self.endpoint_query = False
        self.active_elapsed = 0.
        self.last_advance = None
        self.completion_stage = 'endpoint'
        self.completion_sequence = None
        self.modal_before_restore = None

    def cancel(self):
        self.log('controlled_job_staging_discarded', staged_bytes=self.staged_bytes,
                 staged_lines=len(self.staged), submitted=self.submitted,
                 endpoint_unknown=bool(self.submitted and not self.completed), no_replay=True)
        self.aborted = True
        self.staged.clear()
        self.statuses.clear()
        self.staged_bytes = 0

    def enqueue(self, kind, wire):
        if self.aborted:
            self.log('controlled_job_input_discarded', hex=wire.hex(), no_replay=True)
            return
        super().enqueue(kind, wire)

    def check_status(self, report):
        state, position = parse_status(report, power_ceiling=self.power_ceiling)
        fields = dict(field.split(':', 1) for field in report.strip('<>').split('|')[1:]
                      if ':' in field)
        feed = float(fields['FS'].split(',')[0])
        if not math.isfinite(feed) or feed < 0 or (state in ('Idle', 'Hold:0', 'Alarm') and feed != 0):
            raise RuntimeError('Invalid/nonzero reported speed at controlled rest')
        if self.pipeline.cancelling and self.pipeline.phase in ('cancel_wait_status', 'cancelled'):
            # A real reset banner invalidated the old coordinate generation.
            # Observe finite MPos/zero power, but never trust it for recovery.
            if state not in ('Idle', 'Alarm'):
                raise RuntimeError('Reset has no confirmed stationary state')
            self.log('controlled_reset_status_untrusted', report=report,
                     position_trusted=False, no_replay=True)
            return
        allowed = ('Idle', 'Run', 'Hold:0', 'Hold:1')
        if self.pipeline.cancelling:
            allowed += ('Alarm',)
        if state not in allowed or any(not low-.001<=value<=high+.001
                for value,(low,high) in zip(position,self.position_bounds)):
            raise RuntimeError('Controlled job state/power/position outside review')
        if not self.submitted:
            return
        if self.endpoint_query and self.pipeline.ordinary_allowed and state == 'Idle':
            if any(abs(a-b)>.001 for a,b in zip(position,self.end_position)):
                raise RuntimeError('Normal completion endpoint differs from the reviewed endpoint')
            self.endpoint_query = False
            if self.completion_stage == 'endpoint':
                self.completion_stage = 'modal'
                self.log('controlled_job_endpoint_verified', commands=self.submitted,
                         position=position, modal_verified=False, rapid_mode_restored=False,
                         physical_displacement_independently_measured=False, no_replay=True)
            elif self.completion_stage == 'restored_status':
                self.completed = True
                self.completion_stage = 'complete'
                self.log('controlled_job_completed', commands=self.submitted, position=position,
                         modal_before_restore=self.modal_before_restore,
                         modal_verified=True, rapid_mode_restored=True,
                         physical_displacement_independently_measured=False, no_replay=True)

    @staticmethod
    def require_modal(text, *, rapid=False):
        reports = re.findall(r'^\[GC:(.*)\]\r?$', text, re.MULTILINE)
        required = {'G21', 'G90', 'G94', 'G54', 'M5', 'S0'}
        if rapid:
            required.add('G0')
        if len(reports) != 1 or not required <= set(reports[0].split()):
            raise RuntimeError('Completion modal differs from zero-power review')

    def advance_completion(self):
        if self.pipeline.internal_results:
            result = self.pipeline.internal_results.popleft()
            if result['sequence'] != self.completion_sequence:
                raise RuntimeError('Internal completion response belongs to another sequence')
            self.completion_sequence = None
            text = bytes.fromhex(result['hex']).decode('ascii')
            if self.completion_stage == 'modal':
                self.require_modal(text)
                self.modal_before_restore = text
                self.completion_stage = 'restore'
            elif self.completion_stage == 'restore':
                self.completion_stage = 'restored_modal'
            elif self.completion_stage == 'restored_modal':
                self.require_modal(text, rapid=True)
                self.completion_stage = 'restored_status'
            else:
                raise RuntimeError('Unexpected internal completion stage')
        if not self.pipeline.drained or self.completion_sequence is not None:
            return
        if self.completion_stage in ('endpoint', 'restored_status'):
            self.endpoint_query = True
            self.pipeline.realtime(b'?')
        elif self.completion_stage in ('modal', 'restore', 'restored_modal'):
            wire = b'G0\n' if self.completion_stage == 'restore' else b'$G\n'
            self.completion_sequence = self.pipeline.enqueue_internal(wire, self.completion_stage)

    async def advance(self):
        now = self.clock()
        if not self.can_write() or self.aborted or not self.pipeline.ordinary_allowed:
            self.last_advance = None
            return
        if self.submitted and not self.completed:
            if self.last_advance is not None:
                self.active_elapsed += now - self.last_advance
            self.last_advance = now
            if self.active_elapsed > self.active_timeout:
                raise TimeoutError('Controlled job endpoint unconfirmed; no return/replay')
        if self.submitted == self.job_length and not self.completed:
            self.advance_completion()
            return
        # prepare() is completed before TCP ingress becomes ready. There is no
        # separate request loop that can block urgent controls during a job.
        if not self.prepared:
            raise RuntimeError('Controlled profile requires preflight before ingress')
        await super().advance()
