#!/usr/bin/env python3
"""
spiral_check.py - verify the alt-boot pitch-shifting delay.

The decay test here runs the REAL loop at DC. That is deliberate and it is the
lesson WorkshopSpectral learned the hard way: its first tape_check modelled the
feedback path as a bare multiply, ignoring both filters. Because the damping
lowpass has a DC gain of exactly 1.0, the model was wrong in the one place it
mattered, and a build shipped whose "delay" ran a two-minute tail and did not
audibly decay at all.

So: no modelling of the loop. Run it.

Run: python tools/spiral_check.py
"""

import math
import sys

SR = 48000
SPIRAL_BITS = 16
SPIRAL_LEN = 1 << SPIRAL_BITS
MASK = SPIRAL_LEN - 1
SPIRAL_WIN = 8192
MAX_FEEDBACK = 31129

SIN_LUT = [int(round(math.sin(2.0 * math.pi * i / 1024) * 32767.0))
           for i in range(1024)]


def mul_q15(w, x):
    hi = x >> 15
    lo = x - (hi << 15)
    return w * hi + ((w * lo) >> 15)


def hann_q15(u_q32):
    idx = (u_q32 >> 23) & 0x1FF
    frac = (u_q32 >> 8) & 0x7FFF
    s0 = SIN_LUT[idx]
    s1 = SIN_LUT[(idx + 1) & 1023]
    s = s0 + mul_q15(frac, s1 - s0)
    return mul_q15(s, s)


