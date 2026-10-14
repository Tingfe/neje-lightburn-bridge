# evidence/ is deliberately not part of the repository; tests that read real captures
# skip themselves explicitly rather than failing or pretending to pass.
import os as _os, unittest as _unittest
if not _os.path.isdir(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'evidence')):
    raise _unittest.SkipTest('evidence/ is not part of this repository')
"""USB file parity candidates and faulted file/TCP sessions; all simulated."""
import asyncio
import copy
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from check_controlled_capture import audit
from controlled_usb_probe import ControlledUSBSession, run_review
from reviewed_job_file import load
import test_controlled_tcp as fixtures
from test_reviewed_file_tcp import raw_file, review_bytes


async def simulated_usb_file(profile, *, fail_job=False, controller_class=None):
    rows=[];now=types.SimpleNamespace(value=0.)
    def log(event, **fields):
        stamp=datetime.datetime(2020,1,1,tzinfo=datetime.timezone.utc)+datetime.timedelta(seconds=now.value)
        rows.append(dict(utc=stamp.isoformat(),event=event,simulated=True,**fields))
    class Device(controller_class or fixtures.Controller):
        async def start(self):log('usb_opened',is_open=True)
        async def close(self):self.closed=True;log('usb_closed',is_open=False)
        async def write(self,wire):
            log('usb_control_write_attempt' if wire in (b'$N\n',b'!',b'~',b'\x18')
                else 'usb_ten_mm_write_attempt',hex=wire.hex())
            reply=await super().write(wire)
            if fail_job and self.job_count:
                raise ConnectionError('Job accepted then USB disconnected before response delivery')
            if reply:log('usb_ten_mm_rx',hex=reply.hex())
            return reply
    async def sleep(seconds):
        now.value+=.001
        await asyncio.sleep(0)
    device=Device(defer_m2=False,job_length=len(profile.commands))
    error=None
    try:
        await run_review(device,log,scenario='complete',reviewed_file=profile,
                         clock=lambda:now.value,sleep=sleep)
    except Exception as caught:
        error=caught;log('usb_control_error',message=repr(caught))
    return device,rows,error


async def simulated_file_disconnect(profile, *, prefix=b''):
    case=fixtures.ControlTCPTests()
    async def scenario(reader,writer,controller,clients,rows):
        await case.wait_event(rows,'pipeline_tcp_ready')
        writer.write(b''.join(profile.wires));await writer.drain()
        await asyncio.wait_for(reader.read(),3)
    controller,client,rows,bridge=await case.exercise(scenario,reviewed_file=profile,
        disconnect_on_job=True,unverified_prefix=prefix)
    rows.append(dict(event='pipeline_server_stopped',cleanup_unknown=bridge.cleanup_unknown,
                     failure_reason=bridge.failure_reason))
    for row in rows:row['simulated']=True
    return controller,client,rows,bridge


