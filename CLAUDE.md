# BARBER'S POLE — working notes for Claude Code

A program card for the **Music Thing Modular Workshop System Computer**
(RP2040), built on the header-only **ComputerCard** library. Sibling project to
`../WorkshopSpectral`, `../WorkshopZX`, `../WorkshopBio`, `../Workshop2D2` —
reuse their conventions where they fit.

**BARBER'S POLE generates Shepard–Risset tones**: octave-spaced components
sliding through a fixed spectral envelope, so pitch appears to rise or fall
forever. The alt-boot mode is **HIDDEN BARBER**.

## Naming — the code does NOT follow the product name

The card was called SHEPARD during development and renamed late. **The code was
deliberately left alone**, and the reason matters if you are tempted to
"finish" the rename:

- `namespace shepard`, `shepard.h/.cpp`, `SHEPARD_*` guards, `RenderShepard()`
  and the `shepard.uf2` target all stay. **"Shepard tone" is the technical name
  of the illusion the DSP implements**, not a product name — renaming it would
  make the code describe the marketing instead of the mechanism, and the
  comments would still have to say "Shepard–Risset" everywhere.
- `ping.h`, `PingVoice`, `PingBank`, `ping_mode_` and `ping_check.py` stay.
  A **ping is what a struck, decaying voice is**; HIDDEN BARBER is what the
  mode is called on the panel.

So: **product names in `README.md`, `info.yaml` and `docs/`; mechanism names in
the source.** The repository is still `WorkshopShepard` and that is fine.

## Current status: v1.0.0, RELEASED and confirmed on hardware

`build/shepard.uf2` — **1.38% flash, 56.85% RAM**. All six host test suites
pass, and every finding below has been verified by ear.

Many listening sessions; the findings below are what they produced.
The normal engine has comfortable CPU margin. PING at four simultaneous voices
with the live input mixed in sits close to the ceiling and **will** distort if
triggered densely — that is documented as a limit rather than treated as a bug.

**Removed on the way to release:** the CV Out 2 DSP-load meter (it had served
its purpose), and SEALED freeze (inherited from SPECTRAL, where it held phase
advance in a frozen spectrum; here it only muted the live input, which Y
already does).

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

## Found on HARDWARE — twenty-seven findings, four of them my own changes making things worse

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

### 14. The bands snapped on and off instead of fading — the Q was too high

*"I'd expect it to fade in/out as the relative gain of the band was introduced.
Or am I misunderstanding?"* — not misunderstanding at all, and the question was
the right one to press on.

**The window was innocent**: verified fading smoothly from 0.0000 to 0.7500
across the sweep, exactly as designed. The culprit was the FILTER'S OWN
RESONANCE CURVE, which at Q ≈ 16 is far steeper than the window and therefore
dominates the envelope completely.

The point that makes this specific to a comb that MOVES: a band passes a fixed
partial only while its centre is within a bandwidth of it. Sweeping an octave
per cycle, that works out as

    Q = 16   tone audible 16.5% of the octave, steep onset
    Q =  8   35.0%
    Q =  4   86.5%, gentle - the window is what you hear

So a sustained tone appeared abruptly, sounded briefly and vanished. `kCombQ`
is now 8192 (Q ≈ 4; note the coefficient is INVERSE to Q, so larger is wider).
Rejection drops from 27.7 dB an octave out to 15.5 - broader teeth, but the
motion is smooth and the fade is the window's, which is what makes it read as a
rising filter rather than a set of tuned blips.

`comb_check.py::check_sweep_envelope` now asserts a fixed tone stays audible
for at least half the octave, so a future "let's make it more selective" cannot
silently bring the snapping back.

Also worth noting: the internal resonator state is ~10x smaller at Q = 4, which
gives the whole path far more headroom than it had.

### 15. The live path, third attempt: OCTAVE-STACK the input

Two designs rejected by ear, and they failed the same way — both treated the
input as something to PROCESS while the structure moved past it:

  **Frequency shifter** — moved every partial by the same hertz, breaking the
  harmonic series. The source came back unrecognisable, so the relationship
  between input and output was inaudible.

  **Rising comb** — swept resonant bands across the input. A given partial was
  only heard while a band crossed it, so it came and went rather than
  participating in the glide.

