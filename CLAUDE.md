# SHEPARD — working notes for Claude Code

A program card for the **Music Thing Modular Workshop System Computer**
(RP2040), built on the header-only **ComputerCard** library. Sibling project to
`../WorkshopSpectral`, `../WorkshopZX`, `../WorkshopBio`, `../Workshop2D2` —
reuse their conventions where they fit.

**SHEPARD generates Shepard–Risset tones**: octave-spaced components sliding
through a fixed spectral envelope, so pitch appears to rise or fall forever.

## Current status: v0.1.1, FLASHED ONCE, three hardware bugs fixed

`build/shepard.uf2` — **1.28% flash, 55.95% RAM**. All five host test suites
pass.

**It has been on hardware once.** That session found three bugs (below), all in
the control path, all invisible to a green test suite. They are fixed but the
fixes are NOT yet confirmed by ear — the card needs reflashing and listening to
again before anything here is called working.

## The bugs found before hardware, and why they matter

All three were found on the host, before anything reached hardware. They are
the argument for keeping this discipline — and the third is a reminder that a
passing test suite is not the same as a checked card.

### 1. The window lookup was truncated, not interpolated

`shepard_check.py::check_window_sum` failed on its very first run with a
diagnostic pattern: **even layer counts passed, odd ones failed.**

    truncated:     N even -> 0.006% ripple,  N odd -> up to 0.48%
    interpolated:  every N -> 0.012%, the integer rounding floor

At even N the layer positions land on exact table entries and the truncation
error cancels between them; at odd N they fall between entries and it does not.

A truncated version would have tested clean at N=4, 6, 8 and pulsed audibly at
N=3, 5, 7 — which on hardware reads as *"the illusion only works at some
densities"* and is very hard to attribute to a window lookup.

**Do not remove the interpolation in `HannQ15`.** It costs ~6 cycles, at
control rate, once per layer.

### 2. The output ran 4 dB too hot

`passthru_check.py` measured peaks of 1754 against a 2047 rail, with **2–10% of
samples above the soft knee** — the card would have sat permanently in soft
clip. The output shift was `>> 4`; it is now `>> 5`, giving ~7 dB of headroom
at rms ~443, and the clipper now engages 0.000% of the time.

This is exactly the class of bug that sank a WorkshopSpectral build (512× too
loud, shipped to hardware, every *modelled* test passing). **Re-run
`passthru_check.py` after any change to a shift, a gain, or a normalisation.**

### 3. Two knob mappings that never reached their range

Found by playing with the numbers rather than by a test, which is why both now
have one (`quant_check.py::check_rate_curve` and `::check_spiral_shift`).

**The glide was 15× too slow.** A pure cubic curve, `rate = defl³ * 12`. Two
`MulQ15`s leave a full-scale cube at 4096 rather than 2³¹, so full deflection
gave **0.55 oct/s against a comment claiming 2**. The shape was wrong as well
as the scale: at 70% travel it was 29 *seconds* per octave, indistinguishable
from stopped, so most of the knob did nothing.

Now `mag * 6 + cube * 150` — a linear term to keep the mid-travel alive and a
cubic term to reach the stops:

    55% travel   0.12 oct/s     8.6 s per octave   a slow drift
    70%          0.88           1.1 s              clearly moving
    85%          3.1            0.32 s             a fast sweep
   100%          8.0            0.13 s             a siren

**SPIRAL's shift spanned ±0.28 semitones instead of ±12.** A factor of 43. The
mapping went through a Q15 semitone intermediate (`(defl * 12) >> 4` then
`<< 13`) that does not land on the pow2 LUT's Q32 octave scale at all. The
alt-boot would have sounded like a slightly detuned echo rather than a
shimmer — plausible enough to be blamed on the delay rather than on the knob.

Deflection is ±16384 and one octave is 2³² on that scale, so the conversion is
simply `defl << 18`.

**Neither was caught by the existing tests**, and the reason is worth
remembering: `spiral_check.py` exercises `SpiralDelay::Process` with a rate
handed to it, so the knob-to-rate mapping was never in the loop. A unit test
that starts *downstream of the control path* cannot see a broken control path.

