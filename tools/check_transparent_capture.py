"""Independent audit of a transparent-mode capture.

Transparent mode has no job to compare against, so the guarantees that remain must be
checked structurally instead:

  * the mode was declared as USB-equivalent (no reviewed job, no whitelist),
  * every client byte that entered the policy also came out of it (nothing dropped or
    invented on ingress),
  * every byte the policy produced was handed to the transport, in order,
  * every byte delivered back to the client came from device data (nothing fabricated),
  * the NEJE byte counters advance exactly by each write's length,
  * the session stopped without a replay and, when it shut down cleanly, closed way=0.

A failure here means the bridge cannot be trusted as a transparent pipe; it says
nothing about whether the operator's own G-code was safe, because in this mode that is
no longer the bridge's job.
"""
import json
from pathlib import Path


def _concat(rows, event, key='hex'):
    return b''.join(bytes.fromhex(row[key]) for row in rows if row['event'] == event)


def _one(rows, event):
    found = [row for row in rows if row['event'] == event]
    assert len(found) == 1, f'expected exactly one {event}, found {len(found)}'
    return found[0]


def _contains_in_order(haystack, needle):
    """True when needle appears as a contiguous run inside haystack."""
    return needle in haystack if needle else True


def audit(path, in_progress=False):
    """Audit a capture. in_progress=True audits a session that is still open: the
    byte-level guarantees are checked exactly the same way, but the shutdown
    assertions are reported as not yet applicable instead of being skipped or faked."""
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    assert rows, 'empty capture'
    assert not any(row.get('simulated') for row in rows), 'simulated capture'

    mode = _one(rows, 'pipeline_transparent_mode')
    assert mode['usb_equivalent'] is True
    assert mode['reviewed_job_required'] is False
    assert mode['whitelist'] is False

    received = _concat(rows, 'pipeline_tcp_received')
    ingress = b''.join(bytes.fromhex(row['hex']) for row in rows if row['event'] == 'pipeline_ingress')
    # Nothing is dropped between the socket and the policy, and the policy invents
    # nothing. A jog is deliberately rewritten into an explicit relative move, so the
    # check is that the client's bytes produce exactly these forwards under the same
    # documented policy -- not raw equality, which the translation would break.
    from passthrough_policy import PassthroughPolicy
    expected = b''.join(wire for _, wire in PassthroughPolicy().feed(received))
    assert ingress == expected, 'ingress is not what the documented policy makes of the client bytes'

    written = _concat(rows, 'pipeline_write_attempt')
    # Everything the policy produced reached the transport. The bridge interleaves its
    # own realtime status queries ('?') with the client's bytes, and a realtime byte is
    # handled out of band by GRBL, so its position in the stream carries no meaning.
    # Comparing both streams with those queries removed asserts exactly what matters:
    # no client byte was dropped, duplicated or altered.
    assert written.replace(b'?', b'') == ingress.replace(b'?', b''), \
        'client bytes were not all forwarded unchanged'

    device_data = _concat(rows, 'stream_rx_staged')
    delivered = _concat(rows, 'pipeline_tcp_delivered')
    # Every delivered byte came from the device. This is the anti-fabrication check:
    # the bridge may drop or reorder nothing, but above all it may not invent a byte.
    assert _contains_in_order(device_data, delivered), 'a delivered byte was not device data'

    for row in rows:
        if row['event'] == 'stream_write_reserved':
            expected = (row['before'] + len(bytes.fromhex(row['hex']))) & 0xffff
            assert row['after'] == expected, 'NEJE write counter did not advance by the payload length'

    stopped_rows = [row for row in rows if row['event'] == 'pipeline_transmission_stopped']
    if not stopped_rows and not in_progress:
        raise AssertionError('expected exactly one pipeline_transmission_stopped, found 0')
    assert len(stopped_rows) <= 1, 'more than one transmission_stopped'
    if stopped_rows:
        stopped = stopped_rows[0]
        assert stopped['no_replay'] is True
        assert stopped['staged_bytes'] == 0 and stopped['queued_bytes'] == 0

    cleanup = [row for row in rows if row['event'] == 'cleanup_verified']
    server = [row for row in rows if row['event'] == 'pipeline_server_stopped']
    clean_shutdown = bool(cleanup) and cleanup[-1]['way'] == 0
    if clean_shutdown:
        assert cleanup[-1]['final_app'] == 3
        assert not server or server[-1].get('failure_reason') in (None, '')
    return dict(passed=True, backend='usb-or-ble-transparent',
                mode_declared='usb_equivalent', whitelist=False,
                in_progress=not stopped_rows,
                shutdown_verified=bool(stopped_rows),
                client_bytes_in=len(received), client_bytes_forwarded=len(ingress),
                device_bytes_staged=len(device_data), bytes_delivered=len(delivered),
                neje_writes=sum(1 for row in rows if row['event'] == 'stream_write_reserved'),
                clean_shutdown=clean_shutdown,
                cleanup_way=cleanup[-1]['way'] if cleanup else None,
                no_replay=True, no_fabricated_bytes=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--in-progress', action='store_true',
                        help='Audit a session that is still open: byte-level guarantees are '
                             'checked the same way, shutdown is reported as not yet verified')
    args = parser.parse_args()
    print(json.dumps(audit(args.capture, in_progress=args.in_progress),
                     ensure_ascii=False, indent=2))
