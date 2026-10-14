"""Offline review of one 10 mm zero-power native export; no hardware entry.

Runtime gates are implemented separately; this review never grants permission.
"""
import hashlib
from pathlib import Path
from reviewed_job import JOB

EXPORT=Path(__file__).resolve().parents[1]/'evidence/pipeline-lightburn/ten-mm-reviewed.gc'
SHA256='89c60986076dca8edd9031e9f9b6c0c09b9fd07affa2bf32cd9cbea26b062d17'
TEN_MM_JOB=tuple(b'G1 X10S0F60' if line==b'G1 X1S0F60' else line for line in JOB)


def review(path=EXPORT):
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=SHA256:
        raise ValueError('10 mm export changed; review again')
    commands=tuple(line.split(b';',1)[0].strip() for line in raw.splitlines())
    commands=tuple(line for line in commands if line)
    if commands!=TEN_MM_JOB:
        raise ValueError('10 mm native export differs from fixed review')
    return dict(offline_only=True,sha256=SHA256,commands=[line.decode() for line in commands],
                x_range_mm=[0,10],y_mm=400,feed_mm_min=60,power=0,hardware_executed=False,
                motion_authorized=False,job_bridge_ready=True)


def wire_lines(path=EXPORT):
    review(path)
    # LightBurn omits full-line export comments when streaming, including the
    # localized layer description. Preserve inline comments and command bytes.
    lines = tuple(line+b'\n' for line in Path(path).read_bytes().splitlines()
                  if line.split(b';', 1)[0].strip())
    b''.join(lines).decode('ascii')
    return lines


if __name__=='__main__':
    import json
    print(json.dumps(review(),indent=2))