The third applies the Shepard CONSTRUCTION instead of an effect: one delay
line, N read heads at 2^i the write rate, so every partial is transposed into
N octave-spaced copies and each rises and fades through the same window as an
oscillator layer. The internal voice is octave-stacked sines; this is
octave-stacked *you*.

Costs no extra RAM — SPIRAL's 128 KB buffer is idle in normal boot and this is
idle in alt-boot, so `g_delay_buf` serves both.

**Two things it needed, both found by measurement:**

1. **Drift, not an absolute read pointer.** The obvious form advances a pointer
   at `rate`; at 2× it races away from the write pointer at 1×, laps the buffer
   repeatedly, and the crossfade has no relationship to the lap period. That
   put a spurious component **45 dB ABOVE** the wanted octave. Accumulating
   `rate - unity` keeps each head permanently within one window.

2. **A long crossfade window — 16384, not 8192.** The two taps sit half a
   window apart in the BUFFER, which at rate *r* is only `(win/2)/r` apart in
   real time, so as the ratio rises they converge on the same moment and the
   crossfade stops hiding the recycle. Measured at 4×: wanted octave dominant
   by **−9.9 dB at win 4096, +0.6 at 8192, +18.5 at 16384**. It also halves
   every recycle rate.

Rates are capped at 4× and the stack is CENTRED (`2^(master + i − N/2)`) so
they straddle unity — heads reading slower drift slowly, which took the worst
recycle from 91 Hz to under 9 Hz.

Level is also far more stable than the comb's: rms 308–358 across noise, drone
and chord, because it transposes rather than resonates.

### 16. Grain length varies with shift ratio — a trade-off, not a bug

Reported precisely: *"when I descend I can hear the grains getting lower — but
also LONGER, and the longer ones I can then hear being replaced by short higher
ones. The barber's pole is working macroscopically but not on an individual
line."*

Exactly right. `kOctWin` is a fixed number of BUFFER samples, so its duration in
REAL time is `win / rate`:

    0.25x   1365 ms      2.0x    171 ms
    0.50x    683 ms      4.0x     85 ms
    1.00x    341 ms

A 16× spread across the stack.

**Compensating makes the shifter worse.** Scaling the window by the rate gives
constant grain length but wrecks downshifts, because a short window cannot span
enough of a low-frequency waveform:

                        grain      transposition
    fixed window        varies     +26 to +37 dB at every rate
    rate-scaled         constant   **−16.6 dB at 0.25x** (artefacts win)
    sqrt-scaled         4x spread  +4.4 dB at 0.25x, still marginal

Decision: **keep the fixed window, quality first.** The macroscopic illusion
works; individual lines have varying grain length. That is inherent to a
two-tap granular shifter and the alternative is audibly worse.

**A related question answered:** should the grains fade? They do — both
envelopes are correct. The two-tap crossfade is sin²/cos² summing to exactly
32767, and the layer window fades 0.000 → 1.000 → 0.000 across the cycle. What
is heard starting and stopping is the RECYCLE: the two taps play unrelated
moments of the buffer, so the LEVEL crossfades smoothly while the CONTENT
jumps, and the ear tracks content. Unavoidable in any fixed-buffer shifter.

### Headroom with many layers — checked, no overload

    N=12 drone, full scale      peak 1503   knee 0.04%
    N=12 chord, full scale      peak 1387   knee 0.00%
    N=12 near-DC (coherent)     peak 1624   knee 1.35%
    N=12 full-scale square      peak 1660   knee 0.92%

Against a 2047 rail with a 1450 soft knee. The clipper engages ~1% of the time
on pathological input and the rail is never reached. The octave stack is also
much better behaved than the comb it replaced, because it transposes rather
than resonates — level barely depends on source type.

### 17. PING replaced SPIRAL as the alt-boot

(PING is the internal name; the mode is displayed as HIDDEN BARBER.)

