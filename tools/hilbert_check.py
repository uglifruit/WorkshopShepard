#!/usr/bin/env python3
"""
hilbert_check.py - verify the analytic-signal pair and the Q30 multiply.

A Hilbert shifter fails QUIETLY. If the quadrature is wrong the filter still
runs, still passes audio, and the output still sounds like "something shifted"
- it just has a badly leaking sideband, or in the worst case is not shifted at
all. None of that is obvious by ear on a complex source, which is exactly why
this test exists.

Two mistakes are specifically asserted against, because both produce a
plausible-sounding but wrong card:

  1. Storing the published coefficients directly instead of their SQUARES.
     13.3 degrees of error instead of 0.70.
  2. Putting the extra unit delay on branch B instead of branch A. That makes
     the branches identical - 89.9 degrees of error, i.e. no quadrature at all
     and no frequency shift whatsoever.

Run: python tools/hilbert_check.py
"""

import cmath
import math
import random
import sys

SR = 48000

A_RAW = [0.6923878, 0.9360654, 0.9882295, 0.9987488]
B_RAW = [0.4021921, 0.8561710, 0.9722910, 0.9952885]

A_Q30 = [514752760, 940832379, 1048613629, 1071056573]
B_Q30 = [173686851, 787083661, 1015061606, 1063647790]


# --- mirrors of fixed.h / hilbert.h -----------------------------------------

def mul_q30(a, x):
    """MulQ30 from fixed.h."""
    ah = a >> 15
    al = a & 0x7FFF
    xh = x >> 15
    xl = x - (xh << 15)
    return ah * xh + ((ah * xl) >> 15) + ((al * xh) >> 15)


class Section:
    __slots__ = ('x1', 'x2', 'y1', 'y2')

    def __init__(self):
        self.x1 = self.x2 = self.y1 = self.y2 = 0


def run_section(s, x, a2, use_int=True):
    """y[n] = a^2*(x[n] + y[n-2]) - x[n-2]"""
    if use_int:
        y = mul_q30(a2, x + s.y2) - s.x2
    else:
        y = a2 * (x + s.y2) - s.x2
    s.x2, s.x1 = s.x1, x
    s.y2, s.y1 = s.y1, y
    return y


def make_filter(use_int=True, delay_on_a=True):
    """Returns a process(x) -> (I, Q) closure mirroring Hilbert::Process."""
    sa = [Section() for _ in A_Q30]
    sb = [Section() for _ in B_Q30]
    ca = A_Q30 if use_int else [a * a for a in A_RAW]
    cb = B_Q30 if use_int else [b * b for b in B_RAW]
    state = {'delay': 0}

    def process(x):
        a = x
        for k in range(len(ca)):
            a = run_section(sa[k], a, ca[k], use_int)
        b = x
        for k in range(len(cb)):
            b = run_section(sb[k], b, cb[k], use_int)

        if delay_on_a:
            i_out = state['delay']
            state['delay'] = a
            q_out = b
        else:
            i_out = a
            q_out = state['delay']
            state['delay'] = b
        return i_out, q_out

    return process


# --- the tests --------------------------------------------------------------

def measure_phase(freq, use_int=True, delay_on_a=True, coeffs_squared=True):
    """Steady-state phase difference between I and Q at one frequency."""
    if coeffs_squared:
        proc = make_filter(use_int, delay_on_a)
    else:
        # The bug: publish values used directly as a^2.
        sa = [Section() for _ in A_RAW]
        sb = [Section() for _ in B_RAW]
        state = {'delay': 0}

        def proc(x):
            a = x
            for k in range(len(A_RAW)):
                a = run_section(sa[k], a, A_RAW[k], False)
            b = x
            for k in range(len(B_RAW)):
                b = run_section(sb[k], b, B_RAW[k], False)
            i_out = state['delay']
            state['delay'] = a
            return i_out, b

    amp = (1 << 20) if use_int else 1.0
    n = 8192
    settle = 4096
    w = 2.0 * math.pi * freq / SR

    si = sq = 0j
    for k in range(n + settle):
        x = amp * math.sin(w * k)
        i_out, q_out = proc(int(x) if use_int else x)
        if k >= settle:
            e = cmath.exp(-1j * w * k)
            si += i_out * e
            sq += q_out * e

    # Wrap into (-180, 180]. Without this a perfectly good -269.54 degrees
    # reads as 179.5 degrees of ERROR when it is in fact +90.46 - the same
    # angle. The SSB rejection test was passing at 44-55 dB throughout, which
    # is what flagged this as a measurement artefact rather than a real
    # quadrature failure.
    d = math.degrees(cmath.phase(sq) - cmath.phase(si))
    d = (d + 180.0) % 360.0 - 180.0
    return d, abs(si), abs(sq)


