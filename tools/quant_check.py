#!/usr/bin/env python3
"""
quant_check.py - verify scale quantisation, the octave wrap, and the
portamento slew.

Three things here are easy to get wrong in ways that sound like a broken
instrument rather than a broken calculation:

  1. The nearest-degree search across the OCTAVE WRAP. A position at 1150
     cents is nearer to the next octave's root than to 1100, and a plain
     "largest entry <= pos" walk gets that wrong for everything above the last
     degree - so the top of every octave snaps downward and the glide stalls
     once per cycle.

  2. The portamento's SIGNED difference. target - current as an unsigned
     compare sometimes takes the long way round the octave: a one-semitone
     step becomes an eleven-semitone sweep in the wrong direction.

  3. Manual stepping must be monotonic and must never skip a degree.

Run: python tools/quant_check.py
"""

import math
import sys

OCT = 1 << 32

SCALE_SMOOTH, SCALE_CHROMATIC, SCALE_MAJOR, SCALE_PENT = 0, 1, 2, 3

SCALE12 = [0x00000000, 0x15555555, 0x2AAAAAAB, 0x40000000,
           0x55555555, 0x6AAAAAAB, 0x80000000, 0x95555555,
           0xAAAAAAAB, 0xC0000000, 0xD5555555, 0xEAAAAAAB]

SCALE_MAJ = [0x00000000, 0x2AAAAAAB, 0x55555555, 0x6AAAAAAB,
             0x95555555, 0xC0000000, 0xEAAAAAAB]

SCALE_PENT_T = [0x00000000, 0x2AAAAAAB, 0x55555555, 0x95555555, 0xC0000000]

TABLES = {SCALE_CHROMATIC: SCALE12, SCALE_MAJOR: SCALE_MAJ,
          SCALE_PENT: SCALE_PENT_T}

NAMES = {SCALE_CHROMATIC: "12-ET", SCALE_MAJOR: "major", SCALE_PENT: "pentatonic"}

EXPECT_CENTS = {
    SCALE_CHROMATIC: [0, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100],
    SCALE_MAJOR: [0, 200, 400, 500, 700, 900, 1100],
    SCALE_PENT: [0, 200, 400, 700, 900],
}


def q32_to_cents(q):
    return q / OCT * 1200.0


def snap(pos, scale):
    """SnapToScale from shepard.cpp."""
    if scale == SCALE_SMOOTH:
        return pos
    tab = TABLES[scale]
    n = len(tab)

    lo = 0
    for i in range(n):
        if tab[i] <= pos:
            lo = i
        else:
            break

    below = tab[lo]
    above = tab[lo + 1] if lo + 1 < n else tab[0]

    d_below = (pos - below) & 0xFFFFFFFF
    d_above = (above - pos) & 0xFFFFFFFF

    return below if d_below <= d_above else above


def step_scale(pos, scale, direction):
    """StepScale from shepard.cpp."""
    if scale == SCALE_SMOOTH:
        return pos
    tab = TABLES[scale]
    n = len(tab)
    snapped = snap(pos, scale)
    idx = 0
    for i in range(n):
        if tab[i] == snapped:
            idx = i
            break
    idx += direction
    if idx >= n:
        idx -= n
    elif idx < 0:
        idx += n
    return tab[idx]


def check_tables():
    """Every degree must land on its exact cent value."""
    print("  scale tables land on exact cents:")
    ok = True
    for scale, want in EXPECT_CENTS.items():
        tab = TABLES[scale]
        worst = 0.0
        for q, w in zip(tab, want):
            err = abs(q32_to_cents(q) - w)
            worst = max(worst, err)
        status = "ok" if worst < 0.001 else "FAIL"
        if worst >= 0.001:
            ok = False
        print(f"    {NAMES[scale]:11s} {len(tab):2d} degrees, "
              f"worst error {worst:.6f} cents  {status}")
    return ok


def check_wrap():
    """Nearest-degree must be correct ACROSS the octave boundary.

    This is the trap: everything above the last degree is nearer to the next
    octave's root, and a naive walk snaps it downward instead.
    """
    print("  nearest degree across the octave wrap:")
    ok = True
    for scale in (SCALE_CHROMATIC, SCALE_MAJOR, SCALE_PENT):
        tab = TABLES[scale]
        bad = 0
        worst_err = 0.0
        for step in range(4096):
            pos = (step * OCT) // 4096
            got = snap(pos, scale)

            # Brute-force truth, considering the octave above as a candidate.
            cands = list(tab) + [tab[0] + OCT]
            best = min(cands, key=lambda t: abs(t - pos))
            best &= 0xFFFFFFFF

            if got != best:
                bad += 1
                worst_err = max(worst_err,
                                abs(q32_to_cents((got - pos) & 0xFFFFFFFF)))
        status = "ok" if bad == 0 else f"FAIL ({bad} wrong)"
        if bad:
            ok = False
        print(f"    {NAMES[scale]:11s} {status}")
    return ok


