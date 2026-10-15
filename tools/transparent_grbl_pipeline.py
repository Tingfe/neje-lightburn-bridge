"""Native GRBL response semantics with the reviewed hold/reset state machine.

Ordinary controller errors release their own input credit and remain visible.
Jog/Home/Alarm status reports are device data, not a bridge-generated fault.
Reset ends the old job generation. A real banner and fresh Idle/Alarm response
permit new native commands on the existing link; coordinates remain untrusted.
"""
from controlled_grbl_pipeline import ControlledGrblPipeline
from grbl_pipeline import GrblPipeline
from passthrough_policy import EXTENDED_REALTIME
from collections import deque


class TransparentGrblPipeline(ControlledGrblPipeline):
    def __init__(self, *args, homing_timeout=120, **kwargs):
        if homing_timeout <= 0:
            raise ValueError('Positive native homing timeout required')
        super().__init__(*args, **kwargs)
        self.native_hold = False
        self.native_probe_at = 0
        self.native_probe_pending = False
        self.reset_observed = None
        self.homing_timeout = homing_timeout
        self.homing_deadline = None

    def _finish_homing(self, outcome):
        self.homing_deadline = None
        # A '?' sent during Home may have been deferred by the controller.
        # Require a fresh real report within the ordinary query window after
        # the actual $H terminal; do not invent or discard the outstanding query.
        if self.status_deadlines:
            deadline = self.clock() + self.transition_timeout
            for index in range(len(self.status_deadlines)):
                self.status_deadlines[index] = deadline
        self.log('pipeline_homing_terminal', outcome=outcome,
                 pending_statuses=self.statuses, no_synthetic_status=True)

    def enqueue(self, wire):
        if self.phase == 'cancel_wait_status':
            # New client initialization after the real banner waits behind the
            # private reset-status confirmation. Old work was already discarded.
            return GrblPipeline.enqueue(self, wire)
        return super().enqueue(wire)

    def _reserve_status(self):
        # GRBL stores EXEC_STATUS_REPORT as a flag. Several real '?' bytes can
        # legitimately produce one report, rather than one reply per query.
        if self.phase == 'running' and self.statuses:
            self.log('control_status_coalesced', epoch=self.epoch,
                     outstanding=self.statuses, query_forwarded=True)
            return
        super()._reserve_status()
        if self.homing_deadline is not None and self.status_deadlines:
            self.status_deadlines[-1] = max(self.status_deadlines[-1],
                                            self.homing_deadline)

    def _check_deadline(self):
        if self.homing_deadline is not None and self.clock() > self.homing_deadline:
            raise TimeoutError('Native homing exceeded its bounded terminal wait')
        return super()._check_deadline()

    def _validate_body(self, body):
        if any(byte in (0x18, 0x21, 0x3f, 0x7e) or byte >= 0x80 for byte in body):
            raise ValueError('Realtime bytes must be routed out of band')

    def realtime(self, wire):
        if wire in (b'!', b'~') and not self.cancelling:
            self.require_live()
            if len(self.controls) + self.statuses >= 8:
                import asyncio
                raise asyncio.QueueFull('Realtime queue is bounded')
            self.controls.append(wire)
            self.native_hold = wire == b'!'
            self.native_probe_at = self.clock()
            self.native_probe_pending = True
            self.log('control_native_request', hex=wire.hex(), confirmation_pending=True)
            return
        if len(wire) == 1 and wire[0] in EXTENDED_REALTIME:
            self.require_live()
            if self.cancelling:
                self.log('control_request_suppressed', hex=wire.hex(), reason='cancel_latched')
                return
            if len(self.controls) + self.statuses >= 8:
                import asyncio
                raise asyncio.QueueFull('Realtime queue is bounded')
            # Jog cancel / safety door must overtake queued status/override bytes.
            if wire == b'\x85':
                removed = [line for line in self.queued if line.wire.lstrip().upper().startswith(b'$J=')]
                self.queued = deque(line for line in self.queued if line not in removed)
                self.queued_bytes -= sum(len(line.wire) for line in removed)
                self.log('pipeline_jog_unsent_discarded',
                         lines=[dict(sequence=line.sequence, hex=line.wire.hex()) for line in removed],
                         no_synthetic_ack=True, no_replay=True)
            if wire in (b'\x85', b'\x84'):
                self.controls.appendleft(wire)
            else:
                self.controls.append(wire)
            return
        result = super().realtime(wire)
        if wire == b'\x18':
            self.homing_deadline = None
        return result

    def _batch(self):
        if self.urgent is not None:
            return super()._batch()
        self._check_deadline()
        if (self.native_hold or self.native_probe_pending) and not self.cancelling and not self.controls:
            if not self.statuses and self.clock() >= self.native_probe_at:
                self._reserve_status()
                self.native_probe_at = self.clock() + .2
                self.native_probe_pending = False
                return b'?'
            return b''
        if self.partial is not None and self.controls and self.controls[0] == b'\x85' and not self.cancelling:
            # Finish only the already-attempted line before cancel: otherwise a
            # trailing newline could launch that old jog after 0x85 cleared motion.
            line = self.partial
            payload = line.wire[line.attempted:line.attempted+self.capacity]
            line.attempted += len(payload)
            if line.attempted == len(line.wire): self.partial = None
            return payload
        payload = super()._batch()
        if self.homing_deadline is None and any(
                line.wire.strip().upper() == b'$H' and
                line.attempted == len(line.wire) for line in self.pending):
            self.homing_deadline = self.clock() + self.homing_timeout
            for index in range(len(self.status_deadlines)):
                self.status_deadlines[index] = max(self.status_deadlines[index],
                                                    self.homing_deadline)
            self.log('pipeline_homing_status_grace', seconds=self.homing_timeout,
                     pending_statuses=self.statuses, no_synthetic_status=True)
        return payload

    def _response_line(self, wire):
        terminal = wire.strip()
        if terminal.startswith(b'Grbl ') and self.phase == 'reset_wait':
            super()._response_line(wire)
            self.homing_deadline = None
            self.native_hold = self.native_probe_pending = False
            if self.reset_observed is not None: self.reset_observed()
            return
        if terminal.startswith(b'<') and self.phase == 'cancel_wait_status':
            super()._response_line(wire)
            if self.cancelled:
                self.log('pipeline_native_reset_completed', status=self.last_status,
                         position_trusted=False, requires_explicit_recovery=True,
                         connection_retained=True, no_replay=True)
                self._change('running', position_trusted=False, new_input_only=True)
            return
        if terminal.startswith(b'ALARM:') and not self.cancelling:
            if self.homing_deadline is not None:
                self._finish_homing('alarm')
            if self.partial is not None:
                raise RuntimeError('Alarm during a partial command; explicit recovery required')
            self.log('pipeline_alarm_ledger_invalidated',
                     pending_sequences=[line.sequence for line in self.pending],
                     discarded_lines=[dict(sequence=line.sequence, hex=line.wire.hex()) for line in self.queued],
                     position_trusted=False, no_synthetic_ack=True, no_replay=True)
            self.pending.clear()
            self.partial = None
            self.occupied = self.queued_bytes = 0
            self.queued.clear()
            return
        if (terminal.startswith(b'<') and terminal.endswith(b'>')
                and self.phase in ('running', 'cancel_requested', 'reset_wait')):
            matched = bool(self.status_epochs and self.status_deadlines)
            if matched:
                epoch = self.status_epochs.popleft()
                self.status_deadlines.popleft()
                GrblPipeline._response_line(self, wire)
            else:
                # A burst may produce one OR several real reports. Extra device
                # reports remain visible, without debts or synthetic query credit.
                if self.statuses: raise RuntimeError('Inconsistent native status ledger')
                epoch = None
            self.last_status = terminal.decode('ascii')
            state = self.last_status[1:].split('|', 1)[0]
            self.native_hold = state.startswith(('Hold:', 'Door:'))
            self.log('control_status_observed', epoch=epoch, current_epoch=self.epoch,
                     state=state, phase=self.phase, matched_query=matched)
            return
        if terminal.startswith(b'error:') and not self.cancelling:
            if not self.pending or self.pending[0].attempted != len(self.pending[0].wire):
                raise RuntimeError('Controller error without a fully attempted command')
            line = self.pending.popleft()
            self.occupied -= len(line.wire)
            self.log('pipeline_terminal', sequence=line.sequence, hex=wire.hex(),
                     command_hex=line.wire.hex(), occupied=self.occupied)
            if line.wire.strip().upper() == b'$H' and self.homing_deadline is not None:
                self._finish_homing('error')
            return
        home_terminal = (terminal == b'ok' and self.pending and
                         self.pending[0].wire.strip().upper() == b'$H')
        result = super()._response_line(wire)
        if home_terminal and self.homing_deadline is not None:
            self._finish_homing('ok')
        return result

    async def _consume(self, data):
        # Process the real reset boundary before publishing its banner. This lets
        # a fast client send fresh initialization immediately after receiving it.
        lines = self.output.feed(data)
        if lines: self.log('pipeline_response_lines', hex=b''.join(lines).hex())
        visible = self._public_output(lines, data)
        for wire in lines: self._response_line(wire)
        self._check_deadline()
        if visible: await self.deliver(visible)

    @property
    def ordinary_timeout_suspended(self):
        # Native homing, dwell and a full planner can legitimately defer an ok
        # for minutes. Status reports during an attempted $H use a separate,
        # bounded homing deadline; other query/NEJE transport deadlines and the
        # session wall limit remain enforced. No synthetic terminal is emitted.
        return True
