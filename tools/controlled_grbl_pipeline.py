"""Caller-authorized GRBL hold/resume/reset over the existing byte ledger.

No hardware entry point or permission expansion. A caller must gate source
commands and authorize physical controls separately. Reset is terminal for this
instance: explicit recovery uses a new session, never replay of the old job.
"""
from collections import deque
import time

from grbl_pipeline import GrblPipeline


class ControlledGrblPipeline(GrblPipeline):
    def __init__(self, *args, clock=time.monotonic, transition_timeout=8, **kwargs):
        if transition_timeout <= 0:
            raise ValueError('Positive control transition timeout required')
        super().__init__(*args, **kwargs)
        self.clock, self.transition_timeout = clock, transition_timeout
        self.phase = 'running'
        self.urgent = None
        self.epoch = 0
        self.status_epochs = deque()
        self.status_deadlines = deque()
        self.deadline = None
        self.next_probe = None
        self.resume_after_hold = False
        self.last_status = None
        self.internal = None
        self.internal_results = deque()
        self.abandoned = False

    def abandon(self, reason):
        """Invalidate host-owned work only; never infer a remote stop or ACK."""
        if self.abandoned:
            return
        self.abandoned = True
        before = dict(queued_bytes=self.queued_bytes,
                      queued_sequences=[line.sequence for line in self.queued],
                      pending=[dict(sequence=line.sequence, hex=line.wire.hex(),
                                    attempted_bytes=line.attempted) for line in self.pending],
                      occupied_bytes=self.occupied, pending_statuses=self.statuses,
                      queued_controls=len(self.controls),
                      unsent_urgent_hex=self.urgent.hex() if self.urgent else None,
                      response_tail_hex=self.output.pending.hex(),
                      internal_sequence=self.internal['sequence'] if self.internal else None,
                      internal_results=len(self.internal_results))
        self.fault = self.fault or RuntimeError('Closed generation cannot be reused')
        self.queued.clear(); self.pending.clear(); self.controls.clear()
        self.partial = self.urgent = self.internal = None
        self.queued_bytes = self.occupied = self.statuses = 0
        self.status_epochs.clear(); self.status_deadlines.clear()
        self.internal_results.clear(); self.output.pending.clear()
        self.deadline = self.next_probe = None
        self.resume_after_hold = False
        self.log('control_generation_abandoned', reason=reason, before=before,
                 after=dict(queued_bytes=0, pending_lines=0, occupied_bytes=0,
                            pending_statuses=0, queued_controls=0, response_tail_bytes=0,
                            internal_results=0),
                 local_only=True, remote_stop_inferred=False, no_success_claimed=True,
                 no_replay=True)

    def enqueue_internal(self, wire, name):
        if wire not in (b'$G\n', b'G0\n') or not self.deliver_lines:
            raise ValueError('Only reviewed completion commands with whole-line output allowed')
        if not self.drained or self.internal is not None or self.internal_results:
            raise RuntimeError('Internal completion request requires a drained running pipeline')
        sequence = self.enqueue(wire)
        self.internal = dict(sequence=sequence, name=name, command_hex=wire.hex(), lines=[])
        self.log('pipeline_internal_reserved', sequence=sequence, name=name, hex=wire.hex())
        return sequence

    def _barrier(self, line):
        return (self.internal is not None and self.internal['sequence'] == line.sequence) or super()._barrier(line)

    def _public_output(self, lines, data):
        if self.internal is None:
            return super()._public_output(lines, data)
        pending, visible = deque(self.pending), []
        for wire in lines:
            terminal = wire.strip()
            owner = pending[0] if pending else None
            private = owner is not None and owner.sequence == self.internal['sequence']
            asynchronous = terminal.startswith((b'ALARM:', b'Grbl ', b'[MSG:', b'<'))
            if private and not asynchronous:
                self.internal['lines'].append(wire)
            # Internal ok/parameters must not become credits for LightBurn.
            # Errors and asynchronous messages remain real, visible output.
            if not private or asynchronous or terminal.startswith(b'error:'):
                visible.append(wire)
            if terminal == b'ok' or terminal.startswith(b'error:'):
                if owner is not None and owner.attempted == len(owner.wire):
                    pending.popleft()
            elif terminal.startswith(b'Grbl ') and self.phase == 'reset_wait':
                pending.clear()
        return b''.join(visible)

    def _internal_terminal(self, line):
        if self.internal is not None and self.internal['sequence'] == line.sequence:
            result, self.internal = self.internal, None
            result['hex'] = b''.join(result.pop('lines')).hex()
            self.log('pipeline_internal_response', **result, aborted=self.cancelling)
            if not self.cancelling:
                self.internal_results.append(result)

    @property
    def ordinary_allowed(self):
        return self.phase == 'running' and not self.fault

    @property
    def cancelled(self):
        return self.phase == 'cancelled'

    @property
    def cancelling(self):
        return self.phase in ('cancel_requested', 'reset_wait', 'cancel_wait_status', 'cancelled')

    @property
    def ordinary_timeout_suspended(self):
        # A healthy hold may be arbitrarily long. Transition deadlines and
        # outstanding status-query deadlines still need enforcement by callers.
        return self.phase != 'running'

    def _change(self, phase, **fields):
        previous, self.phase = self.phase, phase
        self.log('control_state', previous=previous, phase=phase, epoch=self.epoch, **fields)

    def enqueue(self, wire):
        if self.cancelling:
            raise RuntimeError('Cancelled generation cannot accept ordinary input')
        return super().enqueue(wire)

    def realtime(self, wire):
        self.require_live()
        if wire == b'?':
            if self.cancelling:
                self.log('control_status_suppressed', phase=self.phase)
                return
            return super().realtime(wire)
        if wire not in (b'!', b'~', b'\x18'):
            raise ValueError('Unsupported realtime byte')
        self.log('control_request', hex=wire.hex(), phase=self.phase)
        if self.cancelling:
            self.log('control_request_suppressed', hex=wire.hex(), reason='cancel_latched')
            return
        if wire == b'\x18':
            self.log('control_unsent_discarded', queued_bytes=self.queued_bytes,
                     sequences=[line.sequence for line in self.queued],
                     queued_statuses=len(self.controls),
                     partial_unsent_bytes=(len(self.partial.wire)-self.partial.attempted)
                         if self.partial else 0,
                     pending_execution_unknown=[line.sequence for line in self.pending],
                     no_replay=True)
            self.queued.clear()
            self.queued_bytes = 0
            self.controls.clear()
            self.resume_after_hold = False
            self.urgent = wire
            self.next_probe = self.deadline = None
            self._change('cancel_requested')
        elif wire == b'!':
            self.resume_after_hold = False
            if self.phase in ('hold_requested', 'holding'):
                return
            self.urgent = wire
            self.next_probe = self.deadline = None
            self._change('hold_requested')  # Freeze before the next await/write.
        elif self.phase == 'hold_requested':
            self.resume_after_hold = True  # Do not overtake deceleration.
        elif self.phase == 'holding':
            self.urgent = wire
            self.next_probe = self.deadline = None
            self._change('resume_requested')
        # Resume in Run/Idle or an already pending resume is a no-op.

    def _reserve_status(self):
        super()._reserve_status()
        self.status_epochs.append(self.epoch)
        self.status_deadlines.append(self.clock() + self.transition_timeout)
        self.log('control_status_reserved', epoch=self.epoch, outstanding=self.statuses)

    def _check_deadline(self):
        if self.deadline is not None and self.clock() > self.deadline:
            raise TimeoutError('Control transition unconfirmed; no replay or automatic recovery')
        if (not self.cancelling and self.status_deadlines and
                self.clock() > self.status_deadlines[0]):
            raise TimeoutError('Missing real status response during control/streaming')

    def _batch(self):
        if self.urgent is not None:
            wire, self.urgent = self.urgent, None
            self.epoch += 1
            self.deadline = self.clock() + self.transition_timeout
            self.next_probe = None if wire == b'\x18' else self.clock()
            if wire == b'\x18':
                self._change('reset_wait')
            self.log('control_write_reserved', hex=wire.hex(), epoch=self.epoch)
            return wire
        self._check_deadline()
        if self.cancelling and self.phase != 'cancel_wait_status':
            return b''
        if self.phase != 'running':
            # Existing queries must retire in FIFO order. A fresh query after
            # the control byte provides evidence; stale reports cannot do so.
            if (self.next_probe is not None and self.clock() >= self.next_probe
                    and not self.statuses):
                self._reserve_status()
                self.next_probe = self.clock() + .2
                self.log('control_probe_attempt', epoch=self.epoch)
                return b'?'
            if self.controls:
                return super()._batch()
            return b''
        return super()._batch()

    def _response_line(self, wire):
        terminal = wire.strip()
        if terminal.startswith(b'Grbl ') and self.phase == 'reset_wait':
            self.log('control_reset_banner', hex=wire.hex(),
                     aborted_sequences=[line.sequence for line in self.pending],
                     pending_execution_unknown=True, no_success_claimed=True)
            # A real reset invalidates the old RX/terminal ledger. It does not
            # acknowledge those commands or prove where the machine stopped.
            self.pending.clear()
            self.partial = None
            self.occupied = 0
            self.statuses = 0
            self.status_epochs.clear()
            self.status_deadlines.clear()
            if self.internal is not None:
                self.log('pipeline_internal_aborted', sequence=self.internal['sequence'],
                         name=self.internal['name'], no_replay=True)
            self.internal = None
            self.internal_results.clear()
            self.epoch += 1
            self.next_probe = self.clock()
            self._change('cancel_wait_status')
            return
        if terminal.startswith(b'ALARM:') and self.phase in ('reset_wait', 'cancel_wait_status'):
            self.log('control_reset_alarm', hex=wire.hex(), position_trusted=False)
            return
        if terminal.startswith(b'<') and terminal.endswith(b'>'):
            if not self.statuses or not self.status_epochs:
                raise RuntimeError('Status without an outstanding control/query epoch')
            epoch = self.status_epochs.popleft()
            self.status_deadlines.popleft()
            super()._response_line(wire)
            report = terminal.decode('ascii')
            state = report[1:].split('|', 1)[0].removesuffix('>')
            self.last_status = report
            self.log('control_status_observed', epoch=epoch, current_epoch=self.epoch,
                     state=state, phase=self.phase)
            if epoch != self.epoch or self.urgent is not None:
                return
            if self.phase == 'hold_requested' and state in ('Hold:0', 'Idle'):
                self.deadline = None
                self._change('holding', report=report)
                if self.resume_after_hold:
                    self.realtime(b'~')
            elif self.phase == 'resume_requested' and state in ('Run', 'Idle'):
                self.deadline = self.next_probe = None
                self._change('running', report=report)
            elif self.phase == 'cancel_wait_status' and state in ('Idle', 'Alarm'):
                self.deadline = self.next_probe = None
                self._change('cancelled', report=report, position_trusted=False,
                             requires_explicit_recovery=True)
            elif self.phase == 'running' and state not in ('Run', 'Idle'):
                raise RuntimeError('Unexpected hold without an accepted pause request')
            elif self.phase == 'holding' and state not in ('Hold:0', 'Idle'):
                raise RuntimeError('Controller left confirmed hold without accepted resume')
            elif state not in ('Run', 'Idle', 'Hold:0', 'Hold:1'):
                raise RuntimeError('Unexpected controller state during control transition')
            return
        if self.cancelling and (terminal == b'ok' or terminal.startswith(b'error:')):
            # Old commands can finish while reset is being requested. Record
            # their actual results, but do not confuse them with reset success.
            if not self.pending or self.pending[0].attempted != len(self.pending[0].wire):
                raise RuntimeError('Unattributed terminal during cancellation')
            line = self.pending.popleft()
            self.occupied -= len(line.wire)
            self.log('pipeline_terminal', sequence=line.sequence, hex=wire.hex(),
                     command_hex=line.wire.hex(), occupied=self.occupied)
            self._internal_terminal(line)
            return
        line = self.pending[0] if self.pending else None
        super()._response_line(wire)
        if terminal == b'ok' and line is not None:
            self._internal_terminal(line)

    async def _consume(self, data):
        await super()._consume(data)
        self._check_deadline()

    @property
    def drained(self):
        return self.ordinary_allowed and super().drained
