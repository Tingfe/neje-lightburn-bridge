"""Bounded ordinary-input staging and barriers for the fixed 10 mm review.

No device entry or authorization. The caller checks the export hash, supplies
real verified sessions, and stops calling advance after EOF/fault. Realtime
status requests remain on the pipeline's separate bounded path.
"""
import asyncio
from collections import deque

from motion_parity_probe import check_position
from reviewed_ten_mm import TEN_MM_JOB


class ReviewedPipelineJob:
    def __init__(self, pipeline, channel, log, can_write=lambda: True, *, job_commands=TEN_MM_JOB):
        self.pipeline, self.channel, self.log = pipeline, channel, log
        self.can_write = can_write
        self.staged, self.statuses = deque(), deque()
        self.staged_bytes = self.submitted = 0
        self.prepared = self.completed = False
        self.job_length = len(tuple(job_commands))
        if not self.job_length:
            raise ValueError('A nonempty reviewed command sequence is required')

    def enqueue(self, kind, wire):
        if self.staged_bytes + len(wire) > 16384:
            raise asyncio.QueueFull('Reviewed input staging is bounded; apply backpressure')
        if kind == 'status': self.statuses.append(wire)
        else: self.staged.append((kind, wire))
        self.staged_bytes += len(wire)

    def check_status(self, report):
        if self.submitted and not self.completed:
            check_position(report, 0, require_idle=False, max_x=10)

    async def advance(self):
        if not self.can_write():
            return
        while self.statuses:
            try:
                self.pipeline.realtime(self.statuses[0])
            except asyncio.QueueFull:
                break
            self.staged_bytes -= len(self.statuses.popleft())
        if self.submitted == self.job_length and not self.completed:
            if self.pipeline.drained:
                await self.channel.finish()
                self.completed = True
                self.log('pipeline_job_completed', commands=self.submitted,
                         physical_displacement_independently_measured=False, no_replay=True)
            return
        while self.staged and self.can_write():
            kind, wire = self.staged[0]
            if kind == 'job' and not self.prepared:
                if not self.pipeline.drained:
                    return
                await self.channel.prepare()
                self.prepared = True
                if not self.can_write():
                    return
            try:
                sequence = self.pipeline.enqueue(wire)
            except asyncio.QueueFull:
                return
            self.staged.popleft()
            self.staged_bytes -= len(wire)
            if kind == 'job':
                self.submitted += 1
                self.log('pipeline_job_submitted', index=self.submitted,
                         sequence=sequence, hex=wire.hex())
                if self.submitted == self.job_length:
                    return  # Post-job initialization waits for endpoint/modal checks.