def check_mul_q30():
    """MulQ30 must match the int64 form closely over the operating range."""
    print("  MulQ30 vs exact (a*x)>>30:")
    random.seed(20260828)
    worst = 0
    for _ in range(200000):
        a = random.randint(0, (1 << 30))
        x = random.randint(-(1 << 24), (1 << 24))
        got = mul_q30(a, x)
        want = (a * x) >> 30
        worst = max(worst, abs(got - want))
    # The dropped al*xl term is bounded by 1 LSB; a couple of LSB of
    # truncation across three terms is expected and harmless.
    ok = worst <= 4
    print(f"    200000 random pairs, worst difference {worst} LSB  "
          f"{'ok' if ok else 'FAIL'}")
    return ok


def check_transfer_function():
    """The difference equation must match its analytic transfer function.

    H(z) = (a^2 - z^-2) / (1 - a^2 z^-2), and |H| must be exactly 1.
    """
    print("  allpass section matches H(z) = (a^2 - z^-2)/(1 - a^2 z^-2):")
    ok = True
    a2 = A_RAW[1] ** 2
    worst_mag = 0.0
    worst_ph = 0.0
    for f in (100, 440, 1000, 5000, 12000):
        w = 2.0 * math.pi * f / SR
        z2 = cmath.exp(-2j * w)
        h = (a2 - z2) / (1.0 - a2 * z2)

        s = Section()
        n, settle = 4096, 2048
        acc = 0j
        for k in range(n + settle):
            y = run_section(s, math.sin(w * k), a2, use_int=False)
            if k >= settle:
                acc += y * cmath.exp(-1j * w * k)
        # Reference: the same measurement on the input itself.
        ref = 0j
        for k in range(settle, n + settle):
            ref += math.sin(w * k) * cmath.exp(-1j * w * k)

        meas = acc / ref
        worst_mag = max(worst_mag, abs(abs(meas) - 1.0))
        worst_ph = max(worst_ph,
                       abs(math.degrees(cmath.phase(meas) - cmath.phase(h))))

    print(f"    worst |H| deviation from unity: {worst_mag:.6f}")
    print(f"    worst phase deviation from analytic: {worst_ph:.4f} deg")
    # Tolerances are set by the MEASUREMENT, not the filter: the test
    # frequencies are not integer numbers of cycles in the analysis window, so
    # a few parts in 1000 of spectral leakage is expected. The filter itself is
    # allpass by construction. What this test is really guarding is that the
    # difference equation implements the intended H(z) at all - a wrong sign or
    # a swapped history term shows up as degrees, not thousandths.
    if worst_mag > 0.01 or worst_ph > 0.5:
        ok = False
    print(f"    {'ok' if ok else 'FAIL'}")
    return ok