def check_slew():
    """The portamento must always take the SHORT way round the octave.

    Mirrors the firmware:
        err = int32(target - current);  current += err >> 6
    The int32 cast is what makes the wrap correct.
    """
    print("  portamento takes the short way round:")
    ok = True

    def to_signed(v):
        v &= 0xFFFFFFFF
        return v - (1 << 32) if v >= (1 << 31) else v

    # Worst case: sitting just below the octave root, snapping up THROUGH the
    # wrap. Unsigned, this slews down through the whole octave.
    cases = [
        ("1150c -> 1200c (wraps to 0)", int(1150 / 1200 * OCT), 0),
        ("50c   -> 0c",                 int(50 / 1200 * OCT),   0),
        ("1100c -> 0c (wrap up)",       int(1100 / 1200 * OCT), 0),
        ("0c    -> 1100c (wrap down)",  0, int(1100 / 1200 * OCT)),
    ]

    for label, start, target in cases:
        cur = start
        max_jump = 0
        for _ in range(400):
            err = to_signed(target - cur)
            max_jump = max(max_jump, abs(q32_to_cents(abs(err))))
            cur = (cur + (err >> 6)) & 0xFFFFFFFF
        final_err = abs(q32_to_cents(abs(to_signed(target - cur))))

        # The path taken must never exceed half an octave - that is what
        # "short way round" means.
        short = max_jump <= 600.5
        settled = final_err < 1.0
        status = "ok" if short and settled else "FAIL"
        if not (short and settled):
            ok = False
        print(f"    {label:30s} max excursion {max_jump:6.1f}c, "
              f"settles to {final_err:.3f}c  {status}")
    return ok


def check_stepping():
    """Manual stepping must be monotonic and must never skip a degree."""
    print("  Pulse In 1 manual stepping:")
    ok = True
    for scale in (SCALE_CHROMATIC, SCALE_MAJOR, SCALE_PENT):
        tab = TABLES[scale]
        n = len(tab)

        # Walk a full octave up; every degree must be visited exactly once.
        pos = tab[0]
        seen = [pos]
        for _ in range(n - 1):
            pos = step_scale(pos, scale, +1)
            seen.append(pos)
        up_ok = sorted(seen) == sorted(tab) and len(set(seen)) == n

        # And back down.
        pos = tab[0]
        for _ in range(n):
            pos = step_scale(pos, scale, -1)
        round_trip = (pos == tab[0])

        status = "ok" if up_ok and round_trip else "FAIL"
        if not (up_ok and round_trip):
            ok = False
        print(f"    {NAMES[scale]:11s} {n:2d} steps visit every degree, "
              f"round trip returns to root  {status}")
    return ok


SR = 48000
DEADZONE_Q15 = 400        # kDeadzone * 8, as applied in UpdateControl
RATE_LINEAR = 6           # kRateLinear
RATE_CUBIC = 150          # kRateCubic


def mul_q15(w, x):
    hi = x >> 15
    lo = x - (hi << 15)
    return w * hi + ((w * lo) >> 15)


def knob_to_rate(pct):
    """The glide rate curve from UpdateControl(), as octaves per second."""
    knob = int(pct / 100 * 4095)
    defl = (knob << 3) - 16384
    if -DEADZONE_Q15 < defl < DEADZONE_Q15:
        defl = 0
    mag = defl if defl >= 0 else -defl
    sq = mul_q15(defl, defl)
    cube = mul_q15(sq, mag)
    rate = mag * RATE_LINEAR + cube * RATE_CUBIC
    if defl < 0:
        rate = -rate
    return rate / (1 << 32) * SR


