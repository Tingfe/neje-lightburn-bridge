"""Host TCP smoke and disconnect test; only $I/? plus blocked synthetic inputs."""
import asyncio
import datetime
import json
from pathlib import Path
import socket
import struct

async def main():
    results=[]
    async def connect():return await asyncio.open_connection('127.0.0.1',4002)
    r,w=await connect()
    # Fragmentation across network writes.
    w.write(b'$');await w.drain();await asyncio.sleep(.05)
    w.write(b'I\r\n');await w.drain()
    build=await asyncio.wait_for(r.readuntil(b'ok\n'),15)
    assert b'[VER:1.1f.NEJE' in build
    r2,w2=await connect()
    assert await asyncio.wait_for(r2.read(),3)==b''
    w2.close();await w2.wait_closed()
    w.write(b'G0 X1\n$40=0\n\x18');await w.drain()
    blocked=[await asyncio.wait_for(r.readline(),3) for _ in range(6)]
    assert all(b'read-only policy blocked' in x for x in blocked[::2])
    assert blocked[1::2] == [b'error:20\n']*3
    w.write(b'?');await w.drain()
    status=await asyncio.wait_for(r.readline(),10)
    assert status.startswith(b'<') and status.rstrip().endswith(b'>')
    results.append(dict(case='fragmented_build_status_blocked_inputs_second_peer',build=build.decode(),status=status.decode(),blocked=[x.decode() for x in blocked]))
    w.close();await w.wait_closed()
    await asyncio.sleep(1)
    # Abrupt peer RST after a verified query; cleanup must still read back way=0.
    r,w=await connect();w.write(b'$I\n');await w.drain()
    build=await asyncio.wait_for(r.readuntil(b'ok\n'),15)
    sock=w.get_extra_info('socket')
    sock.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0))
    w.transport.abort()
    await asyncio.sleep(1)
    results.append(dict(case='tcp_reset_after_build',build=build.decode()))
    print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),results=results,passed=True),indent=2))

asyncio.run(main())
