# evidence/ is deliberately not part of the repository; tests that read real captures
# skip themselves explicitly rather than failing or pretending to pass.
import os as _os, unittest as _unittest
if not _os.path.isdir(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'evidence')):
    raise _unittest.SkipTest('evidence/ is not part of this repository')
"""Recovery against actual historical raw frames and a simulated BLE device.

No physical device I/O. The lifecycle tests run the real inspector, byte stream,
query parser and independent audits rather than replacing their success values.
"""
import contextlib
import copy
import fcntl
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import lightburn_session as manager
from managed_recovery import ownership_proof,input_boundary
from grbl_transport import encode_write
from neje_protocol import GRBL_COMM_STATUS
from fake_ble import FakeClient
from test_ten_mm_runtime import IDLE,SETTINGS,MODAL
from managed_disconnect_host import observed_client
from test_job_disconnect import Delegate

ADDRESS='unit-test-device'
SOURCE=ROOT/'evidence/job-disconnect-20261013/verified-run/ble-job-disconnect-02.jsonl'


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'sessions';self.folder=self.root/'failed'
        self.folder.mkdir(parents=True)
        self.rows=[dict(event='pipeline_ble_target',name='NEJE 5 Plus',address=ADDRESS)]
        self.rows.extend(json.loads(line) for line in SOURCE.read_text().splitlines())
        self.capture=self.folder/'bridge.jsonl';self.write_capture()
        self.state=dict(state='requires_recovery',session_id='failed',directory=str(self.folder),
                        requires_explicit_recovery=True,job_completed=False,error='Real captured link loss')
        (self.root/'state.json').write_text(json.dumps(self.state))
        (self.folder/'session.json').write_text(json.dumps(self.state))

    def write_capture(self):
        self.capture.write_text(''.join(json.dumps(row)+'\n' for row in self.rows))

    def cli(self,*flags):
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            return manager.main(['recover','--session-root',str(self.root),*flags])

    def device(self,*,partial=False,changing=False,bad_power=False):
        proof=ownership_proof(self.capture);clients=[]
        device=dict(way=2,w=proof['last_verified_write_counter']+(proof['unverified_write_bytes'] if partial else 0),
                    r=proof['last_verified_read_counter']+91)
        device['w']&=65535;device['r']&=65535
        class Client(FakeClient):
            def __init__(self,*args,**kwargs):
                super().__init__();self.way=device['way'];self.w=device['w'];self.r=device['r']
                self.statuses=0;clients.append(self)
            async def __aenter__(self):return self
            async def __aexit__(self,*args):
                device.update(way=self.way,w=self.w,r=self.r);self.is_connected=False
            async def write_gatt_char(self,char,packet,**kwargs):
                if packet==GRBL_COMM_STATUS:
                    self.statuses+=1
                    if changing and len(clients)==1 and self.statuses==2:self.w=(self.w+1)&65535
                if packet[3:5]!=b'\x40\x03':return await super().write_gatt_char(char,packet,**kwargs)
                self.writes.append(packet);wire=packet[8:-1]
                if int.from_bytes(packet[6:8],'little')!=self.w:raise ValueError('Wrong simulated counter')
                self.w=(self.w+len(wire))&65535
                reply={b'?':IDLE if not bad_power else IDLE.replace(b'FS:0,0',b'FS:0,1'),
                    b'$$\n':SETTINGS,b'$G\n':MODAL,b'$#\n':b'[G54:0,0,0,0]\n[G92:0,0,0,0]\nok\n',
                    b'$N\n':b'$N0=\n$N1=\nok\n'}[wire]
                for i in range(0,len(reply),11):self.data(reply[i:i+11])
        class Scanner:
            @staticmethod
            async def discover(**kwargs):return [types.SimpleNamespace(name='NEJE 5 Plus',address=ADDRESS)]
        return types.SimpleNamespace(BleakClient=Client,BleakScanner=Scanner),clients

    def test_real_raw_capture_proof_and_tampering(self):
        proof=ownership_proof(self.capture)
        self.assertEqual(proof['last_verified_write_counter'],9649)
        self.assertEqual(proof['unverified_write_bytes'],0)
        self.assertTrue(proof['input_boundaries']['0'])
        original=copy.deepcopy(self.rows)
        for mode in ('frame','write','missing_identity','incomplete'):
            self.rows=copy.deepcopy(original)
            if mode=='frame':next(r for r in self.rows if r['event']=='frame' and r['kind']=='grbl_comm_status')['write_check']+=1
            if mode=='write':next(r for r in self.rows if r['event']=='stream_write_reserved')['after']+=1
            if mode=='missing_identity':self.rows=self.rows[1:]
            self.write_capture()
            if mode=='incomplete':self.capture.write_bytes(self.capture.read_bytes()[:-1])
            with self.subTest(mode=mode),self.assertRaises(ValueError):ownership_proof(self.capture)

    def test_preview_is_offline_and_unknown_arguments_cannot_open_a_job(self):
        before=(self.root/'state.json').read_bytes()
        with patch('managed_recovery.inspect_route',side_effect=AssertionError('No I/O')):
            self.assertEqual(self.cli(),0)
            self.assertEqual(self.cli('--execute'),1)
            with self.assertRaises(SystemExit):self.cli('--job-file','other.gc')
        self.assertEqual(before,(self.root/'state.json').read_bytes())
        self.assertFalse(list(self.folder.glob('recovery-*')))

    def test_live_owner_prevents_recovery(self):
        with (self.root/'bridge.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(self.cli('--execute','--laser-power-isolated'),1)
        self.assertFalse(list(self.folder.glob('recovery-*')))

    def test_recovered_label_without_audit_cannot_start(self):
        state=dict(self.state,state='recovered');(self.root/'state.json').write_text(json.dumps(state))
        with patch.object(manager.bridge,'main',side_effect=AssertionError('No device execution')):
            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                code=manager.main(['start','--session-root',str(self.root),'--execute',
                                   '--allow-initialization','--laser-power-isolated'])
        self.assertEqual(code,1)

    def test_complete_read_only_recovery_keeps_failure_immutable_and_never_replays(self):
        bleak,clients=self.device();raw=self.capture.read_bytes();journal=(self.folder/'session.json').read_bytes()
        with patch.dict(sys.modules,{'bleak':bleak}):
            self.assertEqual(self.cli('--execute','--laser-power-isolated'),0)
        state=json.loads((self.root/'state.json').read_text())
        self.assertEqual(state['state'],'recovered');self.assertFalse(state['job_completed'])
        self.assertEqual(state['original_job_result'],'unknown');self.assertTrue(state['recovery']['audit']['passed'])
        self.assertEqual(raw,self.capture.read_bytes());self.assertEqual(journal,(self.folder/'session.json').read_bytes())
        self.assertEqual(len(clients),2)
        wires=[p[8:-1] for c in clients for p in c.writes if p[3:5]==b'\x40\x03']
        self.assertEqual(wires,[b'?',b'$$\n',b'$G\n',b'$#\n',b'$N\n',b'?'])
        self.assertIn('原作业结果仍未知',manager.status_message(manager.status(self.root)))
        self.assertEqual(self.cli('--execute','--laser-power-isolated'),1)

    def test_changing_counters_or_power_keep_start_blocked(self):
        for kind in ('changing','power'):
            bleak,clients=self.device(changing=kind=='changing',bad_power=kind=='power')
            with patch.dict(sys.modules,{'bleak':bleak}):
                self.assertEqual(self.cli('--execute','--laser-power-isolated'),1)
            self.assertEqual(json.loads((self.root/'state.json').read_text())['state'],'requires_recovery')
            if kind=='changing':self.assertEqual(len(clients),1)

    def test_accepted_partial_controller_line_gets_no_query_and_stays_blocked(self):
        proof=ownership_proof(self.capture);wire=b'G1 X';before=proof['last_verified_write_counter']
        self.rows.extend([dict(event='stream_write_reserved',hex=wire.hex(),before=before,after=(before+len(wire))&65535),
                          dict(event='write_attempt',label='stream_data',hex=encode_write(wire,before).hex())])
        self.write_capture();self.assertFalse(ownership_proof(self.capture)['input_boundaries']['4'])
        bleak,clients=self.device(partial=True)
        with patch.dict(sys.modules,{'bleak':bleak}):self.assertEqual(self.cli('--execute','--laser-power-isolated'),1)
        self.assertEqual(len(clients),1)
        self.assertFalse(any(p[3:5]==b'\x40\x03' for p in clients[0].writes))
        self.assertEqual(json.loads((self.root/'state.json').read_text())['state'],'requires_recovery')

    def test_reset_and_non_ascii_input_cannot_be_recovered_as_trusted_position(self):
        self.assertFalse(input_boundary(b'G1 X1'))
        self.assertTrue(input_boundary(b'G1 X1\n?'))
        for raw in (b'\x18',b'\xff'):
            with self.assertRaises(ValueError):input_boundary(raw)


class ManagedHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_system_status_is_delegated_then_disconnects_only_once(self):
        rows=[];instances=[]
        client=observed_client(Delegate,lambda e,**f:rows.append(dict(event=e,**f)),instances)()
        await client.write_gatt_char('ffe9',GRBL_COMM_STATUS,response=False)
        self.assertEqual(client.disconnections,0)
        client.armed=True
        await client.write_gatt_char('ffe9',GRBL_COMM_STATUS,response=False)
        self.assertEqual(client.disconnections,1)
        self.assertEqual(client.writes,[('ffe9',GRBL_COMM_STATUS,dict(response=False))]*2)
        with self.assertRaises(RuntimeError):await client.write_gatt_char('ffe9',GRBL_COMM_STATUS,response=False)
        self.assertEqual(len(client.writes),2)


if __name__=='__main__':unittest.main()
