import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from homing_recovery_probe import HOMES, EXPECTED_SETTINGS, HomingSession, require_before, run

SETTINGS = ''.join(f'${key}={value}\n' for key, value in EXPECTED_SETTINGS.items()) + 'ok\n'
MODAL = '[GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F60 S0]\nok\n'
PARAMETERS = '[G54:0.000,0.000,0.000,0.000]\n[G92:0.000,0.000,0.000,0.000]\nok\n'
ALARM = '<Alarm|MPos:2.025,400.000,0.000,0.000|FS:0,0>'
IDLE = '<Idle|MPos:0.000,400.000,0.000,0.000|FS:0,0>'
STARTUP = '$N0=\n$N1=\nok\n'


class GuardTests(unittest.TestCase):
    def test_baseline_accepts_intermittent_status_fields_without_trusting_position(self):
        self.assertEqual(require_before(SETTINGS, MODAL, PARAMETERS, STARTUP,
                                       ALARM, ALARM[:-1] + '|Bf:100,1500>')['22'], 1)

    def test_environment_alarm_and_stability_fail_closed(self):
        bad = [(SETTINGS + '$22=1\n', MODAL, PARAMETERS, STARTUP, ALARM, ALARM),
               (SETTINGS.replace('$22=1', '$22=0'), MODAL, PARAMETERS, STARTUP, ALARM, ALARM),
               (SETTINGS.replace('$25=2500', '$25=10000'), MODAL, PARAMETERS, STARTUP, ALARM, ALARM),
               (SETTINGS, MODAL.replace('M5', 'M4'), PARAMETERS, STARTUP, ALARM, ALARM),
               (SETTINGS, MODAL, PARAMETERS.replace('G54:0.000', 'G54:1.000'), STARTUP, ALARM, ALARM),
               (SETTINGS, MODAL, PARAMETERS, STARTUP.replace('$N0=', '$N0=G0X1'), ALARM, ALARM),
               (SETTINGS, MODAL, PARAMETERS, STARTUP, ALARM, IDLE),
               (SETTINGS, MODAL, PARAMETERS, STARTUP, ALARM, ALARM.replace('2.025', '2.050')),
               (SETTINGS, MODAL, PARAMETERS, STARTUP, ALARM.replace('FS:0,0', 'FS:1,0'), ALARM)]
        for args in bad:
            with self.subTest(args=args), self.assertRaises((ValueError, RuntimeError)):
                require_before(*args)


class FakePort:
    def __init__(self, *, partial=False, home_error=False, wrong_x=False):
        self.writes = []; self.data = bytearray(); self.partial = partial
        self.home_error = home_error; self.wrong_x = wrong_x; self.homed = False
    def write(self, wire):
        self.writes.append(wire)
        if self.partial: return len(wire)-1
        if wire == b'?':reply = (IDLE if self.homed else ALARM) + '\n'
        elif wire == b'$$\n':reply = SETTINGS
        elif wire == b'$G\n':reply = MODAL
        elif wire == b'$#\n':reply = PARAMETERS
        elif wire == b'$N\n':reply = STARTUP
        elif wire in HOMES:
            if self.home_error: reply = 'error:3\n'
            else:
                self.homed = True; reply = 'ok\n'
        else: raise AssertionError(wire)
        if self.wrong_x and self.homed and wire == b'?':reply = reply.replace('0.000,400', '1.000,400')
        self.data.extend(reply.encode());return len(wire)
    def read(self, count):
        data = bytes(self.data[:3]);del self.data[:3];return data


class FakeSession(HomingSession):
    def __init__(self, log, **kw):
        super().__init__(log, allow_homing=True);self.backend.port=FakePort(**kw);self.closed=False
    async def start(self):pass
    async def close(self):self.closed=True


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_ordered_once_and_no_other_control(self):
        s=FakeSession(lambda *a, **k:None)
        for wire in (b'$HX\n', b'$X\n', b'$HY\n', b'G0X0\n'):
            with self.assertRaises(ValueError):await s.write(wire)
        s.prepared=True
        await s.write(HOMES[0])
        with self.assertRaises(ValueError):await s.write(HOMES[0])
        self.assertEqual(s.backend.port.writes,list(HOMES))

    async def test_partial_write_is_reserved_and_never_retried(self):
        s=FakeSession(lambda *a, **k:None, partial=True);s.prepared=True
        with self.assertRaises(RuntimeError):await s.write(HOMES[0])
        self.assertEqual(s.homing_attempts,1)
        with self.assertRaises(RuntimeError):await s.write(HOMES[0])
        self.assertEqual(s.backend.port.writes,[HOMES[0]])

    async def test_fragmented_real_channel_completes_and_preserves_settings(self):
        rows=[];log=lambda e,**kw:rows.append(dict(event=e,**kw));s=FakeSession(log)
        await run(s,log)
        self.assertTrue(s.closed)
        self.assertEqual([x for x in s.backend.port.writes if x in HOMES],list(HOMES))
        self.assertTrue(any(r['event']=='homing_recovery_passed' for r in rows))

    async def test_readonly_inspection_never_sends_homing(self):
        s=FakeSession(lambda *a, **k:None)
        await run(s,lambda *a,**k:None,inspect_only=True)
        self.assertFalse(any(x in HOMES for x in s.backend.port.writes));self.assertTrue(s.closed)

    async def test_rejected_home_or_wrong_endpoint_prevents_next_axis(self):
        for options in (dict(home_error=True),dict(wrong_x=True)):
            with self.subTest(options=options):
                s=FakeSession(lambda *a, **k:None,**options)
                with self.assertRaises(RuntimeError):await run(s,lambda *a,**k:None)
                self.assertEqual([x for x in s.backend.port.writes if x in HOMES],[HOMES[0]])
                self.assertTrue(s.closed)


if __name__ == '__main__':unittest.main()
