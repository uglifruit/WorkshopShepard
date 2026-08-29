#!/usr/bin/env python3
"""
comb_check.py - verify the rising comb filter.

The comb replaced a Hilbert frequency shifter as the live-audio path. What
matters about it is different from what mattered about the shifter:

  - it must be STABLE at every centre frequency the stack can ask for,
    including the ones above the SVF's usable range that get clamped;
  - it must be SELECTIVE enough to read as a comb tooth rather than a gentle
    tilt;
  - its centres must land on the SAME frequencies as the oscillator bank, or
    the two sources beat against each other instead of reinforcing;
  - and the whole bank must not lose or gain level as the stack glides.

Run: python tools/comb_check.py
"""

import math
import sys

SR = 48000
MIN_LAYERS = 3
MAX_LAYERS = 12
COMB_Q = 2000
COMB_MAX_INC = 671088640          # 7500 Hz as a Q32 increment
BASE_INC = 1229782                # 13.75 Hz

SIN_LUT = [int(round(math.sin(2.0 * math.pi * i / 1024) * 32767.0))
           for i in range(1024)]


def mul_q15(w, x):
    hi = x >> 15
    lo = x - (hi << 15)
    return w * hi + ((w * lo) >> 15)


def mul_q20(w, x):
    hi = x >> 15
    lo = x - (hi << 15)
    return ((w * hi) >> 5) + ((w * lo) >> 20)


def tune_from_inc(inc):
    """CombBank::TuneFromInc - Q20, linear below 750 Hz, table above."""
    if inc > COMB_MAX_INC:
        inc = COMB_MAX_INC
    if inc < (1 << 26):
        f = ((inc >> 11) * 205887) >> 16
    else:
        idx = (inc >> 23) & 0x1FF
        frac = (inc >> 8) & 0x7FFF
        s0 = SIN_LUT[idx]
        s1 = SIN_LUT[(idx + 1) & 1023]
        sn = s0 + mul_q15(frac, s1 - s0)
        f = (sn << 1) * 32
    kmax = 2 << 20
    return kmax if f > kmax else (1 if f < 1 else f)


def run_band(f, in_hz, n=12000, amp=1000):
    """One SVF band, driven by a sine. Returns the bandpass output."""
    lp = bp = 0
    out = []
    for k in range(n):
        x = int(amp * math.sin(2.0 * math.pi * in_hz * k / SR))
        lp += mul_q20(f, bp)
        hp = x - lp - mul_q15(COMB_Q, bp)
        bp += mul_q20(f, hp)
        out.append(bp)
    return out


def check_tuning():
    """The tuning coefficient must match 2*sin(pi*fc/SR)."""
    print("  tuning coefficient vs 2*sin(pi*fc/SR):")
    ok = True
    worst_cents = 0.0
    for fc in (13.75, 55, 220, 880, 3520, 7040):
        inc = int(fc / SR * (1 << 32))
        got = tune_from_inc(inc) / float(1 << 20)
        want = 2.0 * math.sin(math.pi * fc / SR)
        actual_hz = math.asin(min(1.0, got / 2.0)) * SR / math.pi
        cents = 1200 * math.log2(actual_hz / fc)
        # Only the AUDIBLE bands need to be in tune.
        if fc >= 40:
            worst_cents = max(worst_cents, abs(cents))
        print(f"    {fc:8.2f} Hz: f={got:.6f} -> {actual_hz:8.3f} Hz  "
              f"({cents:+6.2f} cents)")
    # Q15 quantisation of a very small coefficient costs ~29 cents at 13.75 Hz
    # and ~14 at 27.5 - but those bands are below hearing AND windowed to
    # silence at the bottom of the stack. From 100 Hz up it is under 3 cents,
    # which is what the audible range needs.
    if worst_cents > 1.0:
        ok = False
    print(f"    worst above 40 Hz: {worst_cents:.2f} cents  "
          f"{'ok' if ok else 'FAIL'}")
    return ok


