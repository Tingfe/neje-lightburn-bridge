from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from controlled_grbl_pipeline import ControlledGrblPipeline
from test_grbl_pipeline import DeferredSession


class AbandonTests(unittest.IsolatedAsyncioTestCase):
    def make(self,capacity=173):
        self.rows=[];self.session=DeferredSession()
        async def visible(data):pass
        return ControlledGrblPipeline(self.session,visible,
            lambda event,**fields:self.rows.append(dict(event=event,**fields)),
            rx_budget=100,payload_capacity=capacity,deliver_lines=True)

    async def test_partial_line_urgent_status_and_response_tail_are_abandoned_without_write(self):
        pipeline=self.make(2)
        pipeline.enqueue(b'G1 X1\n');pipeline.enqueue(b'G1 X0\n')
        await pipeline.step()
        pipeline.realtime(b'?');pipeline.realtime(b'!')
        pipeline.output.pending.extend(b'o')
        before=list(self.session.writes)
        pipeline.abandon('transport_lost')
        event=next(r for r in self.rows if r['event']=='control_generation_abandoned')
        self.assertEqual(event['before']['pending'][0]['attempted_bytes'],2)
        self.assertEqual(event['before']['queued_sequences'],[2])
        self.assertEqual(event['before']['response_tail_hex'],'6f')
        self.assertEqual(event['before']['unsent_urgent_hex'],'21')
        self.assertFalse(any(event['after'].values()))
        self.assertFalse(event['remote_stop_inferred'])
        self.assertIsNone(pipeline.partial)
        self.assertEqual(self.session.writes,before)
        with self.assertRaises(RuntimeError):pipeline.enqueue(b'G1 X1\n')
        with self.assertRaises(RuntimeError):pipeline.realtime(b'\x18')
        with self.assertRaises(RuntimeError):await pipeline.step()
        self.assertEqual(self.session.writes,before)
        pipeline.abandon('again')
        self.assertEqual(sum(r['event']=='control_generation_abandoned' for r in self.rows),1)

    async def test_private_request_and_status_epochs_cannot_survive_close(self):
        pipeline=self.make()
        seq=pipeline.enqueue_internal(b'$G\n','modal')
        self.session.response=b'[GC:G1 G21 G90 G94 G54 M5 S0]\n'
        await pipeline.step()
        pipeline.realtime(b'?');await pipeline.step()
        self.assertEqual(pipeline.statuses,1)
        pipeline.abandon('eof')
        self.assertIsNone(pipeline.internal)
        self.assertFalse(pipeline.status_epochs)
        self.assertFalse(pipeline.status_deadlines)
        self.assertFalse(pipeline.internal_results)
        event=next(r for r in self.rows if r['event']=='control_generation_abandoned')
        self.assertEqual(event['before']['internal_sequence'],seq)
        self.assertEqual(event['before']['pending_statuses'],1)
        self.assertFalse(any(event['after'].values()))
