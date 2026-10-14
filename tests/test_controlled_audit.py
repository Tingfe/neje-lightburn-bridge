import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from check_controlled_capture import audit
import test_controlled_tcp as fixtures


class AuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_ble_success_and_tampered_public_credit_source_and_reset_are_distinguished(self):
        case = fixtures.ControlTCPTests()
        async def scenario(reader,writer,controller,clients,rows):
            await case.start_job(writer, rows)
            writer.write(b'!'); await writer.drain()
            await case.wait_event(rows,'control_state',phase='holding')
            writer.write(b'~'); await writer.drain()
            await case.wait_event(rows,'control_state',phase='running')
            controller.state,controller.x='Idle',0
            clients[0].data(b'ok\n')
            await case.wait_event(rows,'controlled_job_completed')
            writer.write(b'\x18'); await writer.drain()
            await asyncio.wait_for(reader.read(),3)
        controller,client,rows,bridge = await case.exercise(scenario,reset_idle=True)
        rows.append(dict(event='pipeline_server_stopped',cleanup_unknown=bridge.cleanup_unknown))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'capture.jsonl'
            def check(data):
                path.write_text(''.join(json.dumps(row)+'\n' for row in data))
                return audit(path,backend='ble',simulated=True)
            result=check(rows)
            self.assertTrue(result['passed'])
            self.assertTrue(result['simulated'])
            for event,field,change in (
                ('pipeline_tcp_delivered','hex',lambda value:value+'6f6b0a'),
                ('pipeline_job_submitted','hex',lambda value:b'G1 X999\n'.hex()),
                ('job_request_verified','text',lambda value:value+'[MSG:invented]\n'),
                ('pipeline_write_attempt','hex',lambda value:'7e')):
                altered=copy.deepcopy(rows)
                row=next(row for row in altered if row['event']==event and
                         (event!='pipeline_write_attempt' or row['hex']=='18'))
                row[field]=change(row[field])
                with self.assertRaises((AssertionError,RuntimeError)):check(altered)
