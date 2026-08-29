#!/usr/bin/env python3
"""
ping_check.py - verify the alt-boot PING mode.

The pole climbs silently; a trigger voices a snapshot of it, and that snapshot
DECAYS WITHOUT CLIMBING. What has to be true:

  - a struck voice's pitch must not change afterwards (that is the whole idea);
  - two strikes at different moments must give different pitches;
  - the envelope must decay to silence and free its slot;
  - voice stealing must take the OLDEST, so the newest strikes are the ones
    sounding;
  - the attack must not click.

Run: python tools/ping_check.py
"""

import math
import sys

SR = 48000
MAX_LAYERS = 12
PING_VOICES = 4
PING_ATTACK = 6
PING_ENV_SHIFT = 9
PING_ENV_FULL = 32767 << PING_ENV_SHIFT

# Q20 decay coefficients from shepard.cpp - a SHIFT cannot work here, see the
# note in ping.h: env >> 17 is zero for every value a 15-bit envelope holds.
PING_DECAY = [1038030, 1040798, 1042842, 1044350, 1045461, 1046281,
              1046885, 1047330, 1047658, 1047900, 1048078, 1048209,
              1048306, 1048377, 1048429, 1048468, 1048497]
BASE_INC = 1229782

SIN_LUT = [int(round(math.sin(2.0 * math.pi * i / 1024) * 32767.0))
           for i in range(1024)]


def mul_q15(w, x):
    hi = x >> 15
    lo = x - (hi << 15)
    return w * hi + ((w * lo) >> 15)


def sin_q15(phase):
    idx = (phase >> 22) & 1023
    frac = (phase >> 7) & 0x7FFF
    s0 = SIN_LUT[idx]
    s1 = SIN_LUT[(idx + 1) & 1023]
    return s0 + mul_q15(frac, s1 - s0)


class Voice:
    """PingVoice from ping.h."""

    def __init__(self):
        self.phase = [0] * MAX_LAYERS
        self.inc = [0] * MAX_LAYERS
        self.env = 0
        self.target = 0
        self.age = 0
        self.layers = 0
        self.active = False

    def strike(self, layers, inc):
        self.layers = layers
        for i in range(layers):
            self.phase[i] = 0          # zero, so the attack has no step
            self.inc[i] = inc[i]
        self.target = PING_ENV_FULL
        self.active = True
        self.age = 0

    def tick(self, decay_q20):
        if not self.active:
            return
        if self.target > 0:
            self.env += (self.target - self.env) >> PING_ATTACK
            if self.env > PING_ENV_FULL - (PING_ENV_FULL >> 6):
                self.target = 0
        else:
            hi = self.env >> 12
            lo = self.env - (hi << 12)
            self.env = ((hi * decay_q20) >> 8) + ((lo * decay_q20) >> 20)
            if self.env < (8 << PING_ENV_SHIFT):
                self.env = 0
                self.active = False
        self.age += 1

    def osc(self, i):
        self.phase[i] = (self.phase[i] + self.inc[i]) & 0xFFFFFFFF
        return mul_q15(sin_q15(self.phase[i]), self.env >> PING_ENV_SHIFT)


