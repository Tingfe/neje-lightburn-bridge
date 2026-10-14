# evidence/ is deliberately not part of the repository; tests that read real captures
# skip themselves explicitly rather than failing or pretending to pass.
import os as _os, unittest as _unittest
if not _os.path.isdir(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'evidence')):
    raise _unittest.SkipTest('evidence/ is not part of this repository')
import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from job_disconnect_host import observed_client,parse_args,arm_verified_run
from check_job_disconnect_capture import audit
from grbl_transport import encode_write
from reviewed_ten_mm import wire_lines
from inspect_job_disconnect_route import require_owned,run as inspect_route
from neje_protocol import GRBL_COMM_STATUS,VERIFY,SYSTEM_READ_INFO
from grbl_readonly import DISABLE_RELIABLE
from test_byte_stream import FakeClient
import test_controlled_tcp as fixtures


class Delegate:
    def __init__(self,*args,**kwargs):
        self.is_connected=True;self.writes=[];self.disconnections=0;self.fail=False
    async def write_gatt_char(self,char,data,**kwargs):
        self.writes.append((char,bytes(data),kwargs))
        if self.fail:raise ConnectionError('Underlying write failed')
    async def disconnect(self):
        self.disconnections+=1;self.is_connected=False


class HookTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_run_phase_waits_for_counter_confirmed_motion_before_disconnect(self):
        case=fixtures.ControlTCPTests();instances=[]
        def wrap(base,log):
            class Disconnectable(base):
                async def disconnect(self):
                    self.is_connected=False
                    if self.disconnected_callback:self.disconnected_callback(self)
            return observed_client(Disconnectable,log,instances,phase='verified-run')
        async def scenario(reader,writer,controller,clients,rows):
            await case.wait_event(rows,'pipeline_tcp_ready')
            writer.write(b''.join(wire_lines()));await writer.drain()
            await case.wait_event(rows,'control_status_observed',state='Run')
            self.assertTrue(instances[0].is_connected)
            source=next(r for r in reversed(rows) if r['event']=='pipeline_response_lines' and
                        b'<Run|' in bytes.fromhex(r['hex']))
            arm_verified_run(source['event'],source,instances,
                             lambda e,**f:rows.append(dict(event=e,**f)))
            await asyncio.wait_for(reader.read(),3)
        _,client,rows,bridge=await case.exercise(scenario,client_wrapper=wrap)
        self.assertEqual(client.way,2);self.assertTrue(bridge.cleanup_unknown)
        rows.extend([dict(event='pipeline_server_stopped',cleanup_unknown=True,
                          failure_reason=bridge.failure_reason),
                     dict(event='fault_experiment_capture_complete',ble_connections=1,
                          bridge_failed=True,job_success_claimed=False,automatic_reconnect=False)])
        for row in rows:row['simulated']=True
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'capture.jsonl'
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            result=audit(path,simulated=True)
        self.assertEqual(result['phase'],'verified-run')
        self.assertTrue(result['job_payload_device_counter_verified'])
        self.assertEqual(result['unverified_write_bytes'],0)
        self.assertFalse(result['job_completed'])
        self.assertGreater(result['discarded_pending_lines'],0)

    async def test_separate_inspection_only_closes_stable_owned_route_without_enabling_or_grbl(self):
        for changed in (False,True):
            clients=[];rows=[]
            class Client(FakeClient):
                def __init__(self,*args,**kwargs):
                    super().__init__();self.way=2;self.status_count=0;clients.append(self)
                async def __aenter__(self):return self
                async def __aexit__(self,*args):self.is_connected=False
                async def write_gatt_char(self,char,packet,**kwargs):
                    if packet==GRBL_COMM_STATUS:
                        self.status_count+=1
                        if changed and self.status_count==2:self.w=(self.w+1)&65535
                    await super().write_gatt_char(char,packet,**kwargs)
            class Scanner:
                @staticmethod
                async def discover(**kwargs):return [types.SimpleNamespace(name='NEJE 5 Plus')]
            proof=dict(last_verified_write_counter=65534,last_verified_read_counter=65530,
                       attempted_job_bytes=144)
            with patch.dict(sys.modules,{'bleak':types.SimpleNamespace(BleakClient=Client,BleakScanner=Scanner)}):
                if changed:
                    with self.assertRaises(RuntimeError):
                        await inspect_route(proof,lambda e,**f:rows.append(dict(event=e,**f)))
                else:await inspect_route(proof,lambda e,**f:rows.append(dict(event=e,**f)))
            self.assertTrue(all(w in (VERIFY,SYSTEM_READ_INFO,GRBL_COMM_STATUS,DISABLE_RELIABLE)
                                for w in clients[0].writes))
            self.assertEqual(clients[0].writes.count(DISABLE_RELIABLE),0 if changed else 1)
            self.assertEqual(any(r['event']=='owned_fault_route_cleanup_verified' for r in rows),not changed)

    async def test_exact_job_delegates_unchanged_before_one_disconnect(self):
        rows=[];instances=[]
        cls=observed_client(Delegate,lambda e,**f:rows.append(dict(event=e,**f)),instances)
        client=cls();packet=encode_write(b''.join(wire_lines()),65000)
        await client.write_gatt_char('ffe9',packet,response=False)
        self.assertEqual(client.writes,[('ffe9',packet,dict(response=False))])
        self.assertEqual(client.disconnections,1);self.assertFalse(client.is_connected)
        self.assertEqual(rows[1]['attempted_counter_before'],65000)
        self.assertEqual(rows[1]['attempted_counter_after'],(65000+144)&65535)
        self.assertFalse(rows[0]['device_acceptance_verified'])
        with self.assertRaises(RuntimeError):cls()

    async def test_partial_line_phase_triggers_only_on_a_mid_line_prefix(self):
        from reviewed_half_line import job as half_job
        job=half_job()
        prefix=job[:173]
        self.assertFalse(prefix.endswith(b'\n'))
        self.assertTrue(job.startswith(prefix))
        rows=[]
        cls=observed_client(Delegate,lambda e,**f:rows.append(dict(event=e,**f)),[],
                            phase='partial-line',job=job)
        client=cls()
        await client.write_gatt_char('x',encode_write(prefix,0),response=False)
        self.assertEqual(client.disconnections,1)
        self.assertEqual(client.partial_payload,prefix)
        triggers=[r for r in rows if r['event']=='job_link_disconnect_requested']
        self.assertEqual(len(triggers),1)
        self.assertFalse(triggers[0]['payload_ends_with_lf'])
        self.assertEqual(triggers[0]['payload_bytes'],len(prefix))
        self.assertEqual(triggers[0]['phase'],'partial-line')
        # A whole-line prefix and a non-prefix must never trigger. The complete job
        # cannot even be one frame: the NEJE transport caps a payload at 256 bytes,
        # which is why a partial write is unavoidable for this input.
        self.assertGreater(len(job),256)
        for payload in (job[:job.index(b'\n')+1],b'G1 X999S0F60\n'):
            with self.subTest(payload=payload[:12]):
                rows=[];instances=[]
                cls=observed_client(Delegate,lambda e,**f:rows.append(dict(event=e,**f)),
                                    instances,phase='partial-line',job=job)
                client=cls()
                await client.write_gatt_char('x',encode_write(payload,0),response=False)
                self.assertEqual(client.disconnections,0)
                self.assertFalse(any(r['event']=='job_link_disconnect_requested' for r in rows))

    async def test_other_payload_and_failed_write_never_trigger_fault(self):
        rows=[];cls=observed_client(Delegate,lambda e,**f:rows.append(dict(event=e,**f)),[])
        client=cls();await client.write_gatt_char('ffe9',encode_write(b'$I\n',0),response=False)
        self.assertFalse(client.job_disconnect_requested)
        client.fail=True
        with self.assertRaises(ConnectionError):
            await client.write_gatt_char('ffe9',encode_write(b''.join(wire_lines()),3),response=False)
        self.assertEqual(client.disconnections,0);self.assertEqual(rows,[])

    async def test_crc_or_response_mode_mismatch_rejected_before_hardware_delegate(self):
        for bad_crc in (False,True):
            cls=observed_client(Delegate,lambda *a,**k:None,[]);client=cls()
            packet=encode_write(b''.join(wire_lines()),0)
            if bad_crc:packet=packet[:-1]+bytes([packet[-1]^1])
            with self.assertRaises(AssertionError):
                await client.write_gatt_char('ffe9',packet,response=not bad_crc)
            self.assertEqual(client.writes,[])

    async def test_real_pipeline_simulation_preserves_unverified_bytes_and_fails_closed(self):
        case=fixtures.ControlTCPTests();instances=[]
        def wrap(base,log):
            class Disconnectable(base):
                async def disconnect(self):
                    self.is_connected=False
                    if self.disconnected_callback:self.disconnected_callback(self)
            return observed_client(Disconnectable,log,instances)
        async def scenario(reader,writer,controller,clients,rows):
            await case.wait_event(rows,'pipeline_tcp_ready')
            writer.write(b''.join(wire_lines()));await writer.drain()
            await asyncio.wait_for(reader.read(),3)
        _,client,rows,bridge=await case.exercise(scenario,client_wrapper=wrap)
        self.assertTrue(bridge.cleanup_unknown);self.assertIsNotNone(bridge.failure_reason)
        self.assertEqual(client.way,2);self.assertEqual(len(instances),1)
        rows.append(dict(event='pipeline_server_stopped',cleanup_unknown=bridge.cleanup_unknown,
                         failure_reason=bridge.failure_reason))
        rows.append(dict(event='fault_experiment_capture_complete',ble_connections=1,
                         bridge_failed=True,job_success_claimed=False,automatic_reconnect=False))
        for row in rows:row['simulated']=True
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'capture.jsonl'
            def check(data):
                path.write_text(''.join(json.dumps(row)+'\n' for row in data))
                return audit(path,simulated=True)
            result=check(rows)
            self.assertFalse(result['job_completed'])
            self.assertGreater(result['unverified_received_bytes'],0)
            self.assertEqual(result['discarded_pending_lines'],14)
            for mode in ('fake_ack','fake_cleanup','drop_uncertainty','uncleared'):
                altered=copy.deepcopy(rows)
                if mode=='fake_ack':altered.insert(-1,dict(event='pipeline_tcp_delivered',hex='6f6b0a'))
                elif mode=='fake_cleanup':altered.insert(-1,dict(event='cleanup_verified',way=0))
                elif mode=='drop_uncertainty':altered=[r for r in altered if r['event']!='stream_write_uncertain']
                else:next(r for r in altered if r['event']=='control_generation_abandoned')['after']['pending_lines']=1
                with self.subTest(mode=mode),self.assertRaises(AssertionError):check(altered)