SPIRAL was rejected by ear. It was a competent pitch-shifting delay but had
nothing to do with the card's own idea, and it spent 128 KB saying so.

PING is the user's design and is much better: **the pole keeps climbing but
silently, and a trigger voices a snapshot of it that decays WITHOUT climbing.**
An invisible barber's pole you strike. Each trigger lands wherever the glide has
reached, so repeated strikes build a chord out of one rising structure — the
illusion becomes something you play rather than something you listen to.

A voice copies `inc_[]`, `win_l_/win_r_[]` and `oct_rate_[]` and adds an
envelope. Nothing in it moves except that envelope, which is exactly what "does
not climb once voiced" means. Four voices, oldest stolen.

**Three things it needed:**

1. **Strike at the END of the control block.** A voice copies values that
   `UpdateControl` computes; striking earlier captures the *previous* block's
   pitches, which at a fast glide is audibly the wrong note.

2. **Phases reset to zero, not copied.** Every layer starting at phase zero
   means every layer starts at zero amplitude, so the attack has nothing to
   click against. Copying the pole's running phases would start twelve
   oscillators mid-cycle at once — a broadband transient.

3. **A Q20 decay TABLE, not a shift.** `env -= env >> n` is the obvious
   one-pole and it does not work here: the envelope is 15 bits, so `env >> 17`
   is **zero for every value it can hold** — the decay never begins and the
   voice rings forever. Anything above ~14 stalls outright. The envelope now
   runs in Q24 internally and is multiplied by a tabled Q20 coefficient,
   giving a measured 23 ms to 1.84 s, evenly spaced.

**And one real headroom finding:** `1/√N` normalises for N LAYERS and knows
nothing about there being four VOICES. Without an extra `>> 1` the output
clipped at every layer count — measured peak 2906 at N=3 and 3493 at N=12
against a 2047 rail. With it, worst case 1746.

`spiral.h` and `spiral_check.py` removed; `SoftClipOut` moved to `fixed.h`
where it belongs, and the delay buffer is now owned by the octave stack alone.

### 18. The ping envelope overflowed int32 — and Python could not see it

Reported as *"the ping is very short - it's just a click"*. It was: the
envelope collapsed on its first sample and never rang at all.

`MulQ20Env` split the envelope at bit 12, so the high term was
`(env >> 12) * c` — worst case **4095 × 1048497 = 4.29e9, twice int32's
limit.** It wrapped negative and the envelope died instantly, regardless of
what the decay knob said.

**The test could not have caught it.** `ping_check.py` mirrored the same
arithmetic and passed, because **Python integers are arbitrary precision** —
the identical expression is simply correct there. Fixed-width overflow is
invisible to a Python mirror unless it is asserted explicitly.

Moving the split to 15 fixed that term and broke the other: `lo * c` reached
3.4e10. There is no single split point that works — the high term needs
`s < 11`, the low term needs `s > 13`.

Resolved by not writing a bespoke multiply at all. `MulQ15` is already proven
safe for `|x|` up to 20e6 against an envelope peaking at 16.8e6, and the
envelope stays in Q24 so a coefficient just under 1.0 still has bits to bite
on. Measured 17 ms to 1.72 s, with **2.0× overflow headroom**.

`ping_check.py::check_no_overflow` now asserts every partial product against
`INT32_MAX` explicitly, and separately proves the envelope still holds value
after 200 samples rather than having collapsed. That is the shape of test a
Python mirror needs for fixed-width arithmetic: the mirror shows what the
maths *should* do, and a bounds assertion shows whether the *type* can hold
it.

### 19. PING choked the ISR — and the same bug corrupted the audio

Reported as *"firing lots of triggers means the audio input side distorts...
assuming CPU choke as it doesn't sound like clipping"*. Correct on both counts,
and the cause was a single line.

`octave_.Read(i, ...)` was called **once per voice per layer**. With four
voices and twelve layers that is 48 reads per sample instead of 12 — but worse
than the cost, all four voices share `drift_q16_[]`, so **the read heads
advanced four times per sample** and the transposition was garbage. That is
the distortion; it was never clipping.

    4 voices x 12 layers x ~85 cyc = 4080 raw against a 4000 budget