def check_pitch_frozen():
    """A struck voice must not change pitch afterwards.

    THE WHOLE IDEA. If the voice tracked the pole it would climb, which is
    exactly what PING mode exists not to do.
    """
    print("  a struck voice holds its pitch:")
    v = Voice()
    inc = [BASE_INC << (i + 5) for i in range(4)]
    v.strike(4, list(inc))

    # The pole moves on underneath - simulated by changing what WOULD be
    # passed to a new strike. The voice must be unaffected.
    moved = [int(x * 1.5) for x in inc]

    ok = all(v.inc[i] == inc[i] for i in range(4))
    print(f"    increments unchanged after the pole moves: "
          f"{'yes' if ok else 'NO'}")

    # And measure the actual frequency early vs late in the ring.
    #
    # Measured with the envelope HELD, not decaying: the point under test is
    # that the phase increment never changes, and a decaying envelope simply
    # runs the voice out of zero-crossings to count before the late window.
    v.env = PING_ENV_FULL
    v.target = 0
    zc_early = zc_late = 0
    prev = 0
    for k in range(SR // 2):
        s_out = v.osc(0)
        if prev <= 0 < s_out:
            if k < SR // 4:
                zc_early += 1
            else:
                zc_late += 1
        prev = s_out
        v.env = PING_ENV_FULL       # hold - this test is about pitch only
    f_early = zc_early / 0.25
    f_late = zc_late / 0.25
    drift = abs(f_late - f_early)
    print(f"    measured {f_early:.1f} Hz early, {f_late:.1f} Hz late "
          f"(drift {drift:.2f} Hz)")
    if drift > 1.0:
        ok = False
    return ok


def check_strikes_differ():
    """Strikes at different moments must give different pitches.

    That is what makes the invisible pole playable: each trigger lands on
    wherever the glide has reached.
    """
    print("  strikes at different pole positions differ:")
    ok = True
    pitches = []
    for step, mult in enumerate((1.0, 1.19, 1.41, 1.68)):
        v = Voice()
        v.strike(1, [int(BASE_INC * 8 * mult)])
        v.env = PING_ENV_FULL
        v.target = 0
        zc = 0
        prev = 0
        for _ in range(SR // 4):
            s = v.osc(0)
            if prev <= 0 < s:
                zc += 1
            prev = s
            v.tick(PING_DECAY[16])
        pitches.append(zc * 4)

    for i, p in enumerate(pitches):
        print(f"    strike {i}: {p:6.1f} Hz")
    # Each must be clearly distinct from the last.
    for i in range(1, len(pitches)):
        if pitches[i] <= pitches[i - 1] * 1.05:
            ok = False
    print(f"    all distinct and rising: {'yes' if ok else 'NO'}")
    return ok


def check_envelope():
    """The envelope must attack without a step and decay to silence."""
    print("  envelope shape:")
    ok = True
    for idx, label in ((0, "shortest"), (8, "medium"), (16, "longest")):
        shift = PING_DECAY[idx]
        v = Voice()
        v.strike(1, [BASE_INC])
        env = []
        n = 0
        while v.active and n < SR * 20:
            v.tick(shift)
            env.append(v.env)
            n += 1
        peak = max(env) if env else 0
        # Attack: the first sample must not jump straight to peak.
        first = env[0] if env else 0
        secs = n / SR
        print(f"    {label:8s} (step {idx:2d}): peak {peak:5d}, "
              f"first sample {first:5d}, rings {secs:6.3f} s")
        if first > peak // 4:
            ok = False
            print("      ATTACK STEPS - would click")
        if not (0.005 < secs < 19.0):
            ok = False
    return ok


def check_voice_stealing():
    """With all voices busy, the OLDEST must be stolen."""
    print("  voice stealing takes the oldest:")
    voices = [Voice() for _ in range(PING_VOICES)]

    def strike_bank(inc_val):
        slot = -1
        oldest = 0
        for i, v in enumerate(voices):
            if not v.active:
                slot = i
                break
            if v.age >= oldest:
                oldest = v.age
                slot = i
        voices[slot].strike(1, [inc_val])
        return slot

    used = []
    for n in range(PING_VOICES):
        used.append(strike_bank(BASE_INC * (n + 1)))
        for _ in range(50):       # let each strike age before the next
            for v in voices:
                v.tick(PING_DECAY[16])
    print(f"    first {PING_VOICES} strikes -> slots {used}")
    ok = sorted(used) == list(range(PING_VOICES))

    # The fifth must steal slot 0, the oldest.
    stolen = strike_bank(BASE_INC * 99)
    print(f"    fifth strike steals slot {stolen} "
          f"({'oldest, ok' if stolen == 0 else 'NOT the oldest'})")
    ok &= (stolen == 0)
    return ok


def check_polyphony_headroom():
    """All voices sounding at once must not overflow the accumulator."""
    print("  headroom with every voice ringing:")
    inv_sqrt = {12: 9459}
    def hann(u):
        return math.sin(math.pi * u) ** 2

    ok = True
    for N, inv in ((3, 18919), (8, 11585), (12, 9459)):
        voices = [Voice() for _ in range(PING_VOICES)]
        wins = []
        for vi, v in enumerate(voices):
            m = vi * 0.25          # struck at different pole positions
            v.strike(N, [BASE_INC << i for i in range(N)])
            v.env = PING_ENV_FULL
            v.target = 0
            wins.append([int(hann(((m + i) / N) % 1.0) * 32767)
                         for i in range(N)])
        peak = 0
        for _ in range(3000):
            acc = 0
            for vi, v in enumerate(voices):
                for i in range(N):
                    acc += mul_q15(v.osc(i), wins[vi][i])
                v.tick(PING_DECAY[16])
            # >> 1 is the polyphony normalisation - 1/sqrt(N) covers layers,
            # not voices. Without it this clips at every layer count.
            out = (mul_q15(acc, inv) >> 5) >> 1
            peak = max(peak, abs(out))
        status = "ok" if peak < 2047 else "CLIPS"
        if peak >= 2047:
            ok = False
        print(f"    N={N:2d}: worst-case peak {peak:5d}  {status}")
    print("    (soft knee 1450, rail 2047)")
    return ok


def main():
    print(f"SHEPARD PING check: {PING_VOICES} voices")
    ok = check_pitch_frozen()
    ok &= check_strikes_differ()
    ok &= check_envelope()
    ok &= check_voice_stealing()
    ok &= check_polyphony_headroom()
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
