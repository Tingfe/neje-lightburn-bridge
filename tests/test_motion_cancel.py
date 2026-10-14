import asyncio
import copy
import datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from check_motion_cancel_capture import audit
from controlled_usb_probe import run_review
from test_controlled_tcp import Controller


async def simulated_cancel(*, changed_coordinates=False, reset_failure=False):
    rows,now=[],types.SimpleNamespace(value=0.)
    def log(event,**fields):
        stamp=datetime.datetime(2020,1,1,tzinfo=datetime.timezone.utc)+datetime.timedelta(seconds=now.value)
        rows.append(dict(utc=stamp.isoformat(),event=event,simulated=True,**fields))
    class Device(Controller):
        async def start(self):log('usb_open',is_open=True)
        async def close(self):self.closed=True;log('usb_closed',is_open=False)
        async def write(self,wire):
            log('usb_control_write_attempt' if wire in (b'$N\n',b'\x18',b'!',b'~')
                else 'usb_ten_mm_write_attempt',hex=wire.hex())
            if wire==b'\x18' and reset_failure:
                self.writes.append(wire)
                raise ConnectionError('Accepted reset, USB disappeared')
            if changed_coordinates and b'\x18' in self.writes and wire==b'?':
                self.writes.append(wire);reply=b'<Alarm|MPos:0,0,0,0|Bf:100,1500|FS:0,0>\n'
            else:reply=await super().write(wire)
            if reply:log('usb_ten_mm_rx',hex=reply.hex())
            return reply
    async def sleep(seconds):now.value+=max(.1,seconds)
    device=Device()
    error=None
    try:await run_review(device,log,scenario='motion_cancel',clock=lambda:now.value,sleep=sleep)
    except Exception as caught:error=caught
    return device,rows,error


class CancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_motion_reset_observes_alarm_without_success_return_restore_or_resume(self):
        device,rows,error=await simulated_cancel()
        self.assertIsNone(error)
        self.assertTrue(device.closed)
        self.assertEqual(device.writes.count(b'\x18'),1)
        self.assertNotIn(b'!',device.writes);self.assertNotIn(b'~',device.writes)
        self.assertEqual(device.writes.count(b'$G\n'),1) # Preflight only.
        self.assertNotIn(b'G0\n',device.writes)
        self.assertFalse(any(r['event']=='controlled_job_completed' for r in rows))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'capture.jsonl'
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            result=audit(path,simulated=True)
            self.assertEqual(result['acknowledged_job_lines'],13)
            self.assertEqual(result['reset_aborted_sequences'],[14])
            with self.assertRaises(AssertionError):audit(path)
            for event,field,value in (('usb_control_visible','hex',b'ok\n'.hex()),
                    ('control_reset_banner','aborted_sequences',[]),
                    ('usb_control_cancel_observed','position_trusted',True)):
                changed=copy.deepcopy(rows)
                next(r for r in changed if r['event']==event)[field]=value
                path.write_text(''.join(json.dumps(r)+'\n' for r in changed))
                with self.assertRaises(AssertionError):audit(path,simulated=True)

    async def test_post_banner_coordinate_change_is_observed_but_never_used_for_recovery(self):
        device,rows,error=await simulated_cancel(changed_coordinates=True)
        self.assertIsNone(error)
        result=next(r for r in rows if r['event']=='usb_control_cancel_observed')
        self.assertEqual(result['reported_position'],(0,0,0,0))
        self.assertFalse(result['position_trusted'])
        self.assertEqual(device.writes[-2:],[b'\x18',b'?'])

    async def test_accepted_reset_disconnect_stays_unknown_and_never_retries(self):
        device,rows,error=await simulated_cancel(reset_failure=True)
        self.assertIsInstance(error,ConnectionError)
        self.assertEqual(device.writes.count(b'\x18'),1)
        self.assertTrue(device.closed)
        self.assertFalse(any(r['event']=='usb_control_cancel_observed' for r in rows))
        stopped=next(r for r in rows if r['event']=='usb_control_transmission_stopped')
        self.assertEqual(stopped['pending_lines'],1)
        self.assertTrue(stopped['endpoint_unknown'])
        abandoned=next(r for r in rows if r['event']=='control_generation_abandoned')
        self.assertEqual(len(abandoned['before']['pending']),1)
        self.assertFalse(any(abandoned['after'].values()))

    async def test_motion_cancel_default_is_offline_and_execution_requires_specific_gate(self):
        script=Path(__file__).resolve().parents[1]/'tools/controlled_usb_probe.py'
        offline=subprocess.run([sys.executable,str(script),'--scenario','motion_cancel'],capture_output=True,text=True)
        self.assertEqual(offline.returncode,0)
        self.assertFalse(json.loads(offline.stdout)['hardware_executed'])
        with tempfile.TemporaryDirectory() as directory:
            log=Path(directory)/'not-created.jsonl'
            refused=subprocess.run([sys.executable,str(script),'--execute','--scenario','motion_cancel',
                '--allow-reviewed-ten-mm-job','--allow-reviewed-job-controls','--laser-power-isolated',
                '--log',str(log)],capture_output=True,text=True)
            self.assertEqual(refused.returncode,2)
            self.assertFalse(log.exists())