Hoisting the read out of the voice loop fixes both: 12 reads, 2640 cycles,
66% raw. The heads now track the current pole rather than each voice's frozen
rates — a deliberate simplification, since what makes a ping a ping is its
ENVELOPE and window gains, and those stay per-voice. A strike still gates and
still decays.

**Two further trims:**

- **Retire voices at −58 dB, not −72.** Every sample a voice stays "active"
  costs a full twelve-layer pass, and nothing below −58 dB is audible under a
  decaying envelope.
- **Dropped the fixed `>> 1` polyphony halving.** It guaranteed four
  simultaneous voices could never clip, at the cost of making the card **6 dB
  quiet essentially all the time** — a player strikes one at a time and mostly
  hears one decaying voice. Measured: a single voice peaks ~1453 at N=3 and
  1746 at N=12, at or just past the 1450 knee, effectively transparent. Four at
  once reaches 3436 raw, which `SoftClipOut` folds to ~1911 asymptotically
  rather than pinning.

That second one is the general point: **normalising for the worst case
penalises the common case.** A soft clipper that never pins is there precisely
so the rare pile-up can be allowed to hit it.

### 20. One knob drove two parameters — decay AND quantise

Reported as *"it's initially okay, but stops changing note when I change the
length from a click"*. That sentence localises the fault precisely: the DECAY
knob was affecting PITCH.

When PING replaced SPIRAL I gave decay page 2 Main — **without removing
quantise from it.** Both read `stored_[1][0]`, so one knob drove both:

    0%   decay step  0    scale = smooth
    50%  decay step  8    scale = major
    100% decay step 16    scale = pentatonic

Turning it up for a longer ring also walked the pole from smooth into
chromatic, major, minor and finally pentatonic. With a slow pole, consecutive
triggers then landed on the **same scale degree** — the pitch stopped changing.

Decay moved to page 2 X. Quantise stays on Main, because landing strikes on a
scale is one of the most playable things about the mode; stereo width loses its
knob in PING instead, which matters far less when striking discrete notes.

**A second collision was hiding behind the first:** page 2 X still fed
`width_div_`, so decay would have widened the image. Page 2 X is now read once
into `page2_x_` and used by exactly one parameter depending on mode.

`ping_check.py::check_no_knob_collision` counts readers of every
`stored_[page][knob]` in `main.cpp` and fails if any knob has more than one.
It found the second collision immediately.

**This is a class of bug no DSP test can reach.** Every numeric check was
green — the decay was correct, the scales were correct, the pole advanced
correctly. The fault was in the WIRING, and it needed a test that reads the
source rather than the signal.

### 21. The strike trigger was also freezing the pole

*"The background (invisible) climb ISN'T happening when the note is ringing.
So if the next trigger is before the note has decayed away there is no climb."*
A precise diagnosis, and the cause was one line.

Pulse In 1 is the freeze GATE in normal boot and the strike TRIGGER in PING —
and `gate_freeze_ = pulse1` applied **both** behaviours in both modes. So the
pulse that voiced a ping also stopped the pole climbing, for as long as the
gate stayed high.

It presented as a decay-knob problem because a longer decay means retriggering
sooner, which means the gate is high a greater fraction of the time. **The
decay was innocent both times** — first the quantise collision, now this.

Fixed: `gate_freeze_ = ping_mode_ ? false : pulse1`. In PING the pole climbs
unconditionally, which is the entire idea.

The switch-latched freeze deliberately still works in PING: holding the pole
still so every strike gives the same chord is a real performance control, and
SEALED silencing the live input is coherent there too. Only the GATE was wrong.

`ping_check.py::check_trigger_does_not_freeze` asserts both halves — that the
gate is mode-conditional, and that the switch freeze path is untouched.

**Superseded by finding 27.** Making the gate mode-conditional fixed PING but
left normal boot with the same jack doing both jobs, so stepping the scale
still stopped the glide. HOLD has since moved to Audio In 2 and Pulse In 1 is
now an event jack in both modes; that test asserts the stronger invariant.