## Found on HARDWARE — thirteen findings, three of them my own fixes making things worse

The first two listening sessions. Every host suite was green throughout, which
is the point: three were in the control path, and the fourth hid behind an
invariant that was true but insufficient.

### 1. The glide ran 32× too slow, in audible steps

Reported as *"the pitch climbs, then PLATEAUS, then there's a click and it
resets"*, at every layer count.

`rate_q32_` is a PER-SAMPLE increment, but `master_free_q32_ += rate_q32_` sits
in `UpdateControl()`, which runs once per 32 samples. So the glide was 32× slow
**and** the master moved in 32-sample steps — the plateau is the phase barely
advancing, the click is the accumulated jump arriving at once.

It hid from everything because the window AND the layer frequencies both derive
from the master, so they stayed perfectly consistent with each other. The
illusion still half-worked; only the *rate* was wrong, and nothing measured
absolute rate end to end. `quant_check.py::check_master_advance` now does, and
also asserts the per-block pitch step stays under a couple of cents.

Fix: `master_free_q32_ += rate_q32_ * (kControlMask + 1)`.

### 2. Switching to page 2 silenced the card

`level_arrival_` was captured from `stored_[1][2]`, which on the first visit
still holds the constructor default (32767) rather than where Y is pointing.
The next knob read then looked like a huge movement, `level_live_` latched
immediately, and the level jumped to Y's physical position — silence with Y
anticlockwise.

The "hold until moved" protection did exactly the opposite of its purpose. Fix:
capture from `KnobVal(Knob::Y)`, the live ADC reading, which is correct on the
first visit as well as later ones.

### 3. The live-audio blend was 24 dB too quiet

The input is `<< 9` for the Hilbert filter's precision, and the modulator then
did `>> 9` — exactly undoing it. So the live path entered the crossfade at its
raw ±2048 while `SinQ15` delivered ±32767. Measured: live-only rms 42 against
the synth's 443.

`passthru_check.py` never caught it because it only ever exercised the synth
path. Fix: `>> 5`, which lands ±2^20 at full Q15, plus `>> 1` for the quadrature
sum. Live-only is now rms 336 against 443 — within 2.4 dB.

### 4. The BIG click at the loop: oscillator phases did not rotate

Reported as *"a BIG click when it loops — it sounds like the new overtone
coming in at full volume"*. That description is what solved it.

Crossing an octave boundary RELABELS the stack: layer *i* inherits the window
gain that layer *i−1* had, and inherits its frequency too, since
`inc[i] = inc0 << i` and `inc0` has just halved. **The oscillator phases do not
relabel on their own.** So every layer kept its own phase while taking on a
different neighbour's gain and frequency, and the summed waveform stepped —
every octave, at the loop.

Measured: largest single-sample step **128 without the rotation, 67 with it**,
against a global maximum of 68. The loop goes from being the single largest
discontinuity in the signal to being indistinguishable from ordinary slew.

**The window was innocent.** Its sum is constant and its layout is correct;
both remain true whether the phases rotate or not. That is why every window
assertion stayed green while the card clicked audibly. Closed by
`shepard_check.py::check_phase_rotation`, which measures the summed step
directly: 24610 without rotation, 1 with.

Worth recording that I chased the window first — a free-running window phase,
then a "unified" phase design — and both were wrong turns. The user's
description of the SOUND ("new overtone at full volume") pointed at a layer
entering wrongly, which is where the answer was.

### 5. The illusion's own perceptual limit — NOT a bug

Reported as *"I can hear it sirening up and down, especially at pace"* and then,
decisively, *"at faster than a period of about 1 second I hear repeated
climbing lines"*.

That second observation is the answer, and it is not a defect. **A Shepard tone
only works while the listener cannot track the octave cycle.** Above roughly
1 oct/s the cycle repeats once a second or faster and the ear stops hearing
"endlessly rising" and starts hearing "a climbing line, repeated" — because
that is exactly what it is. Every implementation has this limit; Risset's
originals run at about 0.1–0.3 oct/s.

