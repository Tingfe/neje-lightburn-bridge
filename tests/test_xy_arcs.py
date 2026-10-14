"""Arc candidates are offline; simulated transport is not physical interpolation."""
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from check_controlled_capture import audit
from reviewed_arc_geometry import arc_geometry
from reviewed_job_file import load
from test_reviewed_file_usb import simulated_usb_file
from test_xy_linear import XYController, simulated_xy_ble

ROOT=Path(__file__).resolve().parents[1]
HALVES=b'M5\nS0\nG17 G21 G90 G94 G54 G91.1\nM4 S0\nG3 X10 Y400 I5 J0 F60\nG2 X0 Y400 R5\nM5\nS0\nM2\n'
FULL=b'M5\nS0\nG17\nG1 X10 Y390 F60\nG2 X10 Y390 J-10\nG1 X0 Y400\nM5\nS0\nM2\n'


def review(raw):
    with tempfile.TemporaryDirectory() as directory:
        p=Path(directory)/'job.gc';p.write_bytes(raw)
        return load(p,profile='xy-arc-zero')


def corpus():
    """Exact cardinal endpoints avoid passing one parser's rounding to the other."""
    rng=random.Random(20261012)
    files=[HALVES,FULL]
    for _ in range(32):
        radius=rng.randrange(1,26);cx,cy=rng.randrange(50,201),rng.randrange(50,301)
        start=(cx,cy+radius);end=(cx+radius,cy)
        mode=rng.choice((2,3));kind=rng.choice(('ij','r+','r-'))
        offset=f'I0 J{-radius}' if kind=='ij' else f'R{radius if kind=="r+" else -radius}'
        lines=['M5','S0','G17 G21 G90 G94 G54','M4 S0',f'G1 X{start[0]} Y{start[1]} F60',
               f'G{mode} X{end[0]} Y{end[1]} {offset}','G1 X0 Y400','M5','S0','M2']
        files.append(('\n'.join(lines)+'\n').encode())
    return files


def independent_comparison(profile, reference):
    """CNCjs supplies centers; independently sampled curves check analytic bounds."""
    if len(profile.arcs)!=len(reference['arcs']):raise AssertionError('Arc count mismatch')
    total=reference['line_distance_mm']
    points=[(0.,400.)]
    for segment in reference['segments']:
        points.extend((p['x'],p['y']) for p in (segment['from'],segment['to']))
    for arc,other in zip(profile.arcs,reference['arcs']):
        center=(other['center']['x'],other['center']['y'])
        if not all(math.isclose(a,b,abs_tol=1e-9) for a,b in zip(arc.center,center)):
            raise AssertionError('CNCjs arc center mismatch')
        if (arc.sweep_radians<0)!=(other['motion']=='G2'):raise AssertionError('Direction mismatch')
        start=math.atan2(other['from']['y']-center[1],other['from']['x']-center[0])
        finish=math.atan2(other['to']['y']-center[1],other['to']['x']-center[0])
        # Independent modulo-angle reference. Near-zero GRBL epsilon is tested separately.
        sweep=((start-finish) if other['motion']=='G2' else (finish-start))%math.tau
        if sweep<1e-12:sweep=math.tau
        radius=math.hypot(other['from']['x']-center[0],other['from']['y']-center[1])
        total+=radius*sweep
        for index in range(4097):
            angle=start+(-1 if other['motion']=='G2' else 1)*sweep*index/4096
            point=(center[0]+radius*math.cos(angle),center[1]+radius*math.sin(angle));points.append(point)
            if any(not low-1e-8<=value<=high+1e-8 for value,(low,high) in zip(point,arc.bounds)):
                raise AssertionError('Sampled CNCjs arc escaped reviewed analytic bounds')
    if not math.isclose(float(profile.distance_mm),total,abs_tol=1e-8):raise AssertionError('Length mismatch')
    sampled=tuple((min(p[axis] for p in points),max(p[axis] for p in points)) for axis in (0,1))
    if any(not math.isclose(a,b,abs_tol=1e-5) for bounds,sample in zip(profile.position_bounds,sampled)
           for a,b in zip(bounds,sample)):raise AssertionError('Combined bounds mismatch')
    return dict(passed=True,arcs=len(profile.arcs),distance_mm=total,sample_steps_per_arc=4096)