def check_rate_curve():
    """The glide must reach a genuinely fast sweep at the stops, and the
    knob's middle travel must not be dead.

    Both halves of that are real defects that shipped in the first version:
    a pure cubic curve delivered 0.55 oct/s at full deflection (the comment
    claimed 2), and gave 29 SECONDS per octave at 70% travel - which is not
    distinguishable from stopped.
    """
    print("  glide rate curve:")
    ok = True

    top = knob_to_rate(100)
    mid = knob_to_rate(75)
    low = knob_to_rate(55)

    for pct in (50, 55, 60, 70, 80, 90, 100):
        r = knob_to_rate(pct)
        per = (1 / r) if r else 0
        unit = f"{per:6.2f} s" if per < 60 else f"{per / 60:5.1f} min"
        print(f"    {pct:3d}% travel: {r:7.3f} oct/s   "
              f"{unit if r else '  stopped':>10} per octave")

    # Fast enough at the stops to read as a siren rather than a drift.
    if top < 5.0:
        ok = False
        print(f"    TOO SLOW at full deflection: {top:.2f} oct/s (want >= 5)")
    # Three quarters of the travel must be usefully moving.
    if mid < 0.5:
        ok = False
        print(f"    DEAD MID-TRAVEL: {mid:.2f} oct/s at 75% (want >= 0.5)")
    # But just off centre must still be a slow drift, not a jump.
    if low > 0.5:
        ok = False
        print(f"    TOO COARSE near centre: {low:.2f} oct/s at 55%")

    # Symmetry: the curve must behave identically either side of noon.
    for pct in (60, 75, 90, 100):
        up = knob_to_rate(pct)
        dn = knob_to_rate(100 - pct)
        if abs(abs(up) - abs(dn)) > abs(up) * 0.02:
            ok = False
            print(f"    ASYMMETRIC at {pct}%: {up:.3f} vs {dn:.3f}")

    # The deadzone must actually stop it.
    if knob_to_rate(50) != 0.0:
        ok = False
        print("    DEADZONE does not reach zero")

    print(f"    full deflection {top:.2f} oct/s, 75% travel {mid:.2f} oct/s, "
          f"centre stopped  {'ok' if ok else 'FAIL'}")
    return ok


def check_spiral_shift():
    """SPIRAL's shift must span a full +-12 semitones.

    The first version went through a Q15 semitone intermediate that did not
    land on the pow2 LUT's Q32 octave scale, and spanned +-0.28 SEMITONES -
    a factor of 43 short. The alt-boot would have sounded like a slightly
    detuned echo rather than a shimmer.

    No existing test covered this: spiral_check.py exercises the delay with
    a rate handed to it, so the knob-to-rate mapping was never checked.
    """
    print("  SPIRAL shift range:")
    pow2 = []
    for i in range(257):
        v = (2.0 ** (i / 256.0)) * 1073741824.0
        pow2.append(2147483647 if v >= 2147483647.0 else int(round(v)))

    def pow2_q30(f):
        idx = f >> 24
        frac = (f >> 9) & 0x7FFF
        return pow2[idx] + mul_q15(frac, pow2[idx + 1] - pow2[idx])

    def knob_to_semitones(pct):
        knob = int(pct / 100 * 4095)
        defl = (knob << 3) - 16384
        if -DEADZONE_Q15 < defl < DEADZONE_Q15:
            defl = 0
        oct_q32 = (defl << 18) & 0xFFFFFFFF
        signed = (defl << 18)
        p = pow2_q30(oct_q32)
        rate = (p >> 14) if signed >= 0 else (p >> 15)
        rate = max(16384, min(262144, rate))
        return 12 * math.log2(rate / 65536)

    ok = True
    lo = knob_to_semitones(0)
    hi = knob_to_semitones(100)
    mid = knob_to_semitones(50)

    for pct in (0, 25, 50, 75, 100):
        st = knob_to_semitones(pct)
        print(f"    {pct:3d}% travel: {st:+7.2f} semitones")

    if abs(lo + 12.0) > 0.5:
        ok = False
        print(f"    BOTTOM does not reach -12 st: {lo:+.2f}")
    if abs(hi - 12.0) > 0.5:
        ok = False
        print(f"    TOP does not reach +12 st: {hi:+.2f}")
    if abs(mid) > 0.01:
        ok = False
        print(f"    CENTRE is not unity: {mid:+.4f} st")

    print(f"    spans {lo:+.2f} .. {hi:+.2f} semitones, unity at centre  "
          f"{'ok' if ok else 'FAIL'}")
    return ok


def check_smooth_passthrough():
    """Smooth mode must not quantise at all."""
    print("  smooth mode is a true pass-through:")
    bad = sum(1 for s in range(1000)
              if snap((s * OCT) // 1000, SCALE_SMOOTH) != (s * OCT) // 1000)
    print(f"    {'ok' if bad == 0 else f'FAIL ({bad} altered)'}")
    return bad == 0


def main():
    print("SHEPARD quantisation check")
    ok = check_tables()
    ok &= check_wrap()
    ok &= check_slew()
    ok &= check_stepping()
    ok &= check_smooth_passthrough()
    ok &= check_rate_curve()
    ok &= check_spiral_shift()
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
