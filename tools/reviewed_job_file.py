"""Offline review of bounded zero-power X/XY files with explicit arc/rapid profiles.

No discovery, device I/O or motion authorization in this module. This is the first
file-review profiles; powered machining and relative axis modes remain work.
"""
import argparse
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
from reviewed_arc_geometry import arc_geometry, BOUND_EPSILON

MAX_BYTES=4*1024*1024
WORD=re.compile(rb'([GMXYFSIJR])([+-]?(?:\d+(?:\.\d*)?|\.\d+))')
ALLOWED_G={Decimal(n) for n in (0,1,17,21,40,54,90,94)}
ALLOWED_M={Decimal(n) for n in (2,4,5,9)}
POWER_PROFILES=('xy-linear-power','xy-general-power')
PROFILES=('x-linear-zero','xy-linear-zero','xy-arc-zero','xy-job-zero')+POWER_PROFILES
RAPID_BASELINE={'110':18000.,'111':18000.,'120':250.,'121':250.}
# Multi-hour stability runs are an explicit goal, so the active budget is bounded at
# seven hours instead of one. The bound still exists: it is what keeps a runaway file
# from becoming an unbounded session, and every execution gate is unchanged.
MAX_ACTIVE_SECONDS=25200


@dataclass(frozen=True)
class ReviewedFile:
    sha256:str
    commands:tuple
    wires:tuple
    distance_mm:Decimal
    nominal_seconds:Decimal
    geometry_profile:str='x-linear-zero'
    position_bounds:tuple=((0,10),(400,400),(0,0),(0,0))
    vertices:tuple=()
    arcs:tuple=()
    rapid_segments:int=0
    rapid_distance_mm:Decimal=Decimal(0)
    power_ceiling:int=0
    feed_ceiling_mm_min:int=60
    max_power_used:int=0
    end_position:tuple=(0,400,0,0)

    def require_rapid_settings(self, text):
        if not self.rapid_segments:return
        values={}
        for line in text.splitlines():
            if line.startswith('$') and '=' in line:
                key,value=line[1:].split('=',1)
                if key in RAPID_BASELINE:
                    if key in values:raise RuntimeError('Ambiguous rapid preflight setting')
                    try:values[key]=float(value)
                    except ValueError:raise RuntimeError('Invalid rapid preflight setting') from None
        if values!=RAPID_BASELINE:
            raise RuntimeError('Rapid rates/accelerations differ from reviewed baseline; no job submission')

    def execution_budget(self):
        """Bounded active budget; the caller still owns a separate wall limit."""
        nominal=float(self.nominal_seconds)
        if not math.isfinite(nominal) or nominal*2+30>MAX_ACTIVE_SECONDS:
            raise ValueError(f'Reviewed file exceeds the {MAX_ACTIVE_SECONDS}-second active budget')
        return max(45,math.ceil(nominal*2+30))

    def summary(self):
        return dict(offline_only=True,sha256=self.sha256,command_count=len(self.commands),
                    profile='powered_marking_xy' if self.geometry_profile in POWER_PROFILES
                        else 'zero_power_job_xy' if self.geometry_profile=='xy-job-zero'
                        else 'zero_power_arcs_xy' if self.geometry_profile=='xy-arc-zero'
                        else 'zero_power_linear_xy' if self.geometry_profile=='xy-linear-zero'
                        else 'zero_power_linear_x_0_to_10',
                    geometry_profile=self.geometry_profile,start=[0,400,0,0],end=list(self.end_position),
                    x_range=list(self.position_bounds[0]),y_range=list(self.position_bounds[1]),
                    position_bounds=self.position_bounds,
                    feed_ceiling_mm_min=None if self.rapid_segments else self.feed_ceiling_mm_min,
                    g1_g2_g3_feed_ceiling_mm_min=self.feed_ceiling_mm_min,
                    power=self.max_power_used,power_ceiling=self.power_ceiling,
                    powered=self.geometry_profile in POWER_PROFILES,
                    path_distance_mm=str(self.distance_mm),
                    nominal_linear_motion_seconds=str(self.nominal_seconds) if not self.arcs and not self.rapid_segments else None,
                    nominal_motion_seconds=str(self.nominal_seconds) if not self.rapid_segments else None,
                    motion_budget_allowance_seconds=str(self.nominal_seconds),arc_count=len(self.arcs),
                    rapid_segments=self.rapid_segments,rapid_distance_mm=str(self.rapid_distance_mm),
                    rapid_actual_speed_is_not_limited_by_F60=bool(self.rapid_segments),
                    required_rapid_settings=RAPID_BASELINE if self.rapid_segments else None,
                    arc_bounds_are_analytical_not_firmware_interpolation_proof=bool(self.arcs),
                    nominal_time_excludes_acceleration_communication_and_controls=True,
                    required_preflight=['G21','G90','G94','G54','M5','S0','zero offsets','Idle/X0/Y400'],
                    hardware_executed=False,motion_authorized=False,
                    runtime_file_cli_requires_separate_authorization=True)


