"""Offline XY arc geometry, using the pinned upstream GRBL conventions.

Analytical path bounds are not a proof of NEJE interpolation or physical travel.
No hardware I/O, linearization, command rewriting or authorization lives here.
"""
from dataclasses import dataclass
import math

ANGULAR_EPSILON = 5e-7  # Upstream GRBL config.h; near-zero arcs become full circles.
BOUND_EPSILON = 1e-9


@dataclass(frozen=True)
class Arc:
    start: tuple
    end: tuple
    center: tuple
    radius: float
    sweep_radians: float
    bounds: tuple

    @property
    def length(self):
        return abs(self.sweep_radians)*self.radius


def arc_geometry(start, end, *, clockwise, i=None, j=None, r=None):
    start, end = tuple(map(float, start)), tuple(map(float, end))
    scalar = [*start, *end, *(float(v) for v in (i,j,r) if v is not None)]
    if not all(math.isfinite(v) for v in scalar):
        raise ValueError('Arc geometry must be finite')
    dx, dy = end[0]-start[0], end[1]-start[1]
    if r is not None:
        if i is not None or j is not None:
            raise ValueError('Arc cannot mix R with I/J')
        radius, chord = abs(float(r)), math.hypot(dx,dy)
        if not chord or radius < chord/2:
            raise ValueError('R arc requires distinct endpoints and radius >= half chord')
        if not 0.01 <= radius <= 10000:
            raise ValueError('Candidate arc radius must be 0.01..10000 mm')
        height = math.sqrt(max(0., radius*radius-(chord/2)**2))
        if clockwise: height = -height
        if float(r)<0: height = -height
        center = (start[0]+dx/2-dy/chord*height, start[1]+dy/2+dx/chord*height)
    else:
        if i is None and j is None:
            raise ValueError('Arc requires I/J offsets or R on each motion block')
        i, j = float(i or 0), float(j or 0)
        radius = math.hypot(i,j)
        if not 0.01 <= radius <= 10000:
            raise ValueError('Candidate arc radius must be 0.01..10000 mm')
        center = (start[0]+i,start[1]+j)
    target_radius = math.hypot(end[0]-center[0],end[1]-center[1])
    if abs(target_radius-radius) > max(.005,min(.5,.001*radius)):
        raise ValueError('Arc source/target radii disagree under upstream GRBL tolerance')
    a = (start[0]-center[0],start[1]-center[1])
    b = (end[0]-center[0],end[1]-center[1])
    sweep = math.atan2(a[0]*b[1]-a[1]*b[0],a[0]*b[0]+a[1]*b[1])
    if clockwise:
        if sweep >= -ANGULAR_EPSILON: sweep -= math.tau
    elif sweep <= ANGULAR_EPSILON:
        sweep += math.tau
    start_angle = math.atan2(a[1],a[0])
    # Include the larger radius conservatively when GRBL permits a small mismatch.
    outer = max(radius,target_radius)
    points = [start,end]
    for angle in (start_angle,start_angle+sweep):
        points.append((center[0]+outer*math.cos(angle),center[1]+outer*math.sin(angle)))
    for angle in (0.,math.pi/2,math.pi,3*math.pi/2):
        travel = ((start_angle-angle) if clockwise else (angle-start_angle)) % math.tau
        if travel <= abs(sweep)+1e-12:
            points.append((center[0]+outer*math.cos(angle),center[1]+outer*math.sin(angle)))
    bounds = tuple((min(p[axis] for p in points),max(p[axis] for p in points)) for axis in (0,1))
    return Arc(start,end,center,radius,sweep,bounds)