**Second source-level bug in a row**, after the knob collision. Both were
invisible to every numeric test because the DSP was correct in each case; what
was wrong was which control fed what. Worth remembering when a card gains a
mode: the new mode reuses jacks and knobs that already mean something, and the
old meaning does not switch itself off.

### 22. Alternate-layer panning was tried and REVERTED — it breaks the illusion

The stereo width is deliberately subtle, and that is not a defect to fix.

The window-offset scheme reaches only 0.90 L/R correlation at maximum, which
is barely wide. I measured that, called it weak, and replaced it with
alternate-layer panning — even layers left, odd right — which measures 0.009
at full width. Numerically a far better stereo control.

**It destroys the effect.** The Shepard illusion depends on the octave-spaced
components being heard as ONE fused stack. Panning alternate octaves to
opposite sides is precisely the cue that tells the ear they are separate
sources, so the stack stops fusing and the endless-rise reads as several
independent lines moving in parallel.

Reverted. The window offset stays, and its weakness is the point: it
redistributes gain slightly between channels **without ever separating an
octave from its neighbours**, so the stack stays fused and the outputs are
close to mono by design.

**Do not "improve" the stereo width by decorrelating the layers.** Any scheme
that gives an octave a different spatial position from the octave above it
will measure better and sound worse. If more width is ever wanted, it has to
come from something that leaves the layer-to-layer relationship intact — a
downstream effect, or a treatment applied equally to every layer.

Filed alongside the window itself in the list of things whose apparent
weakness is load-bearing.

### 23. The twelfth layer dropped the pitch a semitone

*"When I move the X knob clockwise and add the last octave the pitch drops
~a semitone... it's very audible if frozen."*

Layer FREQUENCIES do not depend on N at all — `inc[i] = inc0 << i` never sees
it. What N changes is which layers the WINDOW makes loud, and each layer added
moves the window peak up half a slot, which at octave spacing is exactly
**+600 cents**. Measured even from N=3 to N=11: 0.0 cents of deviation.

At N=12 the top layer sits at 13.75 × 2¹¹ = **28160 Hz, above the 24 kHz
Nyquist limit.** It still carried window gain (0.067) and still counted in the
constant-sum, but contributed nothing audible — so the AUDIBLE centre landed
**68 cents short** of where the pattern leads the ear to expect. That is the
semitone, and frozen makes it obvious because no glide masks it.

`kMaxLayers` is now **11**, where the top layer is 14080 Hz and every layer
contributes.

**A second, smaller fault found on the way:** an above-Nyquist increment was
CLAMPED to `0x7FFFFFFF`, pinning that oscillator to exactly Nyquist — an
audible artefact instead of the silence it should give. Letting it wrap would
be worse still: the increment aliases, and as the pole rises the alias
DESCENDS, giving a tone moving against the illusion. Now silenced outright.

`shepard_check.py::check_layer_count_pitch_steps` asserts the step is
consistent across every layer count, and **fails with a 67.7-cent spread if
kMaxLayers is put back to 12** — verified. The measurement has to weight by
AUDIBILITY, excluding anything above Nyquist, or it reports the pattern as even
and misses the fault entirely.

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

### 24. The live path was quiet, and the quietness tracked layer count

Two reports, opposite ends of the same fault: *"the audio in/out seems very
quiet compared to the generated tones"*, then *"actually depends on the number
of layers of notes"*.

`1/√N` was being applied to BOTH sources. It is right for the oscillator bank
(incoherent, powers add) and wrong for the octave stack (correlated copies of
one source, measured growth N^0.70), so the live path was over-normalised,
worst at low N. Fixed with a second table, `kInvPowN`, and by accumulating the
two sources separately so each is normalised by its own law before the mix.

Live rms went from 320–412 across N (a 2.3 dB swing, and up to 2.9 dB below the
synth) to 357–389 (0.8 dB swing, within 1.9 dB of the synth).

Full derivation under **The live path needs a DIFFERENT normaliser**, below.

### 25. The crossfade dipped 3 dB in the middle

