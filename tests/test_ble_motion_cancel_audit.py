"""Motion-cancel audit through loopback TCP and simulated NEJE framing."""
import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from check_motion_cancel_capture import audit
from reviewed_ten_mm import wire_lines
import test_controlled_tcp as fixtures


async def simulated_ble_cancel(**kwargs):
    case=fixtures.ControlTCPTests()
    class InitializedController(fixtures.Controller):
        async def write(self,wire):
            if wire==b'$I\n':
                self.writes.append(wire)
                return b'[VER:1.1f:simulated]\nok\n'
            return await super().write(wire)
    async def scenario(reader,writer,controller,clients,rows):
        await case.wait_event(rows,'pipeline_tcp_ready')
        writer.write(b'$I\n$#\nG0\n');await writer.drain()
        await case.wait_event(rows,'pipeline_terminal',sequence=3)
        writer.write(b''.join(wire_lines()));await writer.drain()
        await case.wait_event(rows,'pipeline_terminal',sequence=16)
        writer.write(b'?');await writer.drain()
        await case.wait_event(rows,'control_status_observed',state='Run')
        writer.write(b'\x18');await writer.drain()
        await asyncio.wait_for(reader.read(),3)
    controller,client,rows,bridge=await case.exercise(scenario,
        controller_class=InitializedController,**kwargs)
    rows.append(dict(event='pipeline_server_stopped',cleanup_unknown=bridge.cleanup_unknown,
                     failure_reason=bridge.failure_reason))
    for row in rows:row['simulated']=True
    return controller,client,rows,bridge


class BLECancelAuditTests(unittest.IsolatedAsyncioTestCase):
    def check(self,rows,*,simulated=True):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'capture.jsonl'
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            return audit(path,backend='ble',simulated=simulated)

    async def test_full_raw_ble_and_initialization_ledger_cancel_audit(self):
        controller,client,rows,bridge=await simulated_ble_cancel()
        result=self.check(rows)
        self.assertTrue(result['passed']);self.assertTrue(result['simulated'])
        self.assertEqual(result['backend'],'ble')
        self.assertEqual(result['acknowledged_job_lines'],13)
        self.assertEqual(result['acknowledged_ordinary_lines'],16) # Includes 3 init ACKs.
        self.assertEqual(result['reset_aborted_sequences'],[17])
        self.assertEqual(result['reported_run_position'],[2,400,0,0])
        self.assertEqual(result['final_state'],'Alarm')
        self.assertFalse(result['position_trusted']);self.assertFalse(result['job_completed'])
        self.assertEqual(controller.writes.count(b'\x18'),1)
        self.assertEqual(client.way,0);self.assertFalse(bridge.cleanup_unknown)
        with self.assertRaises(AssertionError):self.check(rows,simulated=False)

    async def test_raw_notify_credit_reset_and_cleanup_tampering_are_refused(self):
        _,_,rows,_=await simulated_ble_cancel()
        for event,field,value in (
            ('pipeline_tcp_delivered','hex','6f6b0a'),
            ('notify','hex','0000'),
            ('control_reset_banner','aborted_sequences',[]),
            ('pipeline_transmission_stopped','control_position_trusted',True),
            ('controlled_session_cancelled','status','<Idle|MPos:0,0,0,0|FS:0,0>'),
            ('cleanup_verified','way',2),
            ('pipeline_server_stopped','failure_reason','unresolved failure')):
            with self.subTest(event=event):
                altered=copy.deepcopy(rows)
                next(r for r in altered if r['event']==event)[field]=value
                with self.assertRaises((AssertionError,RuntimeError)):
                    self.check(altered)

    async def test_reset_accepted_then_ble_lost_cannot_pass_stop_or_cleanup(self):
        controller,_,rows,bridge=await simulated_ble_cancel(reset_disconnect=True)
        self.assertEqual(controller.writes.count(b'\x18'),1)
        self.assertTrue(bridge.cleanup_unknown)
        with self.assertRaises(AssertionError):self.check(rows)

    async def test_idle_post_reset_is_observation_not_completion(self):
        _,_,rows,_=await simulated_ble_cancel(reset_idle=True)
        result=self.check(rows)
        self.assertEqual(result['final_state'],'Idle')
        self.assertFalse(result['position_trusted'])
        self.assertFalse(result['job_completed'])
        self.assertTrue(result['no_auto_return'])


if __name__=='__main__':unittest.main()