class ArcGeometryTests(unittest.TestCase):
    def test_cw_ccw_half_full_circle_bounds_and_pi_lengths(self):
        profile=review(HALVES)
        self.assertAlmostEqual(float(profile.distance_mm),10*math.pi)
        self.assertEqual(profile.position_bounds,((0,10),(395,400),(0,0),(0,0)))
        self.assertEqual(len(profile.arcs),2)
        full=review(FULL)
        self.assertAlmostEqual(full.arcs[0].length,20*math.pi)
        self.assertEqual(full.arcs[0].bounds,((0,20),(370,390)))
        crlf=review(HALVES.replace(b'\n',b'\r\n'))
        self.assertEqual(b''.join(crlf.wires),HALVES.replace(b'\n',b'\r\n'))
        self.assertEqual(crlf.distance_mm,profile.distance_mm)
        for old in ('x-linear-zero','xy-linear-zero'):
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/'job.gc';p.write_bytes(HALVES)
                with self.assertRaises(ValueError):load(p,profile=old)

    def test_endpoints_inside_but_arc_interior_outside_machine_is_rejected(self):
        bad=HALVES.replace(b'G3 X10',b'G2 X10')
        with self.assertRaisesRegex(ValueError,'interior outside'):review(bad)

    def test_r_format_sign_selects_minor_major_and_distinct_centers(self):
        for clockwise in (False,True):
            minor=arc_geometry((30,300),(40,290),clockwise=clockwise,r=10)
            major=arc_geometry((30,300),(40,290),clockwise=clockwise,r=-10)
            self.assertAlmostEqual(abs(minor.sweep_radians),math.pi/2)
            self.assertAlmostEqual(abs(major.sweep_radians),3*math.pi/2)
            self.assertNotEqual(minor.center,major.center)

    def test_invalid_definitions_planes_words_and_feed_are_refused(self):
        for command in (b'G2 X10 Y400 I5 R5 F60',b'G2 X10 Y400 R4 F60',
                        b'G2 X0 Y400 R5 F60',b'G2 X10 Y400 I0 J0 F60',
                        b'G2 X10 Y400 I2 J0 F60',b'G2 I5 F60',b'G2 X10 F60',
                        b'G2 X10 I5 I5 F60',b'G2 X10 I5 F61',b'G18',b'G90.1',
                        b'G1 X10 I5 F60',b'G2 X10 I5 Z0 F60',b'G2 X10 I5 S1 F60',
                        b'G2 X10 R10001 F60'):
            raw=b'M5\nS0\nG17\n'+command+b'\nG1 X0 Y400 F60\nM5\nS0\nM2\n'
            with self.subTest(command=command),self.assertRaises(ValueError):review(raw)
        with self.assertRaisesRegex(ValueError,'declared G17'):review(HALVES.replace(b'G17 ',b''))
        with self.assertRaises(ValueError):review(HALVES.replace(b' F60',b''))

    def test_upstream_radius_tolerance_and_near_zero_full_circle_semantics(self):
        near=arc_geometry((10,0),(10.004,0),clockwise=True,i=-10)
        self.assertGreater(near.length,62)
        with self.assertRaisesRegex(ValueError,'radii disagree'):
            arc_geometry((10,0),(10.02,0),clockwise=True,i=-10)
        target=(10*math.cos(1e-7),10*math.sin(1e-7))
        tiny=arc_geometry((10,0),target,clockwise=False,i=-10)
        self.assertGreater(tiny.sweep_radians,math.tau)
        self.assertAlmostEqual(tiny.bounds[0][0],-10)
        self.assertAlmostEqual(tiny.bounds[1][1],10)

    def test_34_files_match_pinned_cncjs_centers_and_independent_curve_sampling(self):
        node=shutil.which('node')
        if node is None:self.skipTest('Node required for independent offline geometry oracle')
        with tempfile.TemporaryDirectory() as d:
            paths=[];profiles=[]
            for index,raw in enumerate(corpus()):
                p=Path(d)/f'{index}.gc';p.write_bytes(raw);paths.append(p);profiles.append(load(p,profile='xy-arc-zero'))
            result=subprocess.run([node,str(ROOT/'tools/check_xy_geometry_oracle.cjs'),*map(str,paths)],
                                  capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            for profile,reference in zip(profiles,json.loads(result.stdout)):
                self.assertFalse(reference['bounds_are_complete'])  # Original oracle remains honest.
                self.assertTrue(independent_comparison(profile,reference)['passed'])


class ArcRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_usb_ble_arc_files_keep_exact_wire_and_private_finish_and_strict_audit(self):
        for raw in (HALVES,FULL,HALVES.replace(b'\n',b'\r\n')):
            profile=review(raw)
            usb,usb_rows,error=await simulated_usb_file(profile,controller_class=XYController)
            self.assertIsNone(error)
            ble,client,rows,bridge=await simulated_xy_ble(profile)
            self.assertFalse(bridge.cleanup_unknown);self.assertIsNone(bridge.failure_reason)
            self.assertEqual(client.way,0)
            self.assertEqual(usb.vertices,ble.vertices)
            self.assertEqual(usb.vertices,[(float(x),float(y)) for x,y in profile.vertices])
            with tempfile.TemporaryDirectory() as d:
                for backend,data in (('usb',usb_rows),('ble',rows)):
                    p=Path(d)/(backend+'.jsonl');p.write_text(''.join(json.dumps(r)+'\n' for r in data))
                    self.assertTrue(audit(p,backend=backend,simulated=True,reviewed_file=profile,expected_controls=())['passed'])
                    ready=next(r for r in data if r['event'] in ('pipeline_tcp_ready','usb_control_ready'))
                    if backend=='usb':
                        self.assertEqual(ready['x_range'],list(profile.position_bounds[0]))
                        self.assertEqual(ready['y_range'],list(profile.position_bounds[1]))
                    original=ready['reviewed_position_bounds'];ready['reviewed_position_bounds']=((0,10),(400,400),(0,0),(0,0))
                    p.write_text(''.join(json.dumps(r)+'\n' for r in data))
                    with self.assertRaises(AssertionError):audit(p,backend=backend,simulated=True,reviewed_file=profile,expected_controls=())
                    ready['reviewed_position_bounds']=original


class ArcGateTests(unittest.TestCase):
    def test_old_xy_authorization_cannot_enable_arc_or_import_hardware(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'arc.gc';source.write_bytes(HALVES);profile=review(HALVES)
            for module in ('serial','bleak'):(root/(module+'.py')).write_text('raise RuntimeError("No hardware import")\n')
            env=dict(os.environ,PYTHONPATH=d)
            for script in ('pipeline_tcp_bridge.py','controlled_usb_probe.py'):
                cmd=[sys.executable,str(ROOT/'tools'/script),'--job-file',str(source),'--file-profile','xy-arc-zero']
                offline=subprocess.run(cmd,capture_output=True,text=True,env=env)
                self.assertEqual(offline.returncode,0,offline.stderr)
                log=root/'not-created.jsonl'
                result=subprocess.run(cmd+['--execute','--expected-job-sha256',profile.sha256,
                    '--allow-reviewed-file','--allow-reviewed-xy-file','--allow-reviewed-job-controls',
                    '--laser-power-isolated','--log',str(log)]+(['--allow-initialization'] if script.startswith('pipeline') else []),
                    capture_output=True,text=True,env=env)
                self.assertEqual(result.returncode,2,result.stderr);self.assertFalse(log.exists())
                self.assertIn('--allow-reviewed-arc-file',result.stderr)
                self.assertNotIn('No hardware import',result.stderr)