def load(path, *, expected_sha256=None, profile='x-linear-zero',
         power_ceiling=None, feed_ceiling=None):
    if profile not in PROFILES:raise ValueError('Unknown file geometry profile')
    powered=profile in POWER_PROFILES
    if powered:
        # A powered review must carry an explicit, caller-declared ceiling. There is
        # no default: the device maximum ($30=1000) is not a protective limit.
        if power_ceiling is None or feed_ceiling is None:
            raise ValueError('Powered profile requires declared power and feed ceilings')
        if not 0<power_ceiling<=1000:raise ValueError('power_ceiling must be 1..1000')
        if not 0<feed_ceiling<=6000:raise ValueError('feed_ceiling must be 1..6000')
    path=Path(path)
    if path.stat().st_size>MAX_BYTES:raise ValueError('Review file exceeds 4 MiB')
    raw=path.read_bytes()
    if len(raw)>MAX_BYTES:raise ValueError('Review file exceeds 4 MiB')
    digest=hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and digest!=expected_sha256:
        raise ValueError('Reviewed source changed; refusing the stale digest')
    if not raw.endswith(b'\n'):raise ValueError('Require a final newline')
    wires=tuple(line for line in raw.splitlines(keepends=True) if line.split(b';',1)[0].strip())
    if any(not line.endswith(b'\n') for line in wires):
        raise ValueError('Each command line requires LF or CRLF; no silent newline conversion')
    commands=tuple(line.split(b';',1)[0].strip() for line in wires)
    if not 4<=len(commands)<=50000 or commands[:2]!=(b'M5',b'S0') or commands[-3:]!=(b'M5',b'S0',b'M2'):
        raise ValueError('Require M5/S0 prefix, M5/S0/M2 suffix and bounded command count')
    x,y,feed=Decimal(0),Decimal(400),None
    vertices=[(x,y)]
    extrema=[(float(x),float(y))];arcs=[];plane_declared=False
    motion,spindle=Decimal(0),Decimal(5)
    distance=seconds=rapid_distance=Decimal(0);rapid_segments=0;max_power=Decimal(0)
    for index,(wire,command) in enumerate(zip(wires,commands)):
        if len(wire)>512 or any(byte not in (9,10,13) and not 32<=byte<127 for byte in wire):
            raise ValueError('Only bounded ASCII command lines; full-line UTF-8 comments are omitted')
        if any(byte in b'?!~' for byte in wire):raise ValueError('Realtime bytes are forbidden in source lines')
        words=list(WORD.finditer(command))
        if re.sub(rb'\s+',b'',WORD.sub(b'',command)) or not words:
            raise ValueError('Unsupported or malformed GCode word')
        values={}
        for match in words:
            letter=match[1].decode();number=Decimal(match[2].decode())
            values.setdefault(letter,[]).append(number)
        for letter in ('X','Y','F','S','I','J','R'):
            if len(values.get(letter,()))>1:raise ValueError('Duplicate scalar word')
        g,m=values.get('G',[]),values.get('M',[])
        allowed_g=ALLOWED_G|{Decimal(2),Decimal(3),Decimal('91.1')} if profile in ('xy-arc-zero','xy-job-zero','xy-general-power') else ALLOWED_G
        if any(n not in allowed_g for n in g) or any(n not in ALLOWED_M for n in m):
            raise ValueError('Unsupported modal/control command')
        groups=[{Decimal(0),Decimal(1),Decimal(2),Decimal(3)},{Decimal(17)},{Decimal(21)},
                {Decimal(40)},{Decimal(54)},{Decimal(90)},{Decimal('91.1')},{Decimal(94)}]
        if any(sum(n in group for n in g)>1 for group in groups) or len(m)>1:
            raise ValueError('Ambiguous modal group')
        if Decimal(2) in m and (index!=len(commands)-1 or len(words)!=1):
            raise ValueError('M2 must be the final standalone command')
        if 'S' in values:
            if powered:
                if not 0<=values['S'][0]<=power_ceiling:
                    raise ValueError('S outside the declared power ceiling')
                max_power=max(max_power,values['S'][0])
            elif values['S'][0]!=0:
                raise ValueError('Nonzero power is forbidden')
        if profile=='x-linear-zero' and 'Y' in values and values['Y'][0]!=400:
            raise ValueError('Y must remain 400 mm')
        if 'F' in values:
            feed=values['F'][0]
            if not 0<feed<=(feed_ceiling if powered else 60):
                raise ValueError('Feed must be positive and within the declared ceiling')
        # GRBL retains the previous S value when a block omits S. The mandatory
        # S0 prefix and rejection of every nonzero S keep bare M4 at zero power.
        if m and m[0] in (Decimal(4),Decimal(5)):spindle=m[0]
        if Decimal(17) in g:plane_declared=True
        for n in g:
            if n in (Decimal(0),Decimal(1),Decimal(2),Decimal(3)):motion=n
        target=values.get('X',[x])[0]
        target_y=values.get('Y',[y])[0]
        max_x=10 if profile=='x-linear-zero' else 260
        if not 0<=target<=max_x:raise ValueError('X outside selected file review envelope')
        if not 0<=target_y<=400:raise ValueError('Y outside 0..400 mm machine travel')
        arc_words=any(letter in values for letter in ('I','J','R'))
        arc_block=motion in (2,3) and (arc_words or 'X' in values or 'Y' in values or any(n in (2,3) for n in g))
        if arc_block:
            if profile not in ('xy-arc-zero','xy-job-zero','xy-general-power') or not plane_declared or not any(axis in values for axis in ('X','Y')):
                raise ValueError('Arc requires explicit XY profile, declared G17 plane and an axis word')
            arc=arc_geometry((x,y),(target,target_y),clockwise=motion==2,
                             **{letter.lower():values[letter][0] for letter in ('I','J','R') if letter in values})
            if any(low < -BOUND_EPSILON or high > limit+BOUND_EPSILON
                   for (low,high),limit in zip(arc.bounds,(260,400))):
                raise ValueError('Arc interior outside machine travel; endpoints alone are insufficient')
            arcs.append(arc)
            extrema.extend([(arc.bounds[0][0],arc.bounds[1][0]),(arc.bounds[0][1],arc.bounds[1][1])])
            travel=Decimal(str(arc.length))
        else:
            if arc_words:raise ValueError('Unused arc words are forbidden on a linear/modal block')
            travel=((target-x)**2+(target_y-y)**2).sqrt()
        if travel:
            if motion==0 and profile in ('xy-job-zero','xy-general-power'):
                rapid_segments+=1;rapid_distance+=travel
                # Conservative budget only: 1 mm/s traversal plus a full-stop
                # acceleration allowance at the freshly verified baseline.
                # This never changes the command or limits actual G0 speed.
                seconds+=travel+2*(travel/250).sqrt()
            elif powered and 'S' not in values:
                # Never rely on the controller's modal S: every powered motion line
                # declares its own value so a missing line cannot inherit power.
                raise ValueError('Powered movement requires an explicit S on every motion line')
            elif powered and values['S'][0]>0 and spindle!=Decimal(4):
                # A nonzero S without M4 would be reviewed as powered marking while
                # the spindle modal is still off; require M4 explicitly.
                raise ValueError('Powered motion requires M4 before any nonzero S')
            elif (motion not in (1,2,3) or not arc_block and motion!=1
                    or feed is None or spindle not in (4,5)):
                raise ValueError('Only feed-controlled reviewed movement')
            else:seconds+=travel/feed*60
            distance+=travel
            vertices.append((target,target_y))
            extrema.append((float(target),float(target_y)))
        x,y=target,target_y
    if (x,y)!=(0,400) and profile!='xy-general-power':
        raise ValueError('Normal file endpoint must return to X0/Y400')
    # The general daily-use profile lets a real design end where it ends; the
    # completion check then verifies that declared endpoint instead of X0/Y400.
    end_position=(0,400,0,0) if (x,y)==(0,400) else (float(x),float(y),0,0)
    if not distance:raise ValueError('Require a real nonzero path for file review')
    bounds=((0,10),(400,400),(0,0),(0,0))
    if profile!='x-linear-zero':
        bounds=tuple((float(min(v[axis] for v in extrema)),float(max(v[axis] for v in extrema)))
                     for axis in (0,1))+((0,0),(0,0))
    return ReviewedFile(digest,commands,wires,distance,seconds,profile,bounds,tuple(vertices),tuple(arcs),
                        rapid_segments,rapid_distance,int(power_ceiling or 0),
                        int(feed_ceiling if powered else 60),int(max_power),end_position)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file',type=Path)
    parser.add_argument('--expected-sha256')
    parser.add_argument('--profile',choices=PROFILES,default=PROFILES[0])
    args=parser.parse_args()
    print(json.dumps(load(args.file,expected_sha256=args.expected_sha256,profile=args.profile).summary(),ensure_ascii=False,indent=2))