The real fault was mine in the knob mapping: the cubic curve put 1 oct/s at
**70% of travel**, so half the knob was past where the effect survives. That
came from over-correcting an earlier "not fast enough at the extremes" note.

Now a **seventh** power, `mag*3 + defl⁷*2600`:

    52%   0.02 oct/s     46 s per octave
    70%   0.22           4.6 s
    85%   0.97           1.0 s        <- the perceptual edge
    92%   2.6            0.38 s       siren
   100%   7.9            0.13 s       siren

86% of the travel now sits inside the illusion, with the top for deliberate
sirens. `check_rate_curve` asserts that fraction directly, so the constraint
is recorded as perceptual rather than numerical.

**Window ripple was ruled out first**: amplitude sum, power sum and A-weighted
loudness are all flat to 0.00% at every layer count. The swing that did show up
in rendered audio (2.3 dB at N=3) is scattered rather than octave-periodic —
ordinary beating between three widely spaced oscillators.

### 6. Density stepped instead of fading

X quantised to an integer layer count, so a new oscillator appeared at full
window gain the moment the count incremented — nine audible jumps across the
knob.

Now the layer count is fractional and the **whole LAYOUT crossfades**:

    win[i] = (1-f)*hann(u_i at N) + f*hann(u_i at N+1)

That distinction is load-bearing. Scaling just the top layer by the fraction
looks equivalent and is not: it gives up to **10.6% window ripple** at N=3.5,
which is precisely the level pulsing this card exists to avoid. Blending two
layouts that each sum flat gives a flat sum at every fractional position —
verified 0.0000% throughout. `1/√N` is interpolated across the fade too, or the
level steps where the layers no longer do.

Costs one extra window evaluation per layer per channel at control rate: about
5 cycles/sample amortised, 0.13% of budget. The per-sample inner loop is
untouched.

### 7. The density crossfade fought the octave wrap

The fade from finding 6 made the popping WORSE, not better - "I can hear the
new layer popping in VERY notably now."

Crossfading the N-layer and (N+1)-layer LAYOUTS keeps the window sum perfectly
flat at every fractional position (0.0000% ripple, verified), so it looked
correct. But **the two layouts rotate differently at an octave wrap**, so
mid-blend the stack cannot rotate cleanly and the top slot's energy is
discarded once per octave. Measured: largest sample step **123 mid-fade against
5 at either end** — worse than the stepping it replaced.

Fixed by separating the two concerns. The layout stays an exact integer one,
which rotates cleanly; the newest layer's gain is slewed independently
(`entry_gain_`, `>> 3` per control block ≈ 5 ms), which has nothing to do with
the wrap. Max step is now 5 at every fade position.

The general shape of the mistake: **two mechanisms that are individually
correct can still be in tension.** The window blend was right about level and
wrong about rotation; the wrap rotation was right about phase and assumed a
fixed layout. Neither test caught it because each was testing its own mechanism
in isolation.

### 8. Why the density fade was impossible, and what replaced it

The slewed entry gain from finding 7 was still very audible, "especially
descending - it's not fading in at all". It wasn't. The appended layer sat at

    u = (master + n) / n   ==  master/n + 1   ->  wraps to master/n

which is **exactly layer 0's position**. So `entry_gain_` was not fading in a
quiet new layer at the edge of the window; it was fading in a **duplicate of
layer 0 at up to 0.74 gain**, on top of the original.

The underlying reason both fade attempts failed is structural, and worth
stating plainly:

> **N is the window DIVISOR, so changing it respaces every layer at once.**
> Going 3 → 4 moves all three existing layers as well as adding one. There is
> no "entering layer" to fade, and a worst-case per-layer gain change of 0.63
> is unavoidable.

So the step is accepted, and made rare and predictable instead:

- **Hysteresis** (≈16% of a step) so ADC jitter at a boundary cannot retrigger
  it. Without that the count flickers between N and N+1 every control block,
  which is far worse than one clean step when the knob is actually moved.
  Verified stable against jitter, with all ten counts still reachable in both
  directions.
- **`1/√N` is slewed (~20 ms) rather than stepped**, so the LEVEL does not jump
  at the same instant the layout does. The layout step is unavoidable; the
  level step is not, and two simultaneous discontinuities read as much worse
  than one.

