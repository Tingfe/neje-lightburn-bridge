"""Two-dimensional zero-power candidate. All device sessions are simulated."""
import asyncio
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from check_controlled_capture import audit
from controlled_reviewed_job import ControlledReviewedJob
from reviewed_job_file import load
import test_controlled_tcp as fixtures
from test_reviewed_file_usb import simulated_usb_file

ROOT=Path(__file__).resolve().parents[1]
SQUARE=b'M5\nS0\nG21 G90 G94 G54\nM4 S0\nG1 X10 Y400 F60\nY390\nX0\nY400\nM5\nS0\nM2\n'


def review(raw):
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'job.gc';path.write_bytes(raw)
        return load(path,profile='xy-linear-zero')


class XYController(fixtures.Controller):
    """Only deterministic endpoint/state simulation, never mechanical timing."""
    def __init__(self,**kwargs):
        super().__init__(**kwargs);self.y=400.;self.geometry_pending=bytearray();self.vertices=[(0.,400.)]
    async def write(self,wire):
        if wire==b'?':
            self.writes.append(wire)
            return f'<{self.state}|MPos:{self.x},{self.y},0,0|Bf:100,1500|FS:0,0>\n'.encode()
        result=await super().write(wire)
        if wire in (b'$$\n',b'$G\n',b'$#\n',b'$N\n',b'!',b'~',b'\x18'):
            return result
        self.geometry_pending.extend(wire)
        while b'\n' in self.geometry_pending:
            index=self.geometry_pending.index(10)
            command=bytes(self.geometry_pending[:index]).split(b';',1)[0]
            del self.geometry_pending[:index+1]
            words={letter.decode():float(value) for letter,value in
                   re.findall(rb'([XY])([+-]?(?:\d+(?:\.\d*)?|\.\d+))',command)}
            if words:
                self.x=words.get('X',self.x);self.y=words.get('Y',self.y)
                self.vertices.append((self.x,self.y));self.state='Run'
            if command.strip()==b'M2':self.state='Idle'
        return result


async def simulated_xy_ble(profile):
    case=fixtures.ControlTCPTests()
    async def scenario(reader,writer,controller,clients,rows):
        await case.wait_event(rows,'pipeline_tcp_ready')
        writer.write(b''.join(profile.wires));await writer.drain()
        await case.wait_event(rows,'controlled_job_completed')
        writer.write_eof();await writer.drain();await asyncio.wait_for(reader.read(),3)
    device,client,rows,bridge=await case.exercise(scenario,reviewed_file=profile,
        controller_class=XYController,gatt_limit=17,defer_m2=False)
    rows.append(dict(event='pipeline_server_stopped',cleanup_unknown=bridge.cleanup_unknown,
                     failure_reason=bridge.failure_reason))
    for row in rows:row['simulated']=True
    return device,client,rows,bridge


class XYReviewTests(unittest.TestCase):
    def test_square_and_diagonal_distances_bounds_and_default_x_profile_refusal(self):
        square=review(SQUARE)
        self.assertEqual(square.distance_mm,40);self.assertEqual(square.nominal_seconds,40)
        self.assertEqual(square.position_bounds,((0,10),(390,400),(0,0),(0,0)))
        self.assertEqual(square.execution_budget(),110)
        crlf=review(SQUARE.replace(b'\n',b'\r\n'))
        self.assertEqual(b''.join(crlf.wires),SQUARE.replace(b'\n',b'\r\n'))
        self.assertEqual(crlf.commands,square.commands)
        self.assertEqual(crlf.distance_mm,square.distance_mm)
        with self.assertRaises(ValueError):review(SQUARE.replace(b'G1 X10 Y400 F60\n',b'G1 X10 Y400 F60\r'))
        diagonal=review(b'M5\nS0\nG1 X3 Y396 F60\nX0 Y400\nM5\nS0\nM2\n')
        self.assertEqual(diagonal.distance_mm,10)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'xy.gc';path.write_bytes(SQUARE)
            with self.assertRaises(ValueError):load(path)

    def test_geometry_rejects_outside_travel_wrong_endpoint_rapid_arcs_and_other_modes(self):
        for command in (b'G1 X261 Y400 F60',b'G1 X-1 Y400 F60',b'G1 X0 Y401 F60',
                        b'G1 X0 Y-1 F60',b'G0 X1 Y399',b'G91',b'G20',b'G2 X1 I1',
                        b'G1 X1 Y399 Z0 F60',b'G1 X1 Y399 A0 F60',b'G1 X1 Y399 F61',b'M3 S0'):
            raw=b'M5\nS0\n'+command+b'\nG1 X0 Y400 F60\nM5\nS0\nM2\n'
            with self.subTest(command=command),self.assertRaises(ValueError):review(raw)
        with self.assertRaises(ValueError):review(b'M5\nS0\nG1 X0 Y390 F60\nM5\nS0\nM2\n')

    def test_pinned_cncjs_oracle_matches_vertices_lengths_and_bounds_for_33_linear_files(self):
        node=shutil.which('node')
        if node is None:self.skipTest('Node is required for the independent offline oracle')
        rng=random.Random(20261012)
        with tempfile.TemporaryDirectory() as directory:
            paths=[];profiles=[]
            corpus=[SQUARE]
            for _ in range(32):
                lines=[b'M5',b'S0',b'G21 G90 G94 G54',b'M4 S0']
                for _ in range(20):lines.append(f'G1 X{rng.randrange(0,261)} Y{rng.randrange(0,401)} F60'.encode())
                lines += [b'G1 X0 Y400',b'M5',b'S0',b'M2']
                corpus.append(b'\n'.join(lines)+b'\n')
            for index,raw in enumerate(corpus):
                path=Path(directory)/f'{index}.gc';path.write_bytes(raw);paths.append(path)
                profiles.append(load(path,profile='xy-linear-zero'))
            run=subprocess.run([node,str(ROOT/'tools/check_xy_geometry_oracle.cjs'),*map(str,paths)],
                               capture_output=True,text=True,timeout=20)
            self.assertEqual(run.returncode,0,run.stderr)
            oracle=json.loads(run.stdout);self.assertEqual(len(oracle),33)
            for profile,reference in zip(profiles,oracle):
                self.assertFalse(reference['arcs'])
                self.assertEqual([(float(x),float(y)) for x,y in profile.vertices],
                                 [(v['x'],v['y']) for v in reference['vertices']])
                self.assertTrue(math.isclose(float(profile.distance_mm),reference['line_distance_mm'],rel_tol=1e-12))
                self.assertEqual(list(map(list,profile.position_bounds[:2])),reference['bounds'][:2])


class XYRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_xy_file_usb_and_tcp_ble_have_actual_fragmented_path_and_private_finish(self):
        for raw in (SQUARE,SQUARE.replace(b'\n',b'\r\n')):
            profile=review(raw)
            usb,usb_rows,error=await simulated_usb_file(profile,controller_class=XYController)
            self.assertIsNone(error)
            ble,client,ble_rows,bridge=await simulated_xy_ble(profile)
            self.assertFalse(bridge.cleanup_unknown);self.assertIsNone(bridge.failure_reason)
            self.assertEqual(client.way,0)
            expected=[(float(x),float(y)) for x,y in profile.vertices]
            self.assertEqual(usb.vertices,expected);self.assertEqual(ble.vertices,expected)
            with tempfile.TemporaryDirectory() as directory:
                for backend,rows in (('usb',usb_rows),('ble',ble_rows)):
                    path=Path(directory)/(backend+'.jsonl')
                    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
                    result=audit(path,backend=backend,simulated=True,reviewed_file=profile,expected_controls=())
                    self.assertEqual(result['complete_job_commands'],len(profile.commands))
            submitted=lambda data:[r['hex'] for r in data if r['event']=='pipeline_job_submitted']
            self.assertEqual(submitted(usb_rows),submitted(ble_rows))

    async def test_runtime_bounds_and_endpoint_check_both_axes(self):
        pipeline=types.SimpleNamespace(cancelling=False,ordinary_allowed=True)
        job=ControlledReviewedJob(pipeline,None,print,position_bounds=((0,10),(390,400),(0,0),(0,0)))
        job.submitted=1;job.endpoint_query=True
        job.check_status('<Run|MPos:5,395,0,0|FS:60,0>')
        for status in ('<Run|MPos:5,389,0,0|FS:60,0>','<Run|MPos:11,395,0,0|FS:60,0>',
                       '<Idle|MPos:0,390,0,0|FS:0,0>','<Run|MPos:5,395,1,0|FS:60,0>'):
            with self.subTest(status=status),self.assertRaises(RuntimeError):job.check_status(status)


class XYGateTests(unittest.TestCase):
    def test_old_file_authorization_does_not_enable_xy_and_never_imports_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'square.gc';source.write_bytes(SQUARE);profile=load(source,profile='xy-linear-zero')
            for module in ('serial','bleak'):(root/(module+'.py')).write_text('raise RuntimeError("No hardware import")\n')
            env=dict(os.environ,PYTHONPATH=directory)
            for script in ('pipeline_tcp_bridge.py','controlled_usb_probe.py'):
                cmd=[sys.executable,str(ROOT/'tools'/script),'--job-file',str(source),'--file-profile','xy-linear-zero']
                result=subprocess.run(cmd,capture_output=True,text=True,env=env)
                self.assertEqual(result.returncode,0,result.stderr)
                log=root/'not-created.jsonl'
                result=subprocess.run(cmd+['--execute','--expected-job-sha256',profile.sha256,
                    '--allow-reviewed-file','--allow-reviewed-job-controls','--laser-power-isolated',
                    '--log',str(log)]+(['--allow-initialization'] if script.startswith('pipeline') else []),
                    capture_output=True,text=True,env=env)
                self.assertEqual(result.returncode,2,result.stderr);self.assertFalse(log.exists())
                self.assertIn('--allow-reviewed-xy-file',result.stderr)
                self.assertNotIn('No hardware import',result.stderr)