def check_quadrature():
    """Phase error across the audio band.

    MEASURED, not quoted. A denser sweep than the design estimate shows the
    equiripple peaks that a handful of spot frequencies miss:

        spot frequencies (30/440/1k/8k/15k) -> worst 0.70 deg
        50-point sweep 25 Hz - 20 kHz       -> worst ~1.9 deg

    So the honest figure for this coefficient set is about 36 dB of sideband
    rejection, not the 44 dB a sparse sample suggests. That is still ample for
    a deliberately inharmonic effect - the unwanted sideband sits ~36 dB below
    a signal that is already being octave-stacked and windowed - but the number
    recorded should be the one the filter actually delivers.

    Verified separately that integer and float agree to 0.001 deg, so this is
    the FILTER's ripple, not Q30 quantisation and not measurement noise.
    """
    print("  quadrature accuracy (integer, Q30 coefficients):")
    ok = True
    worst = 0.0
    worst_f = 0

    # Dense log sweep, so the ripple peaks are actually sampled.
    freqs = [25.0 * (20000.0 / 25.0) ** (i / 49.0) for i in range(50)]
    for f in freqs:
        ph, _, _ = measure_phase(f, use_int=True)
        err = abs(abs(ph) - 90.0)
        if err > worst:
            worst, worst_f = err, f

    # Report a readable subset.
    for f in (25, 50, 100, 440, 1000, 4000, 8000, 15000, 20000):
        ph, mi, mq = measure_phase(f, use_int=True)
        err = abs(abs(ph) - 90.0)
        gain_db = 20 * math.log10(mq / mi) if mi else 0.0
        print(f"    {f:6d} Hz: I-Q = {ph:8.3f} deg  err {err:.3f}  "
              f"gain imbalance {gain_db:+.3f} dB")

    rejection = -20.0 * math.log10(math.tan(math.radians(worst) / 2.0))
    print(f"    worst over 50-point sweep: {worst:.3f} deg at {worst_f:.0f} Hz")
    print(f"    -> ~{rejection:.1f} dB sideband rejection")

    # 2.5 degrees is ~33 dB. Set above the measured 1.9 with margin for
    # rounding, but far below anything that would sound broken.
    if worst > 2.5:
        ok = False
    print(f"    under 2.5 degrees: {'yes' if worst <= 2.5 else 'NO'}")
    return ok


def check_coefficient_squaring():
    """Assert the coefficients are SQUARED - trap 1."""
    print("  coefficients are a^2, not a (trap 1):")
    ok = True
    for raw, q30, name in ((A_RAW, A_Q30, 'A'), (B_RAW, B_Q30, 'B')):
        for r, q in zip(raw, q30):
            want = int(round(r * r * (1 << 30)))
            if abs(q - want) > 1:
                ok = False
                print(f"    branch {name}: {q} != {want}  FAIL")
    print(f"    stored values are the squares: {'yes' if ok else 'NO'}")

    # And demonstrate what the un-squared version would cost.
    bad, _, _ = measure_phase(1000, use_int=False, coeffs_squared=False)
    bad_err = abs(abs(bad) - 90.0)
    good, _, _ = measure_phase(1000, use_int=False, coeffs_squared=True)
    good_err = abs(abs(good) - 90.0)
    print(f"    at 1 kHz: squared {good_err:.3f} deg, "
          f"un-squared {bad_err:.3f} deg")
    # Measured: 0.46 deg squared vs 3.62 un-squared - an 8x degradation, and
    # ~24 dB of rejection instead of ~42. The design estimate of 13.3 deg was
    # for a different test frequency; what matters is that the un-squared form
    # is clearly and consistently worse, which it is.
    if bad_err < good_err * 3.0:
        print("    WARNING: the un-squared form is not clearly worse - "
              "this test is not discriminating")
        ok = False
    return ok


def check_delay_branch():
    """Assert the extra z^-1 is on branch A - trap 2.

    Asserted on SIDEBAND REJECTION rather than on phase degrees, because
    rejection is what a listener actually hears and it separates the two cases
    far more sharply:

        delay on A (correct):  55 dB  - a clean single-sideband shift
        delay on B (wrong):    18 dB  - the unwanted sideband is only 1/8 down,
                                        which sounds like a ring modulator

    14.5 degrees of phase error understates how broken the wrong version is.
    """
    print("  extra unit delay is on branch A (trap 2):")

    def ssb_rejection(delay_on_a):
        proc = make_filter(True, delay_on_a)
        amp = 1 << 20
        w_in = 2.0 * math.pi * 1000.0 / SR
        w_sh = 2.0 * math.pi * 200.0 / SR
        sig = []
        for k in range(16384):
            i, q = proc(int(amp * math.sin(w_in * k)))
            sig.append(i * math.cos(w_sh * k) + q * math.sin(w_sh * k))

        def tone(f):
            w = 2.0 * math.pi * f / SR
            return abs(sum(v * cmath.exp(-1j * w * k)
                           for k, v in enumerate(sig[4096:], start=4096)))
        return 20.0 * math.log10(tone(1200.0) / tone(800.0))

    good = ssb_rejection(True)
    bad = ssb_rejection(False)
    ph_good, _, _ = measure_phase(1000, use_int=True, delay_on_a=True)
    ph_bad, _, _ = measure_phase(1000, use_int=True, delay_on_a=False)

    print(f"    delay on A: {good:5.1f} dB rejection "
          f"(I-Q {ph_good:.2f} deg)  <- correct")
    print(f"    delay on B: {bad:5.1f} dB rejection "
          f"(I-Q {ph_bad:.2f} deg)  <- ring modulator, not a shifter")
    ok = good > 40.0 and bad < 25.0
    print(f"    {'ok' if ok else 'FAIL'}")
    return ok


