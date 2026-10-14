"""Transparent client policy: forward GRBL bytes verbatim, match nothing.

This is the policy behind the opt-in transparent bridge mode. It exists so
LightBurn can use the link the way it uses a direct USB cable: any G-code, jogging,
homing and settings writes all pass. Nothing is compared against a reviewed job and
no whitelist applies.

What it still refuses to do is fabricate: every byte handed out here is a client byte
on its way to the controller, in order, exactly once. Realtime bytes stay realtime so
`?`, `!`, `~` and the soft reset keep their out-of-band meaning.

Because the pre-review gate is gone, the review guarantees do not apply in this mode;
see docs/transparent-bridge-plan.md for what that costs.
"""
import re

MAX_LINE_BYTES = 512
REALTIME = {ord('!'): 'hold', ord('~'): 'resume'}

# This firmware is GRBL 1.1f.NEJE, not upstream GRBL, and its jog semantics were
# measured to be unsafe through this link: a commanded 1 mm jog ($J=Y-1F100) ran away
# for at least 168 mm in Jog state until the controller restarted. The readings match
# the jog being carried out as an ABSOLUTE target rather than a relative offset: from
# X0 an absolute "move to X=1" is indistinguishable from a relative +1 mm, which is why
# $J=X1F100 looked correct, while from Y400 an absolute "move to Y=-1" is a 401 mm move.
#
# So a jog is not refused outright and it is not forwarded blindly: it is translated
# into the single explicit relative move it was always meant to be. One line in, one ok
# out, so the sender's flow control still holds, and the move cannot run away because a
# relative displacement is bounded by construction. Anything that is not the simple
# axis/feed form is still refused, because a jog carrying modal or settings words is not
# something this bridge can reason about.
JOG_PREFIX = b'$J='
MAX_JOG_MM = 100.0
MAX_JOG_FEED = 6000.0
BARE_WORD = re.compile(rb'([A-Za-z])(-?\d+(?:\.\d+)?)')


def translate_jog(compact):
    """Translate `$J=` into the single explicit relative move it always meant.

    Raises ValueError for anything that is not the plain axis/feed form: a jog carrying
    modal or settings words is not something this bridge can reason about, and guessing
    there is how the runaway happened.
    """
    body = compact[len(JOG_PREFIX):]
    axes, feed, position = [], None, 0
    for match in BARE_WORD.finditer(body):
        if match.start() != position:
            raise ValueError('Jog carries something other than axis/feed words')
        position = match.end()
        letter, value = match.group(1).upper(), float(match.group(2))
        if letter in (b'X', b'Y', b'Z'):
            if abs(value) > MAX_JOG_MM:
                raise ValueError(f'Jog step exceeds {MAX_JOG_MM:g} mm')
            axes.append((letter, value))
        elif letter == b'F':
            feed = value
        else:
            raise ValueError('Jog carries a modal or settings word')
    if position != len(body) or not axes:
        raise ValueError('Jog has no usable axis word')
    if feed is None or not 0 < feed <= MAX_JOG_FEED:
        raise ValueError('Jog needs a feed within range')
    words = b' '.join(b'%s%g' % (letter, value) for letter, value in axes)
    return b'G91 G1 ' + words + b' F%g\n' % feed


class PassthroughPolicy:
    def __init__(self):
        self.pending = bytearray()
        self.failed = False
        self.forwarded_bytes = 0

    def feed(self, data):
        if self.failed:
            raise ValueError('Policy fault is latched; no further input accepted')
        result = []
        try:
            for byte in data:
                if byte == ord('?'):
                    result.append(('status', b'?'))
                    self.forwarded_bytes += 1
                    continue
                if byte in REALTIME:
                    result.append((REALTIME[byte], bytes([byte])))
                    self.forwarded_bytes += 1
                    continue
                if byte == 0x18:
                    result.append(('cancel', b'\x18'))
                    self.forwarded_bytes += 1
                    continue
                self.pending.append(byte)
                if len(self.pending) > MAX_LINE_BYTES:
                    raise ValueError('Input line exceeds the transparent bridge limit')
                if byte != 10:
                    continue
                wire = bytes(self.pending)
                self.pending.clear()
                compact = re.sub(rb'\s+', b'', wire)
                if compact.upper().startswith(JOG_PREFIX):
                    # One line in, one line out: the sender still gets exactly one ok.
                    result.append(('ordinary', translate_jog(compact)))
                else:
                    result.append(('ordinary', wire))
                self.forwarded_bytes += len(wire)
        except ValueError:
            self.failed = True
            raise
        return result

    def eof(self):
        # Forwarding is line-by-line, so a truncated final line is simply never
        # forwarded. It is reported rather than completed or replayed.
        leftover = bytes(self.pending)
        self.pending.clear()
        return leftover