The wrap is now completely independent of density: max sample step at a wrap is
5 at N=3 and unrelated to knob position.

### 9. The shifter was inaudible for two independent reasons

Reported as "I can't hear what it's doing to the signal".

**The shift amounts were derived from the glide RATE.** At normal glide speeds
that gave shifts of **0.0017 to 3.5 Hz** — a frequency shifter needs tens of Hz
before the ear registers anything at all, so at 0.017 Hz you would wait a
minute for one cycle of beating.

Now it tracks the MASTER PHASE instead, which gives the shift its own Shepard
glide: `shift_base` doubles across an octave and halves back at the wrap,
exactly as `inc0` does, so the same window fades each layer's shift in and out
and the same rotation keeps it seamless.

    layer 0    0.25 - 0.5 Hz     inaudibly slow, as it should be
    layer 4    4 - 8 Hz          a slow throb
    layer 8    64 - 128 Hz       clearly shifted
    layer 11   512 - 1024 Hz     far out, but windowed away

Note this means the shifter works with Main at noon, which is correct — it is
an effect ON the input, not a function of movement.

**And the level was 16 dB down.** The quadrature pair was being halved:
`(I*cos + Q*sin) >> 1`. But those terms are 90° apart, so they sum to a
CONSTANT magnitude — it is a rotation, not a doubling — and the halving cost
6 dB for nothing. Removed: a full-scale input now measures rms 446 against the
synth path's 443, with 2.6 dB of headroom.

### 10. Page changes now use PICKUP, with LED feedback

Without it, changing page snapped three parameters to wherever the knobs
happened to be sitting: flicking Up to check the scale also jumped width and
level, and flicking back jumped speed, density and source. Every page change
was three unintended edits.

SPECTRAL tried pickup and removed it, and its note is worth taking seriously —
after a page change all three knobs felt DEAD, with no indication anything was
waiting. **The fix is feedback, not abandoning pickup.** While a knob is
uncaptured its LED pulses (0 = Main, 1 = X, 2 = Y, matching the knob order), so
"dead" becomes "waiting, and here is which one".

Capture band is 1200 of 32767, ~3.7% of travel: past ADC jitter, well inside a
deliberate nudge. Page 1 is live from boot, so nothing is dead at power-on.

This also removed a duplicate: the separate `level_live_` latch that finding 2
fixed is now subsumed by the general mechanism.

### 11. The frequency shifter was replaced by a RISING COMB

"I don't like the audio thru stream at all" — and the suggestion that came with
it was better than the original design: process the input with very narrow
bands at the same rising frequencies and relative intensities as the Shepard
tones, so it becomes a comb filter that rises forever.

That is right for a reason worth stating. A frequency shifter **transforms**
the input into something unrelated; a comb **filters** it, so the source stays
recognisable and the illusion is applied *to* it. The latter is what "Shepard
tones on live audio" should mean.

`comb.h`: N Chamberlin state-variable bandpasses, centres taken from the same
`inc[]` the oscillators use, gains from the same window. Measured 27.7 dB
rejection an octave either side — narrow enough to read as a comb tooth,
broad enough to stay musical.

It is also **cheaper** than what it replaced: ~264 cycles at N=12 stereo
against ~510 for a Hilbert pair plus twelve complex modulators.

Three things it needed:

1. **Clamped centres.** The Chamberlin form is stable only while
   `f = 2sin(πfc/SR) < 1`, i.e. below ~8 kHz. At N=12 the top layers sit at 14
   and 28 kHz with real window gain (up to 0.43), so unclamped they blow up.
   Clamped they pile up at 7.5 kHz and fall naturally quiet, because a 28 kHz
   component driving a 7.5 kHz band produces almost nothing.