*"When the X knob is CW the internal sounds massively dominate."*

The source mix was a linear crossfade. Between two UNRELATED signals that loses
3 dB at the midpoint — the 50/50 mix measured BELOW both sources at every layer
count (275 at N=3, against 445 synth and 389 live). Replaced with an
equal-power cos/sin fade read out of the existing `sin_lut`; the midpoint now
sits between the two sources, gaining 3.6 dB.

Note the user also guessed the cause correctly as *"probably high frequencies
of the synth cutting through"* — which is a real secondary effect, since the
octave stack rolls off where the oscillators do not. The level fix addresses
the dominant term.

### 26. The seam detector was condemning N=3 — a false positive in the committed tree

**Not from hardware** — found when the new level tests were added and the whole
analysis was re-run. **N=3 had been scoring a flat 1.0000, the same as the
deliberately-broken control, in every run since the detector was written.**

The measure is "largest jump near a wrap / largest jump anywhere". At N=3 the
largest jump anywhere is 5 LSB — the quantisation floor — and 11,183 samples
tie at it, so whether one lands in the seam guard is luck. The broken control's
maximum is shared by 28. A real discontinuity stands alone.

The verdict now requires the maximum to be unique (≤0.5% of samples tie);
otherwise it reports *"no unique jump"* rather than a defect.

**This is the third measure in that file to fail this exact way.** Level
periodicity and HF splatter were retired earlier for the same reason: a ratio
only separates signal from noise when its denominator IS the signal.

### 27. HOLD moved to Audio In 2 — one jack cannot be a gate AND an event

The README claimed *"Glide and stepping work together: park Main at centre for
pure manual stepping, or leave it off-centre and the pulses nudge an
already-moving glide."* Reported as a good idea that **isn't true**, and it
wasn't: stepping worked, but the second half could not, because Pulse In 1 was
BOTH the step trigger and the freeze gate.

The rising edge advanced a degree; the gate then pinned the pole for as long as
it stayed high. So stepping an already-moving glide necessarily stopped it. The
two meanings were in direct conflict and no amount of tuning could reconcile
them — **a gate and an event are different gestures.**

HOLD now lives on **Audio In 2**, which was the card's one unused jack. That is
the right home for it structurally: a hold is a STATE, and a level input is
where states belong. Pulse In 1 is left meaning exactly one thing per mode —
step in normal boot, strike in HIDDEN BARBER.

**Two things it needed:**

1. **A Schmitt trigger.** Audio In 2 is a bipolar AUDIO input with no
   comparator in front of it, so a single threshold chatters on any slow edge —
   and chatter on a hold reads as the pole stuttering rather than stopping.
   `kHoldEngage` 400 / `kHoldRelease` 200 (about 1.2 V and 0.6 V of a 6 V full
   scale) puts the hysteresis at half the engage level. An unpatched jack reads
   exactly 0, because ComputerCard zeroes disconnected inputs, so the default is
   "not held".

2. **Stopping the trigger stepping the scale in PING.** Found while making the
   change, and it would have become audible immediately: `if (triggered)` ran
   `StepScale` in both modes, so in HIDDEN BARBER every strike also kicked the
   pole a degree. Repeated triggers would have walked up the scale rather than
   sampling wherever the pole had drifted to — which is the entire idea of the
   mode. **It was latent only because the gate used to freeze the pole for its
   own duration and hid the jump.** Now `!ping_mode_ && scale_ != kScaleSmooth`.

That second point is the general shape: **removing a bug can expose one that
was hiding behind it.** The freeze was masking the step, so fixing the freeze
made the step audible for the first time.

**This is the third "one control, two meanings" fault on this card**, after the
decay/quantise knob collision (finding 20) and the trigger/freeze collision
(finding 21). All three were invisible to every numeric test because the DSP
was correct in each case; what was wrong was the WIRING.
`ping_check.py::check_trigger_does_not_freeze` now asserts the stronger
invariant — that Pulse In 1 does not reach `gate_freeze_` at all, in either
mode — plus the hysteresis and the PING step exclusion. Verified to fail on
both regressions.