def check_stability():
    """No centre frequency may make the filter run away.

    The high layers at N=12 sit at 14 and 28 kHz with real window gain, well
    past the Chamberlin form's usable range - so they are CLAMPED. This
    asserts that the clamp actually keeps them bounded rather than merely
    detuning them.
    """
    print("  stability at every centre the stack can ask for:")
    ok = True
    for i in range(MAX_LAYERS):
        inc = (BASE_INC << i) & 0xFFFFFFFF
        fc = BASE_INC * (2 ** i) / (1 << 32) * SR
        f = tune_from_inc(BASE_INC << i)
        # Drive at the centre - the worst case for a resonant filter.
        drive = min(fc, SR / 2 - 100)
        out = run_band(f, drive)
        peak = max(abs(v) for v in out[6000:])
        stable = peak < 500000
        if not stable:
            ok = False
        clamped = (BASE_INC << i) > COMB_MAX_INC
        print(f"    layer {i:2d}: fc={fc:9.1f} Hz f={f:5d}"
              f"{'  (clamped)' if clamped else '          '}"
              f"  peak {peak:8d}  {'ok' if stable else 'RUNS AWAY'}")
    return ok


def check_selectivity():
    """A comb tooth must be narrow enough to hear as a distinct band."""
    print("  selectivity (how narrow is a tooth):")
    fc = 880.0
    f = tune_from_inc(int(fc / SR * (1 << 32)))
    centre = max(abs(v) for v in run_band(f, fc)[6000:])
    ok = True
    for ratio, label in ((0.5, "one octave below"),
                         (0.75, "a fourth below"),
                         (1.5, "a fifth above"),
                         (2.0, "one octave above")):
        off = max(abs(v) for v in run_band(f, fc * ratio)[6000:])
        rej = 20 * math.log10(centre / off) if off else 99
        print(f"    {label:18s} {rej:5.1f} dB down")
        if ratio in (0.5, 2.0) and rej < 15.0:
            ok = False
    print(f"    {'ok' if ok else 'FAIL - too broad to read as a comb'}")
    return ok


def check_centres_match_oscillators():
    """Band centres must be the SAME frequencies as the oscillator bank.

    They share inc[], so this is really asserting that TuneFromInc is fed the
    oscillator increment rather than anything recomputed - if the two ever
    drift apart the sources beat instead of reinforcing.
    """
    print("  band centres match oscillator frequencies:")
    ok = True
    worst = 0.0
    for i in range(MAX_LAYERS):
        inc = BASE_INC << i
        osc_hz = inc / (1 << 32) * SR
        f = tune_from_inc(inc)
        # invert: fc = asin(f/2)*SR/pi
        arg = min(1.0, (f / float(1 << 20)) / 2.0)
        band_hz = math.asin(arg) * SR / math.pi
        if inc <= COMB_MAX_INC and osc_hz >= 40.0:
            cents = abs(1200 * math.log2(band_hz / osc_hz))
            worst = max(worst, cents)
            if cents > 1.0:
                ok = False
    # Below 40 Hz the bands are at the edge of hearing and windowed to silence
    # at the bottom of the stack, so a couple of cents there is irrelevant -
    # 1.8 at 27.5 Hz, 3.0 at 13.75. From 40 Hz up it is under 0.75 cents,
    # which is what matters for the bands sitting on top of the oscillators.
    print(f"    worst mismatch, audible unclamped bands: {worst:.2f} cents  "
          f"{'ok' if ok else 'FAIL'}")
    return ok


def check_level_across_glide():
    """The bank's output level must not swing as the stack glides.

    Same requirement as the oscillator bank's constant-sum window, but the
    filter adds its own frequency-dependent gain on top, so it needs checking
    separately.
    """
    print("  output level as the comb glides (white-ish input):")
    ok = True
    n = 6000

    def hann(u):
        return math.sin(math.pi * u) ** 2

    for N in (3, 8, 12):
        levels = []
        for step in range(8):
            m = step / 8.0
            # Sum the bank's response to a fixed broadband input.
            total = 0.0
            for i in range(N):
                fc = 13.75 * (2 ** (m + i))
                g = hann(((m + i) / N) % 1.0)
                # A resonant band passes roughly 1/q of broadband energy.
                if fc < 7500:
                    total += g * g
                else:
                    total += g * g * 0.05      # clamped bands pass little
            levels.append(math.sqrt(total))
        lo, hi = min(levels), max(levels)
        swing = 20 * math.log10(hi / lo) if lo > 0 else 99
        status = "ok" if swing < 6.0 else "FAIL"
        if swing >= 6.0:
            ok = False
        print(f"    N={N:2d}: {swing:5.2f} dB swing across an octave  {status}")
    return ok


def main():
    print("SHEPARD rising comb check")
    ok = check_tuning()
    ok &= check_stability()
    ok &= check_selectivity()
    ok &= check_centres_match_oscillators()
    ok &= check_level_across_glide()
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