2. **Q20 coefficients, and a hybrid tuning path.** In Q15 the coefficient for a
   13.75 Hz band is 58, so quantisation is 29 cents of detuning — the bands
   would drift off the oscillators they sit on and the sources would beat.
   Q20 alone did not fix it: the sine TABLE is only 512 points across the
   half-cycle, so a small angle resolves to a few LSBs whatever Q the result
   is in. Below 750 Hz the coefficient is now computed directly from the
   increment (`sin(x) ≈ x` to well under a cent there); above it the table
   takes over, since the linear form diverges to 67 cents by 7 kHz. Worst
   error is 3.0 cents at 13.75 Hz — inaudible and windowed away — and under
   0.74 cents everywhere above 27.5 Hz.

3. **Measured make-up gain.** A resonant band passes only a fraction of a
   broadband input, so at the shifter's old input scaling the comb sat 13 dB
   below the synth voice and Y read as a fade. `<< 5` puts it within 1 dB,
   with 3.1 dB of headroom on noise.

### 12. The comb clipped hard on tonal input — and noise hid it

"The audio path feels very very distorted (clipped?)". It was, badly:
**peak 7939 against a 2047 rail, 48% of samples clipping** on a sustained
drone. Measured, not guessed.

A resonant bandpass boosts a tone sitting on its centre by roughly `32768/q` —
about **16×** at `kCombQ = 2000`. The filter was not merely selective, it was
loud.

**And I calibrated the make-up gain on noise**, which is precisely the source
that cannot expose this: noise never sits on a band centre long enough to ring,
so it stayed tame at a setting where a drone was 4× over the rail. Choosing the
flattering test signal is the whole mistake.

Fixed by normalising the band output by its own resonant gain
(`MulQ15(kCombQ, bp)`), so an on-centre tone passes at **unity** and off-centre
content is attenuated — what a filter should do. Selectivity is untouched,
since it is set by q and this scales the whole response: 27.7 dB rejection
before and after.

Make-up gain then recalibrated against the WORST case rather than the average:
`<< 6`, with zero clipping on noise, drone and chord, at every Y position.

`comb_check.py::check_resonant_gain` now asserts on-centre gain stays near
unity, using a steady tone on a band centre — the case that actually bites.

### 13. The comb's resonator state did not rotate at the wrap

The loudest fault the card has had, and the report pinned it precisely: *"a
large phasy noise with a discreet start and stopping point... starts about
halfway through LED1 being lit, stops before LED stops being lit... the pitch
changes slightly depending on the position of Main."*

Every detail of that is diagnostic. LEDs show `master >> 30`, so the event was
tied to master phase, not to the input. A *discrete* start and stop means a
resonator being switched, not a filter sweeping. And the pitch following Main
means the ring-out frequency was **the band centre the glide had reached**.

At an octave wrap the stack relabels: band *i* inherits band *i−1*'s centre.
Finding 4 rotated the oscillator phases to match — but **not the comb's `lp_`
and `bp_` state.** So a resonator holding energy at one frequency was suddenly
retuned to another, and a charged resonator that is retuned rings out at the
frequency it was charged at.

Measured: state of 1.3e6, retuned down an octave, **rings out at peak 79558
against a ±2047 rail — 39× over.** With X clockwise several bands do it at
staggered times, which is the "multiple (3?) phasy tones, not all
simultaneously".

Fixed with `CombBank::RotateUp/RotateDown`, called from the same place as the
oscillator rotation. `comb_check.py::check_retune_ringout` measures the
ring-out both ways — 32590 retuned in place, 0 when rotated — and fails if the
control does not separate.

**A near-miss worth recording**: I first read this as a gain problem and
dropped the input from `<< 6` to `<< 4`. That removed the clipping and left the
card 15 dB quiet — trading a loud fault for a quiet one while the actual bug
survived. With the rotation fixed, `<< 6` measures a worst-case peak of 1368
against the 1450 knee. The gain was never wrong.

### The lesson, which is the same one four times

A test that begins *downstream* of the thing that is broken cannot see it.
`spiral_check.py` takes a rate as an argument; `passthru_check.py` renders the
synth bank directly; `shepard_check.py` sets the master phase itself and only
ever inspected the WINDOW, never the phases. Every one was green while the card
misbehaved.

**When adding a test, ask what it takes as given.** That is where the next bug
will be — and note that the fourth one hid behind an invariant that was
genuinely true. A constant-sum window is necessary for seamlessness; it is not
sufficient, and a test that proves the necessary condition can read as though
it has proved the sufficient one.