class USBFileTests(unittest.IsolatedAsyncioTestCase):
    async def test_usb_file_constructor_needs_no_historical_fixed_job_and_opens_no_port(self):
        profile=review_bytes(raw_file(1))
        with patch('check_ten_mm_host.wire_lines',side_effect=FileNotFoundError('historical fixed export absent')) as fixed:
            session=ControlledUSBSession(lambda *a,**k:None,allow_job=True,
                                         allow_controls=True,reviewed_file=profile)
            self.assertEqual(session.allowed_job,b''.join(profile.wires))
            self.assertIsNone(session.backend.port)
            fixed.assert_not_called()
            with self.assertRaises(ValueError):
                ControlledUSBSession(print,reviewed_file=profile)
            with self.assertRaises(FileNotFoundError):
                ControlledUSBSession(print,allow_job=True)

    async def test_variable_usb_file_has_same_bytes_terminals_private_finish_as_ble_reference(self):
        root=Path(__file__).resolve().parents[1]
        for stem in ('file-166','file-8006'):
            with self.subTest(stem=stem):
                profile=load(root/'evidence/reviewed-file-runtime'/(stem+'-simulated.gc'))
                device,rows,error=await simulated_usb_file(profile)
                self.assertIsNone(error)
                self.assertTrue(device.closed)
                self.assertEqual(device.job_count,len(profile.commands))
                self.assertNotIn(b'!',device.writes);self.assertNotIn(b'~',device.writes)
                self.assertNotIn(b'\x18',device.writes)
                with tempfile.TemporaryDirectory() as directory:
                    path=Path(directory)/'usb.jsonl'
                    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
                    result=audit(path,backend='usb',simulated=True,reviewed_file=profile,expected_controls=())
                    self.assertEqual(result['complete_job_commands'],len(profile.commands))
                    altered=copy.deepcopy(rows)
                    next(r for r in altered if r['event']=='usb_control_ready')['reviewed_source_sha256']='0'*64
                    path.write_text(''.join(json.dumps(r)+'\n' for r in altered))
                    with self.assertRaises(AssertionError):
                        audit(path,backend='usb',simulated=True,reviewed_file=profile,expected_controls=())
                ble=[json.loads(line) for line in (root/'evidence/reviewed-file-runtime'/
                     (stem+'-simulated.jsonl')).read_text().splitlines()]
                submitted=lambda data:[r['hex'] for r in data if r['event']=='pipeline_job_submitted']
                self.assertEqual(submitted(rows),submitted(ble))
                terminals=lambda data:[r['command_hex'] for r in data if r['event']=='pipeline_terminal']
                self.assertEqual(terminals(rows),terminals(ble))

    async def test_usb_file_transport_failure_discards_remaining_source_without_return_or_reset(self):
        profile=review_bytes(raw_file(1000))
        device,rows,error=await simulated_usb_file(profile,fail_job=True)
        self.assertIsInstance(error,ConnectionError)
        self.assertTrue(device.closed);self.assertLess(device.job_count,len(profile.commands))
        self.assertNotIn(b'G0\n',device.writes);self.assertNotIn(b'\x18',device.writes)
        self.assertFalse(any(r['event'] in ('controlled_job_completed','usb_file_review_passed') for r in rows))
        abandoned=next(r for r in rows if r['event']=='control_generation_abandoned')
        self.assertTrue(abandoned['before']['pending']);self.assertFalse(any(abandoned['after'].values()))
        stop=next(r for r in rows if r['event']=='usb_control_transmission_stopped')
        self.assertTrue(stop['endpoint_unknown']);self.assertGreater(stop['staged_bytes'],0)

    async def test_usb_file_raw_policy_exact_source_gate_partial_write_and_no_physical_controls(self):
        profile=review_bytes(raw_file(1));writes=[]
        with self.assertRaises(ValueError):ControlledUSBSession(print,reviewed_file=profile)
        session=ControlledUSBSession(lambda *a,**k:None,allow_job=True,allow_controls=True,reviewed_file=profile)
        def write(wire):writes.append(wire);return len(wire)
        session.backend.port=types.SimpleNamespace(write=write,read=lambda n:b'')
        for wire in (b'M3 S1000\n',b'G1 X9\n',b'!',b'~',b'\x18'):
            with self.assertRaises(ValueError):await session.write(wire)
        await session.write(b'$N\n')
        raw=b''.join(profile.wires)
        await session.write(raw[:5]);await session.write(raw[5:]);await session.write(b'G0\n')
        self.assertEqual(session.attempted,len(raw))
        broken=ControlledUSBSession(lambda *a,**k:None,allow_job=True,allow_controls=True,reviewed_file=profile)
        broken.backend.port=types.SimpleNamespace(write=lambda wire:len(wire)-1,read=lambda n:b'')
        with self.assertRaises(RuntimeError):await broken.write(profile.wires[0])
        with self.assertRaises(RuntimeError):await broken.write(profile.wires[0])
        self.assertTrue(broken.failed)
        with self.assertRaises(ValueError):await run_review(session,print,reviewed_file=profile,scenario='motion_cancel')

    async def test_ble_file_disconnect_retains_uncertain_counts_and_clears_host_only(self):
        profile=review_bytes(raw_file(80))
        for prefix in (b'',b'o'):
            with self.subTest(prefix=prefix):
                controller,client,rows,bridge=await simulated_file_disconnect(profile,prefix=prefix)
                self.assertTrue(bridge.cleanup_unknown);self.assertIsNotNone(bridge.failure_reason)
                self.assertIn('Job chunk accepted then BLE link lost',bridge.failure_reason)
                self.assertEqual(client.way,2) # Host cleanup must not claim remote way=0.
                uncertain=next(i for i,r in enumerate(rows) if r['event']=='stream_write_uncertain')
                self.assertFalse(any(r['event']=='stream_write_reserved' for r in rows[uncertain+1:]))
                self.assertFalse(any(r['event'] in ('controlled_job_completed','cleanup_verified') for r in rows))
                reserved=[r for r in rows if r['event']=='stream_write_reserved'][-1]
                previous=[r for r in rows if r['event']=='stream_verified'][-1]
                self.assertEqual(reserved['before'],previous['write_check'])
                self.assertEqual(client.w,reserved['after'])
                self.assertEqual(client.r,(previous['read_check']+len(prefix))&65535)
                self.assertEqual(next(r for r in rows if r['event']=='stream_stopped')['pending_bytes'],len(prefix))
                abandoned=next(r for r in rows if r['event']=='control_generation_abandoned')
                self.assertTrue(abandoned['before']['pending']);self.assertFalse(any(abandoned['after'].values()))
                self.assertFalse(abandoned['remote_stop_inferred'])
                self.assertNotIn(b'G0\n',controller.writes);self.assertNotIn(b'\x18',controller.writes)


class USBFileCLITests(unittest.TestCase):
    def test_offline_usb_review_and_missing_changed_or_conflicting_gates_never_open_serial(self):
        script=Path(__file__).resolve().parents[1]/'tools/controlled_usb_probe.py'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'job.gc';source.write_bytes(raw_file(1));profile=load(source)
            (root/'serial.py').write_text('raise RuntimeError("Must not import serial")\n')
            environment=dict(os.environ,PYTHONPATH=directory)
            command=[sys.executable,str(script),'--job-file',str(source)]
            result=subprocess.run(command,capture_output=True,text=True,env=environment)
            self.assertEqual(result.returncode,0,result.stderr)
            data=json.loads(result.stdout);self.assertEqual(data['scenario'],'complete')
            self.assertEqual(data['review']['sha256'],profile.sha256);self.assertEqual(data['controls'],[])
            for extra in ([],['--allow-reviewed-file'],['--expected-job-sha256',profile.sha256],
                ['--allow-reviewed-file','--expected-job-sha256','0'*64],
                ['--allow-reviewed-file','--expected-job-sha256',profile.sha256,'--scenario','motion_cancel'],
                ['--allow-reviewed-file','--expected-job-sha256',profile.sha256,'--allow-reviewed-ten-mm-job']):
                log=root/'must-not-exist.jsonl'
                result=subprocess.run(command+['--execute','--allow-reviewed-job-controls',
                    '--laser-power-isolated','--log',str(log)]+extra,capture_output=True,text=True,env=environment)
                self.assertEqual(result.returncode,2,result.stderr);self.assertFalse(log.exists())
                self.assertNotIn('Must not import serial',result.stderr)