class GateTests(unittest.TestCase):
    def test_real_capture_keeps_verified_partial_status_and_unverified_suffix_separate(self):
        source=ROOT/'evidence/job-disconnect-20261013/ble-job-disconnect-01.jsonl'
        result=audit(source)
        self.assertEqual(result['verified_incomplete_line_bytes'],18)
        self.assertEqual(result['unverified_received_bytes'],44)
        self.assertEqual(result['discarded_pending_lines'],14)
        rows=[json.loads(line) for line in source.read_text().splitlines()]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'altered.jsonl'
            for mode in ('drop_tail','forward_tail'):
                altered=copy.deepcopy(rows)
                if mode=='drop_tail':
                    next(r for r in altered if r['event']=='control_generation_abandoned')['before']['response_tail_hex']=''
                else:altered.insert(-1,dict(event='pipeline_tcp_delivered',hex='3c49646c657c4d506f733a302e3030302c34'))
                path.write_text(''.join(json.dumps(row)+'\n' for row in altered))
                with self.subTest(mode=mode),self.assertRaises(AssertionError):audit(path)

    def test_owned_route_window_supports_wrap_but_rejects_other_writes_and_errors(self):
        proof=dict(last_verified_write_counter=65500,last_verified_read_counter=65530,
                   attempted_job_bytes=144)
        status=dict(way=2,write_check=(65500+144)&65535,read_check=8,
                    device_tx_error=0,device_rx_error=0)
        result=require_owned(status,proof)
        self.assertEqual((result['device_write_delta'],result['device_read_delta']),(144,14))
        self.assertTrue(result['job_acceptance_complete'])
        self.assertFalse(result['job_execution_or_completion_proven'])
        for field,value in [('way',1),('write_check',109),('read_check',1020),
                            ('device_tx_error',1),('device_rx_error',1)]:
            changed=dict(status,**{field:value})
            with self.subTest(field=field),self.assertRaises(RuntimeError):require_owned(changed,proof)

    def test_missing_fault_scope_rejected_before_log_or_ble(self):
        for args in ([],['--allow-reviewed-ten-mm-job'],
                     ['--allow-reviewed-ten-mm-job','--allow-reviewed-job-controls']):
            with self.assertRaises(SystemExit):
                parse_args(['--execute','--allow-initialization','--laser-power-isolated',
                            '--log','/tmp/never-created-job-disconnect.jsonl']+args)
        accepted=parse_args(['--execute','--allow-initialization','--laser-power-isolated',
            '--log','/tmp/never-created-job-disconnect.jsonl','--allow-reviewed-ten-mm-job',
            '--allow-reviewed-job-controls','--allow-job-disconnect'])
        self.assertTrue(accepted.execute)

    def test_default_offline_does_not_import_bleak(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'bleak.py').write_text('raise RuntimeError("No BLE import allowed")\n')
            result=subprocess.run([sys.executable,str(ROOT/'tools/job_disconnect_host.py')],
                capture_output=True,text=True,env=dict(os.environ,PYTHONPATH=directory))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(json.loads(result.stdout)['offline_only'])


if __name__=='__main__':unittest.main()
