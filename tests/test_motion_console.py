from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from lightburn_one_mm_bridge import MotionConsoleParser
from motion_parity_probe import MOVES
class ConsoleParserTests(unittest.TestCase):
    def test_exact_motion_and_all_tcp_splits(self):
        for name,payload in MOVES.items():
            for i in range(len(payload)+1):
                p=MotionConsoleParser()
                self.assertEqual(p.feed(payload[:i])+p.feed(payload[i:]),[('query',name)])
    def test_other_motion_is_blocked(self):
        for payload in (b'G53 G1 X2.000 F60.000\n',b'G1 X1\n',b'G53 G1 X1.000 F60.000 S0\n',b'M3\n',b'$H\n',b'G91\n'):
            self.assertEqual(MotionConsoleParser().feed(payload)[0][0],'blocked')

from unittest.mock import patch
from lightburn_one_mm_bridge import MotionConsoleSession
from lightburn_init_bridge import InitSession
class ConsoleSequenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_cycle_only_and_duplicate_rejected_before_write(self):
        sent=[]; x=0
        async def fake_query(session,name):
            nonlocal x
            sent.append(name)
            if name=='settings': return b'$130=260\n$131=400\n$13=0\nok\n'
            if name=='modal': return b'[GC:G0 G54 G21 G90 G94 M5 S0]\nok\n'
            if name=='parameters': return b'[G54:0,0,0,0]\n[G92:0,0,0,0]\nok\n'
            if name=='status': return f'<Idle|MPos:{x},400,0,0|FS:0,0>\n'.encode()
            x=1 if name=='x_out' else 0
            return b'ok\n'
        session=MotionConsoleSession(None,lambda *a,**k:None)
        with patch.object(InitSession,'query',fake_query):
            with self.assertRaises(RuntimeError): await session.query('x_back')
            self.assertEqual(sent,[])
            await session.query('x_out')
            prior=list(sent)
            with self.assertRaises(RuntimeError): await session.query('x_out')
            self.assertEqual(sent,prior)
            await session.query('x_back')
            prior=list(sent)
            with self.assertRaises(RuntimeError): await session.query('x_out')
            self.assertEqual(sent,prior)
        self.assertEqual([n for n in sent if n in MOVES],['x_out','x_back'])

if __name__=='__main__': unittest.main()