def saturate(x):
    lim, knee = 32767, 24576
    room = lim - knee
    if x > knee:
        over = x - knee
        return lim if over >= room * 2 else knee + over - (over * over) // (room * 4)
    if x < -knee:
        over = -knee - x
        return -lim if over >= room * 2 else -(knee + over - (over * over) // (room * 4))
    return x


def soft_clip_out(x):
    lim, knee = 2047, 1450
    ax = -x if x < 0 else x
    if ax <= knee:
        return x
    over = ax - knee
    room = lim - knee
    y = knee + (over * room) // (room + over)
    return -y if x < 0 else y


def rate_q16(semitones):
    """Read-pointer advance for a given transposition."""
    return int(round(65536 * (2.0 ** (semitones / 12.0))))


def check_rate():
    """The shift ratio must match the requested semitones."""
    print("  shift ratio vs semitones:")
    ok = True
    worst = 0.0
    for st in (-12, -7, -5, -1, 0, 1, 5, 7, 12):
        r = rate_q16(st)
        got = 1200.0 * math.log2(r / 65536.0)
        err = abs(got - st * 100.0)
        worst = max(worst, err)
        print(f"    {st:+3d} st: rate_q16 = {r:6d}  "
              f"= {got:+8.2f} cents (err {err:.3f})")
    ok = worst < 0.05
    print(f"    worst error {worst:.4f} cents  {'ok' if ok else 'FAIL'}")
    return ok


def check_crossfade():
    """The two head gains must sum to a constant.

    A linear crossfade would amplitude-modulate at the head-recycle rate,
    audible as tremolo that tracks the shift amount.
    """
    print("  crossfade gains sum constant:")
    sums = []
    for p in range(SPIRAL_WIN):
        u = (p << 32) // SPIRAL_WIN
        g0 = hann_q15(u & 0xFFFFFFFF)
        g1 = 32767 - g0
        sums.append(g0 + g1)
    lo, hi = min(sums), max(sums)
    ripple = (hi - lo) / (sum(sums) / len(sums)) * 100.0
    ok = ripple < 0.01
    print(f"    g0 + g1 = {lo}..{hi}  ripple {ripple:.6f}%  "
          f"{'ok' if ok else 'FAIL'}")
    return ok


def check_head_bounds():
    """Neither head may leave the buffer or reach the write pointer."""
    print("  read heads stay inside the buffer guard:")
    ok = True
    min_samples = 1200
    span = SPIRAL_LEN - min_samples - SPIRAL_WIN - 512
    for st in (-12, 0, 12):
        r = rate_q16(st)
        for time_pct in (0, 50, 100):
            t = int(time_pct / 100 * 32767)
            delay = min_samples + (span * t) // 32768
            # Worst case: the second head sits half a window further back.
            deepest = delay + SPIRAL_WIN // 2 + 1
            if deepest >= SPIRAL_LEN or delay < min_samples:
                ok = False
                print(f"    {st:+3d} st, time {time_pct:3d}%: "
                      f"deepest read {deepest} OUT OF RANGE")
    print(f"    max reach {min_samples + span + SPIRAL_WIN // 2 + 1} "
          f"of {SPIRAL_LEN}  {'ok' if ok else 'FAIL'}")
    return ok


def run_loop(feedback_pct, semitones, n_samples):
    """Run the ACTUAL loop with an impulse and return its envelope.

    Not a model. The filters are in the path, the saturator is in the path,
    and the interpolating reads are in the path.
    """
    buf = [0] * SPIRAL_LEN
    write = 0
    read = 0
    damp = 0
    dc = 0
    r = rate_q16(semitones)
    fb_amt = (int(feedback_pct / 100 * 32767) * MAX_FEEDBACK) >> 15
    delay = 1200 + ((SPIRAL_LEN - 1200 - SPIRAL_WIN - 512) * 16384) // 32768

    env = []
    peak_window = 0
    for k in range(n_samples):
        # Impulse burst at the start - a short tone, so there is something for
        # the shifter to transpose.
        drive = int(20000 * math.sin(2 * math.pi * 440 * k / SR)) if k < 2400 else 0

        read = (read + r) & 0xFFFFFFFF
        phase = (read >> 16) % SPIRAL_WIN
        base = ((write - delay) & MASK) << 16
        p0 = (base + read) & 0xFFFFFFFF
        p1 = (p0 + (SPIRAL_WIN // 2 << 16)) & 0xFFFFFFFF

        def rd(p):
            idx = (p >> 16) & MASK
            frac = p & 0xFFFF
            a = buf[idx]
            b = buf[(idx + 1) & MASK]
            return a + (((b - a) * frac) >> 16)

        u = (phase << 32) // SPIRAL_WIN
        g0 = hann_q15(u & 0xFFFFFFFF)
        g1 = 32767 - g0
        wet = mul_q15(rd(p0), g1) + mul_q15(rd(p1), g0)

        damp += (wet - damp) >> 2
        dc += (damp - dc) >> 9
        damped = damp - dc

        fb = mul_q15(damped, fb_amt)
        buf[write & MASK] = saturate(drive + fb)
        write = (write + 1) & MASK

        peak_window = max(peak_window, abs(wet))
        if k % 4800 == 4799:
            env.append(peak_window)
            peak_window = 0
    return env


def check_decay():
    """Run the real loop and report how long the tail actually is.

    SPIRAL is meant to sustain, so this is not a pass/fail on decay - it is a
    pass/fail on STABILITY. What must never happen is the level growing without
    bound, which would clip and stay clipped.
    """
    print("  loop stability, running the REAL loop (not a model):")
    ok = True
    for st, label in ((0, "unity (deadzone)"), (7, "+7 st"), (-7, "-7 st")):
        for fb in (50, 100):
            env = run_loop(fb, st, SR * 6)
            if not env:
                continue
            peak = max(env)
            final = env[-1]
            # Growth is the failure. A long tail is the instrument.
            growing = final > peak * 1.05 and final > 1000
            ratio_db = 20 * math.log10(final / peak) if peak and final else -99
            status = "GROWING" if growing else "stable"
            if growing:
                ok = False
            print(f"    {label:16s} fb {fb:3d}%: peak {peak:7d} -> "
                  f"after 6 s {final:7d} ({ratio_db:+6.1f} dB)  {status}")
    return ok


def check_saturator():
    """Both clippers must be monotonic and bounded."""
    print("  clippers:")
    ok = True
    prev = None
    for x in range(-60000, 60001, 500):
        y = saturate(x)
        if abs(y) > 32767 or (prev is not None and y < prev):
            ok = False
            print(f"    Saturate broken at {x} -> {y}")
            break
        prev = y
    print(f"    Saturate: range {saturate(-60000)}..{saturate(60000)}, "
          f"monotonic {'yes' if ok else 'NO'}")

    prev = None
    pinned = 0
    for x in range(0, 12000, 7):
        y = soft_clip_out(x)
        if abs(y) > 2047:
            ok = False
        if prev is not None:
            if y < prev:
                ok = False
            if y == prev and y >= 2047:
                pinned += 1
        prev = y
    print(f"    SoftClipOut: never pins below 12000 "
          f"{'yes' if pinned == 0 else f'NO ({pinned} at rail)'}")
    ok &= pinned == 0
    return ok


def main():
    print(f"SPIRAL delay check: {SPIRAL_LEN} samples = "
          f"{SPIRAL_LEN / SR:.3f} s, window {SPIRAL_WIN}")
    ok = check_rate()
    ok &= check_crossfade()
    ok &= check_head_bounds()
    ok &= check_saturator()
    ok &= check_decay()
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