## The invariant everything depends on

    sum over layers of hann((master_frac + i)/N)  ==  N/2   EXACTLY

for every layer count and every master phase, with zero ripple. This is the
card's constant-overlap-add property. If it ripples, the output level pulses
once per octave cycle and the illusion collapses into an audible sweep.

It is a mathematical property of sin² at even spacing, verified in exact float
(ripple 0.0000000000) as well as in the integer code. **Do not "improve" the
window.** `shepard_check.py` asserts it at every N.

## Sizing, and why

**3–11 layers, `f_base` = 13.75 Hz (A−1), 192 MHz, control rate = 48 kHz / 32.**

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

## The live path needs a DIFFERENT normaliser — 1/N^0.70, not 1/√N

Reported from hardware twice, from opposite ends, and both reports were the
same fault: *"the audio in/out seems very quiet compared to the generated
tones"*, then *"actually depends on the number of layers of notes"*.

`1/√N` is correct for the **oscillator bank**. Those layers are mutually
incoherent — different frequencies, unrelated phases — so their *powers* add
and the sum grows as √N.

The **octave stack is N copies of one source** transposed by octaves. Those are
partially *correlated*, so they sum faster. Measured with the normaliser
divided back out:

    N= 3  raw  554.0        grows as N^0.70
    N= 5  raw  736.2        (incoherent N^0.5, fully coherent N^1.0)
    N= 7  raw  919.9
    N= 9  raw 1133.9
    N=11  raw 1367.1

So `1/√N` over-normalises the live path, worst at low N — which is exactly the
layer-count dependence that was heard. `kInvPowN` fixes it: live rms is now
357–389 across N=3..11 against the synth's flat 443, spread 0.8 dB.

The two sources therefore **accumulate separately** in `RenderShepard` and are
normalised before the crossfade. Mixing first and normalising once applies the
wrong law to whichever source dominates.

## The source crossfade is EQUAL POWER, from `sin_lut`

The synth and the octave stack are unrelated signals, so a linear crossfade
loses 3 dB in the middle — the mix sat *below both sources*, heard as *"when
the X knob is CW the internal sounds massively dominate"*.

    mix    linear   equal-power
    0.50    0.707       1.000     <- the dip

`XfadeQ15` takes both legs out of the **existing sine table** — mix spans a
quarter turn, so `mix << 15` is one leg's phase and `(32767 - mix) << 15` the
other. Worst power deviation 0.0076%, no new table. Same economy as `HannQ15`.

## Crest factor: why the live path still sounds softer, and must

**Not a fault, and not to be "fixed".** Measured crest (peak/rms):

    synth  1.75 – 1.91        independent oscillators, peaks rarely coincide
    live   2.74 – 3.87        correlated copies, peaks line up

The rail limits *peak*; loudness follows *rms*. At matched peak the live path
carries ~6 dB less rms, so it reads quieter than its level suggests. Raising
its gain to match by ear would clip it — at N=11 it already has only 2.9 dB of
peak headroom against the synth's 7.7.

`shepsim.py::check_live_levels` pins all three of the above.

## The seam detector's third correction: a maximum must be UNIQUE

The seam measure was condemning **N=3 at a flat 1.0000**, identical to the
deliberately-broken control. It was a false positive, and it had been in the
committed tree.

At low layer counts the signal is a few low sines, so the largest
sample-to-sample jump in the whole render is **5 LSB** — the quantisation
floor — and thousands of samples tie at it. Whether one of those ties lands
inside the seam guard is then luck.

    real build N=3   max jump  5, shared by 11,183 samples  (1.456%)
    broken control   max jump  7, shared by     28 samples  (0.004%)

A genuine discontinuity is a large jump that **stands alone**. The tie fraction
separates the two cases by a factor of 350, so a maximum shared by more than
0.5% of samples now yields *"no unique jump"* — inconclusive — rather than a
verdict.

This is the **third** measure in that file to fail the same way: a ratio only
separates signal from noise when its denominator *is* the signal. Level
periodicity and HF splatter were both retired for it earlier.

## Test discipline