## The invariant everything depends on

    sum over layers of hann((master_frac + i)/N)  ==  N/2   EXACTLY

for every layer count and every master phase, with zero ripple. This is the
card's constant-overlap-add property. If it ripples, the output level pulses
once per octave cycle and the illusion collapses into an audible sweep.

It is a mathematical property of sin² at even spacing, verified in exact float
(ripple 0.0000000000) as well as in the integer code. **Do not "improve" the
window.** `shepard_check.py` asserts it at every N.

## Sizing, and why

**3–12 layers, `f_base` = 13.75 Hz (A−1), 192 MHz, control rate = 48 kHz / 32.**

- **192 MHz** — proven on this hardware by grains/51, glitter/53 and SPECTRAL.
- **Control rate K=32** puts worst-case pitch drift at 6.4 cents within a
  block, at the top glide speed of 8 oct/s. It is a *smooth* staleness — the
  pitch is momentarily behind and then catches up — not a discontinuity, and at
  that speed the ear is tracking the sweep rather than the tuning. Power of two
  so the counter is a mask. If it ever reads as gritty on hardware, drop
  `kControlMask` to 15 rather than capping the speed.
- **pow2 LUT at 257 entries** — measured 0.0016 cents. 129 entries gives 0.006,
  65 gives 0.025. The knee is at 256 and the table is 1 KB, so there is no
  reason to go smaller.

## The economies that make 12 layers affordable

Both are structural, not optimisations to be undone later.

### `inc[i] = inc0 << i`

Layers are exactly an octave apart, so every layer's frequency increment is
layer 0's shifted left by *i*. **One** pow2 lookup serves all twelve, at
control rate. There is no per-layer exponential. This was the obvious failure
point in the design and the octave structure removes it entirely.

### One Hilbert transform, N modulators

The analytic signal depends only on the INPUT, not on how far it is
subsequently shifted, so N transforms would cost 12× for **bit-identical**
results. That is waste, not a trade-off.

More importantly it must be shared for **correctness**: all N modulators need
the same (I, Q) or the layers decorrelate and the illusion smears.
`hilbert_check.py::check_layer_coherence` pins this so a later "optimisation"
cannot silently split it.

## Core split — deliberately NOT SPECTRAL's arrangement

Everything runs in the audio ISR. Core 1 is unused.

SPECTRAL puts its DSP on core 1 because an FFT frame costs ~0.5 ms, 24× the
per-sample budget: it *cannot* run inline, and the ring buffer is the price of
admission. An additive oscillator bank has **no frame structure at all** —
every sample costs the same, there is nothing to batch, no deadline to miss.

Block rendering would add 0.7–2.7 ms of latency for no benefit, and would
reintroduce the ring-pointer races, stale-slot residue and read/write overlap
bugs that dominate SPECTRAL's notes. Self-inflicted.

It also degrades honestly: an ISR overrun is obvious, where a block renderer
turns the same fault into an intermittent underrun that is miserable to
reproduce.

**If CV Out 2 ever reads high on hardware**, the control block (`UpdateControl`)
is the piece that moves to core 1 — publish via a double-buffered block with a
`__dmb()` before the index flip, or the ISR reads a half-built block for one
sample and clicks intermittently.

## No int64 in the audio path — verified in the binary

`MulQ15` and `MulQ30` exist so that no 64-bit multiply appears in the hot path.
Unlike SPECTRAL, `PICO_INT64_OPS_IN_RAM` is **not** set: an `__aeabi_lmul` in
the audio path here is a **bug to find, not a flag to add**.

Four crept in during the first build and were caught by disassembly:

    UpdateControl        int64 casts on layer count and scale  -> plain int32
    UpdateControl        (uint64)kBaseInc * m                  -> pre-shifted int32
    UpdateControl        64-bit window-position divide         -> split divisor
    SpiralDelay::Process int64 in Saturate (both branches)     -> int32