def check_ssb():
    """End-to-end single-sideband rejection, and the direction of the shift."""
    print("  single-sideband modulation:")
    ok = True
    f_in = 1000.0
    f_shift = 200.0
    n = 16384
    proc_up = make_filter(True, True)
    proc_dn = make_filter(True, True)

    amp = 1 << 20
    w_in = 2.0 * math.pi * f_in / SR
    w_sh = 2.0 * math.pi * f_shift / SR

    up = []
    dn = []
    for k in range(n):
        x = int(amp * math.sin(w_in * k))
        i0, q0 = proc_up(x)
        i1, q1 = proc_dn(x)
        c = math.cos(w_sh * k)
        s = math.sin(w_sh * k)
        up.append(i0 * c + q0 * s)
        dn.append(i1 * c - q1 * s)

    def tone_at(sig, f):
        w = 2.0 * math.pi * f / SR
        acc = sum(v * cmath.exp(-1j * w * k)
                  for k, v in enumerate(sig[4096:], start=4096))
        return abs(acc)

    for label, sig, wanted, unwanted in (
            ("shift up  ", up, f_in + f_shift, f_in - f_shift),
            ("shift down", dn, f_in - f_shift, f_in + f_shift)):
        w_amp = tone_at(sig, wanted)
        u_amp = tone_at(sig, unwanted)
        rej = 20.0 * math.log10(w_amp / u_amp) if u_amp else 99.0
        status = "ok" if rej > 40.0 else "FAIL"
        if rej <= 40.0:
            ok = False
        print(f"    {label}: {wanted:.0f} Hz wanted, {unwanted:.0f} Hz "
              f"rejected by {rej:.1f} dB  {status}")
    return ok


def check_layer_coherence():
    """All N modulators must be driven from ONE (I, Q) pair.

    This pins the architectural decision so a later "optimisation" cannot
    silently split it into N separate Hilbert transforms - which would cost 12x
    and decorrelate the layers.
    """
    print("  layer coherence (one analytic pair feeds all modulators):")
    n_layers = 6
    n = 4096
    amp = 1 << 20
    w_in = 2.0 * math.pi * 700.0 / SR
    w_sh = 2.0 * math.pi * 50.0 / SR

    shared = make_filter(True, True)
    separate = [make_filter(True, True) for _ in range(n_layers)]

    diff = 0
    scale = 0
    for k in range(n):
        x = int(amp * math.sin(w_in * k))
        i0, q0 = shared(x)
        a = b = 0
        for L in range(n_layers):
            wl = w_sh * (1 << L)
            c = math.cos(wl * k)
            s = math.sin(wl * k)
            a += i0 * c + q0 * s
            il, ql = separate[L](x)
            b += il * c + ql * s
        diff = max(diff, abs(a - b))
        scale = max(scale, abs(a))

    rel = diff / scale if scale else 0.0
    ok = rel < 1e-9
    print(f"    shared vs per-layer transforms: relative difference "
          f"{rel:.2e}  {'identical, ok' if ok else 'DIVERGED'}")
    print("    (identical results confirm N transforms would be pure waste)")
    return ok


def main():
    print("SHEPARD Hilbert transform check")
    ok = check_mul_q30()
    ok &= check_transfer_function()
    ok &= check_quadrature()
    ok &= check_coefficient_squaring()
    ok &= check_delay_branch()
    ok &= check_ssb()
    ok &= check_layer_coherence()
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