**If you change a DSP file, run the matching test.** They take seconds.

| File | Test | What it pins down |
|---|---|---|
| `shepard.cpp` | `tools/shepard_check.py` | window constant-sum, pow2 accuracy, 1/√N, split divisor, illusion continuity |
| `shepard.cpp` | `tools/quant_check.py` | scale tables, octave wrap, portamento direction, stepping |
| `octave.h` | `tools/octave_check.py` | transposition ratio, crossfade constant-sum, recycle rates, head rotation at the wrap |
| `ping.h` | `tools/ping_check.py` | pitch frozen after a strike, strikes differ, envelope range, voice stealing, polyphonic headroom |
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

Three things follow, and all are now in the code. The third was added later,
after the same failure recurred a third time — see finding 26:

1. **The verdict rests on seam step alone** — the largest sample-to-sample
   jump near a wrap boundary, over the largest jump anywhere. A real
   discontinuity puts the global maximum exactly at the seam and scores 1.00.
2. **The check calibrates itself every run.** It breaks the window on
   purpose first and confirms the measure responds, because a detector that
   never fires is indistinguishable from one that cannot fire. If the
   control fails to separate, it says so and withholds the verdict.
3. **The maximum must be UNIQUE for the ratio to mean anything.** At low layer
   counts the largest jump in the whole render is the quantisation floor, tied
   by thousands of samples, so the ratio measures luck. A maximum shared by
   more than 0.5% of samples now yields "no unique jump" — inconclusive —
   rather than a verdict.

Current result:

        N   seam step    max    ties   verdict
        3      1.0000      5   11183   no unique jump   (quantisation floor)
        4      0.4444      9     704   seamless
        6      0.7917     24     580   seamless
        8      0.9412     68      32   seamless
       11      0.9379    322       8   seamless
     control   1.0000      7      28   fires correctly

No wrap discontinuity where the measure can see one. N=3 is genuinely
undecidable this way — the signal has no jump large enough to compare against —
so that case rests on listening, and it has been listened to.
**Expect audible gentle beating at N=3 regardless** — that is three
oscillators, not a defect.

## Status: CONFIRMED ON HARDWARE, released

Every finding in this file has been heard on hardware and confirmed, including
the last round — the live-path normaliser (24), the equal-power crossfade (25)
and the HOLD move to Audio In 2 (27) were all verified working in a listening
session on 2026-08-30, after release.

That matters for how this file should be read. The measurements below are no
longer predictions awaiting a check; they are the settled behaviour of a card
that works. **Do not "fix" anything here on the strength of a host measurement
alone** — several of the entries above record exactly that mistake, where a
number improved and the sound got worse.

**Released as v1.0.0 on 2026-08-30**, `draft: false` / `Status: Released`, with
`UF2/shepard.uf2` populated and PR #392 opened against
`TomWhitwell/Workshop_Computer` as `releases/104_barbers_pole`.

Note the upstream registry schema differs from what this repo carried while
drafting, and a file can parse cleanly while still failing the PR check:

- **`short-description` is REQUIRED**, and `Description` is not an alias for it.
- **`controls.knobs[].when` must be an OBJECT** (`{z: middle|up|down}`), not a
  prose string; switch positions live under `controls.switch`; LED ids are
  strings (`LED0`), not integers.
- `date` is a valid alias for `date-created`.

Run the registry's own validator before submitting rather than assuming — it
found 25 warnings here that reading the file did not:

    cd ../Workshop_Computer/tools/sitegen && npm install
    npm run validate-info -- ../../releases/104_barbers_pole/info.yaml
    git diff --name-status -z upstream/main...HEAD |       node src/validate/prRulesCli.js OUT.json

The second one also catches `duplicate-uf2` — shipping `FLASHME/` alongside
`UF2/` trips it, since the two are byte-identical.

**Still open:**

- `panels/` is empty — no panel art yet. The only remaining chore.

**`docs/BRINGUP.md`** is the regression checklist, keyed to the symptom as it
was actually reported for each finding. Its CV Out 2 steps were dropped when
that meter was removed.