Verify after any change:

    arm-none-eabi-nm build/shepard.elf | grep aeabi_lmul
    # then disassemble ProcessSample / UpdateControl / SpiralDelay::Process
    # and grep for lmul - the count must be 0

The helper is still *linked* (the SDK pulls it in elsewhere); what matters is
that nothing in the audio path calls it.

### The split-divisor trick

The window position is `(master + i*2^32) / n`, which needs 36 bits. Rather
than carry a 64-bit intermediate it is split:

    (master + i*2^32) / n  ==  master/n + i*(2^32/n)

as three int32 divides per control block instead of one 64-bit divide per
layer. Verified bit-identical to the exact form by
`shepard_check.py::check_split_divisor`.

## Hilbert: the honest numbers, and two traps

**Measured over a 50-point log sweep, not a handful of spot frequencies:**

    worst quadrature error   2.0 degrees at 33 Hz
    -> sideband rejection   ~35 dB worst case
    mid-band (100 Hz-20 kHz) 0.2-0.7 degrees, 44-55 dB

A sparse sample suggests 0.70° / 44 dB. That misses the equiripple peaks and is
optimistic — the design estimate said exactly that, and the dense sweep
corrected it. 35 dB is ample for an effect that is deliberately inharmonic, but
the number on record should be the one the filter delivers.

**Q30 quantisation costs nothing**: integer and float agree to 0.001°, so the
ripple above is the filter's own.

Two traps, both producing a card that sounds plausible but is wrong:

1. **The published constants are `a`; what is stored is `a²`.** Using them raw
   costs ~8× the phase error (0.46° → 3.62° at 1 kHz).
2. **The extra z⁻¹ goes on branch A.** On branch B the branches collapse:
   17.7 dB rejection instead of 55.4 — a ring modulator, not a shifter.

`hilbert_check.py` asserts both, the second on *rejection* rather than degrees
because that separates the cases far more sharply.

## Frequency shift is LINEAR — this is not a bug

A frequency shifter moves every partial by the same **hertz**; a pitch shifter
multiplies by the same **ratio**. 200/400/600/800 shifted +100 Hz becomes
300/500/700/900 — ratios 1.50/1.25/1.17/1.13, so the harmonic series breaks and
the result clangs.

Inherent to the technique, and the reason Y *crossfades* rather than the
shifter replacing the oscillators. The illusion still holds because the layer
SHIFT AMOUNTS are octave-spaced.

Do not "fix" this by trying to make the shifter proportional. That is a
different algorithm (and a far more expensive one).

## Gotchas carried over from SPECTRAL — do not undo these

1. **Tables are plain globals in `.cpp`, never header statics.** A `static` in
   a header can land in flash, where writes are silently discarded. Verified:
   `sin_lut` and `pow2_lut` are at `0x2002xxxx` marked `B`. The scale tables
   and `kInvSqrtN` are `const` and correctly in flash at `0x1000xxxx`.
2. **Alt-boot reads the switch ONCE, after the full 0.5 s window** — never
   "Down seen at any point". WorkshopZX and WorkshopBio both shipped that bug.
3. **`__not_in_flash_func` on every hot-path function**, not just
   `ProcessSample`.
4. **Page changes are deferred** (`kPageSettle`) — Up and Middle are not
   adjacent, so a flick passes through Middle and would otherwise register a
   spurious page change.
5. **Switch debounce** (`kSwitchSettle`, 20 ms) — without it contact bounce
   toggles freeze an even number of times, so it appears not to work at all.
6. **Pulse triggers are counted, not flagged.** A bool set by the ISR and
   cleared elsewhere loses any trigger arriving between the read and the clear.
7. **`PICO_XOSC_STARTUP_DELAY_MULTIPLIER=64`** in both CMakeLists *and* as a
   `#define` in `fixed.h` — the upstream PR validator greps the source for the
   `#define` form and does not see the CMake flag.

## The signed-difference portamento

    int32_t err = (int32_t)(target_q32 - master_out_q32_);
    master_out_q32_ += err >> 6;

**The `int32_t` cast is load-bearing.** A signed difference takes the shorter
way round the octave automatically; unsigned, a one-semitone step sometimes
slews the long way — an eleven-semitone sweep in the wrong direction.
`quant_check.py::check_slew` asserts the excursion never exceeds half an octave.

## SPIRAL feedback is capped HIGHER than TAPE's, deliberately

TAPE uses 0.85 to guarantee decay. SPIRAL uses 0.95 because it is *supposed* to
sustain — repeats climbing forever is the instrument.

What bounds it is **the pitch shift itself**: energy shifted up leaves through
Nyquist, energy shifted down leaves through the loop highpass. Note the damping
lowpass has **DC gain exactly 1.0** and contributes nothing to decay — that is
the trap that made SPECTRAL's first tape build run a two-minute tail.

At the centre deadzone (unity shift) neither escape route operates, so high
feedback there will ring for a long time. Documented behaviour of that
position, not a fault. `spiral_check.py` runs the real loop at DC and reports
the tail at every setting.

## Test discipline

**If you change a DSP file, run the matching test.** They take seconds.

| File | Test | What it pins down |
|---|---|---|
| `shepard.cpp` | `tools/shepard_check.py` | window constant-sum, pow2 accuracy, 1/√N, split divisor, illusion continuity |
| `shepard.cpp` | `tools/quant_check.py` | scale tables, octave wrap, portamento direction, stepping |
| `comb.h` | `tools/comb_check.py` | tuning accuracy, stability at every centre, selectivity, centres match the oscillators, level across the glide |
| `spiral.h` | `tools/spiral_check.py` | shift ratio, crossfade, buffer bounds, loop stability at DC |
| **whole chain** | **`tools/passthru_check.py`** | **end-to-end gain — run after ANY scaling change** |
| whole engine | `tools/shepsim.py` | renders WAVs, and the seam analysis (below) |

## The seam analysis, and a lesson about detectors

`tools/shepsim.py` runs the exact integer engine and writes audio, so the
card can be heard before it is flashed. It also answers the make-or-break
question objectively: **is the octave wrap detectable?**

The first version of that analysis reported three measures and **two of them
were worthless.** Deliberately breaking the window lookup and re-running gave:

                    correct        broken
    level ratio     1.1896         1.1893      <- cannot tell them apart
    HF splatter     1.0934         1.0930      <- cannot tell them apart
    seam step       0.8000         1.0000      <- the real signal

Level periodicity at low layer counts is **not** a seam. It is ordinary
beating between three widely spaced oscillators — the phase-folded profile
is scattered, not dipped at any consistent phase. A detector built on it
condemns a perfectly good card.

Two things follow, and both are now in the code:

1. **The verdict rests on seam step alone** — the largest sample-to-sample
   jump near a wrap boundary, over the largest jump anywhere. A real
   discontinuity puts the global maximum exactly at the seam and scores 1.00.
2. **The check calibrates itself every run.** It breaks the window on
   purpose first and confirms the measure responds, because a detector that
   never fires is indistinguishable from one that cannot fire. If the
   control fails to separate, it says so and withholds the verdict.

Current result: control 1.00, real build 0.44–0.96 at every layer count.
No wrap discontinuity. **Expect audible gentle beating at N=3 regardless** —
that is three oscillators, not a defect.

## Still to do

**`docs/BRINGUP.md` is the ordered first hardware session** — each step depends
only on what is already confirmed, so a failure localises to what was just
added. Read CV Out 2 *before* judging the sound: an overrunning ISR produces
artefacts that look like DSP bugs and chasing those first wastes the session.

- **Flash it and listen.** Nothing here has been heard.
- Confirm the octave wrap is genuinely inaudible at low layer counts.
- Check CV Out 2 for the real DSP load. The estimate was ~22% raw / ~45–50%
  compiled, but SPECTRAL was modelled at 51% and ran at 231%, so the meter is
  the authority.
- Confirm L/R sum to mono without cancellation on hardware (host test says
  −0.22 dB worst).
- Listen for aliasing at 12 layers — the top layer sits near 28 kHz, silenced
  only by the window.
- `panels/` is empty — no panel art yet.
- `UF2/` is empty — populate on first release, and flip `info.yaml` to
  `draft: false` / `Status: Released` only after hardware testing.
