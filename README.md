# SHEPARD

An infinite glissando for the Music Thing Modular Workshop System Computer —
a pitch that rises or falls forever without ever leaving its range.

> ## ⚠️ DRAFT — never tested on hardware
>
> This card has not been flashed or played. It compiles clean, and its DSP is
> verified by a host test suite, but **every description of how it sounds is a
> prediction from the algorithm — not an observation.**
>
> There is no released binary. The DSP load is unmeasured. Treat everything
> below as intent rather than as fact, and see [docs/BRINGUP.md](docs/BRINGUP.md)
> for the ordered first hardware session.

## What it does

A Shepard–Risset tone is a stack of components spaced exactly one octave apart,
all sliding together through a fixed spectral envelope. Each fades in at the
bottom, rises through the envelope, and fades out at the top. Because the stack
is octave-spaced, sliding it by one whole octave returns it to exactly where it
started — so the slide can loop forever and the ear never catches the seam.

Turn Main clockwise and the pitch climbs and keeps climbing. Turn it
anticlockwise and it falls without ever reaching the bottom. Park it at noon
and the stack sits still, a drone made of octaves.

The card does this two ways at once, and Y crossfades between them:

- **Internal voice** — a bank of 3 to 12 sine oscillators.
- **Live audio** — a Hilbert frequency shifter drags whatever is patched into
  Audio In 1 through the same structure.

## What to feed it

The internal voice needs nothing patched. It is a complete instrument on its
own: the illusion is most obvious with a slow glide and a high layer count.

The live path wants **sustained, harmonically rich** material — pads, drones,
noise, cymbals, feedback, a whole mix. Percussion works but reads as texture
rather than as pitch.

**What works poorly:** a solo melodic line. The shifter is inharmonic (see
below), so a recognisable tune comes back as a recognisable tune that has been
bent out of shape, which is usually not what is wanted.

## Controls

The switch's two stable positions select knob pages. Down is spring-loaded and
is a button, not a third page.

### Switch MIDDLE — page 1

| Knob | Function |
|---|---|
| **Main** | **Speed and direction.** Centre is stationary, with a deadzone so it can be found by feel. Anticlockwise descends, clockwise ascends. Summed with CV In 1. |
| **X** | **Density** — 3 to 12 octave layers. |
| **Y** | **Source** — fully anticlockwise is the internal voice, fully clockwise is the live-audio shifter, anywhere between is a blend. |

### Switch UP — page 2

| Knob | Function |
|---|---|
| **Main** | **Quantise** — smooth glissando, 12-ET chromatic, major, or pentatonic. |
| **X** | **Stereo width.** |
| **Y** | **Output level.** Holds its current value until actually moved, so changing page never drops the volume. |

### Switch DOWN — momentary

- **Short press** toggles **FREEZE**: the stack stops moving through the
  envelope but keeps sounding.
- **Hold for two seconds** toggles **SEALED** freeze, which also silences the
  live input — a true static hold. LED 3 pulses in sealed, steady in normal.
- Any short press returns to live.

### Jacks

| Jack | Function |
|---|---|
| Audio In 1 | Source for the frequency shifter, and for SPIRAL |
| CV In 1 | Bipolar speed offset, summed with Main |
| Pulse In 1 | Manual step — advances one scale degree in the stepped modes |
| Pulse In 2 | Freeze while high |
| Audio Out 1 / 2 | Stereo output |
| CV Out 1 | Master phase, a 0–5 V ramp — one cycle per octave |
| CV Out 2 | Measured DSP load |

## Why the live path sounds inharmonic

**A frequency shifter is not a pitch shifter.** It moves every partial by the
same number of *hertz*, where a pitch shifter multiplies every partial by the
same *ratio*. Feed it a 200 Hz tone with harmonics at 400, 600 and 800, shift
by 100 Hz, and they come back at 300, 500, 700 and 900 — ratios of 1.50, 1.25,
1.17 and 1.13. The harmonic series is broken and the result rings and clangs.

That is inherent to the technique and is **not a fault**. It is what Risset's
own tape pieces sound like, and it is why Y crossfades between the two sources
rather than the shifter simply replacing the oscillators. Use the internal
voice when the illusion should be smooth; bring in the shifter when it should
have teeth.

The illusion still works, because the *shift amounts* are octave-spaced across
the layers even though each individual layer is a linear shift.

## Stepped modes

With Main on page 2 past its first quarter, the glide snaps to a scale. The
transition is **slewed rather than snapped** — about 1.4 ms — because twelve
oscillators jumping together is audible as a thump even though a pitch step is
not itself a click.

Pulse In 1 advances one degree. Glide and stepping work together: park Main at
centre for pure manual stepping, or leave it off-centre and the pulses nudge an
already-moving glide.

## Reading the DSP load

CV Out 2 reports the measured cost of the audio ISR as a fraction of its
20.83 µs budget, live and with no smoothing, so it can be watched on a
voltmeter while knobs are moved. Full scale means the ISR is exactly filling
its budget.

This is the authority on whether the card fits — not any figure in this README.

**It has not been read yet.** The estimate is roughly 45–50% of budget, but the
sibling card SPECTRAL was modelled at 51% and measured 231% on real hardware.
Until a voltmeter says otherwise, the cost of this card is unknown.

## SPIRAL mode (alt-boot)

**Hold the switch DOWN at power-on.**

A delay whose feedback path is pitch-shifted, so every repeat comes back
transposed and the echoes spiral away upward or downward forever — the
time-domain cousin of what the card does in its normal mode.

| Knob | Function |
|---|---|
| Main (page 1) | Shift amount, ±12 semitones. Centre is unity — a clean, unshifted delay. |
| X (page 1) | Delay time, 25 ms to 1.365 s |
| Y (page 1) | Dry / wet |
| Main (page 2) | Feedback |

Feedback is capped at 0.95, higher than a plain delay would allow, because
SPIRAL is *supposed* to sustain. What keeps it stable is the pitch shift
itself: energy shifted up eventually leaves through Nyquist, and energy shifted
down leaves through the loop's highpass. At the centre deadzone neither escape
route operates, so a unity-shift setting at high feedback will ring for a long
time — that is the documented behaviour of that position, not a fault.

Even LEDs (0/2/4) show a normal boot, odd LEDs (1/3/5) the alt-boot.

## How it works

Everything is fixed point. The RP2040 has no FPU, so a float multiply costs
~360 ns against a 20.8 µs per-sample budget — a single float in the audio path
is not a slow choice, it is a broken one. Floats appear only at startup, to
fill the lookup tables.

Two economies make a twelve-layer stereo bank affordable at 192 MHz:

- **`inc[i] = inc0 << i`.** Because the layers are exactly an octave apart,
  every layer's frequency increment is layer 0's shifted left by *i*. One
  exponential lookup serves all twelve, and it runs at 1.5 kHz rather than
  48 kHz because the master phase moves at only a few octaves per second.
- **One Hilbert transform, twelve modulators.** The analytic signal depends
  only on the input, not on how far it is shifted, so running twelve
  transforms would cost twelve times as much for bit-identical results. They
  also *must* share one, or the layers decorrelate and the illusion smears.

The spectral envelope is `sin²(πu)`, which reuses the first half of the sine
table rather than needing one of its own. Its sum across the layers is exactly
N/2 for every layer count and every phase — the same constant-overlap property
that makes a Hann window work at 50% overlap, and the reason the level does not
pulse as layers cycle through.

Unlike its sibling card SPECTRAL, all the DSP runs in the audio ISR. An
additive bank has no frame structure to amortise, so a block renderer would
add latency and a class of ring-buffer race conditions for no benefit.

## Verification

The fixed-point DSP is verified on the host before anything is flashed. Each
check re-implements the integer algorithm and asserts against a reference:

| Test | Covers |
|---|---|
| `tools/shepard_check.py` | window constant-sum, pow2 accuracy, 1/√N, Nyquist, illusion continuity |
| `tools/quant_check.py` | scale tables, the octave wrap, portamento direction, stepping |
| `tools/hilbert_check.py` | allpass transfer function, quadrature, sideband rejection, both coefficient traps |
| `tools/spiral_check.py` | shift ratio, crossfade, buffer bounds, loop stability run at DC |
| `tools/passthru_check.py` | **end-to-end gain — the authority on scaling** |

Two of these found real bugs during development. `shepard_check.py` caught a
truncated window lookup that rippled 0.48% at odd layer counts while testing
clean at even ones; `passthru_check.py` caught output running 4 dB too hot,
sitting in soft clip up to 10% of the time.

### Hearing it before flashing

`tools/shepsim.py` runs the same integer engine and writes WAVs, so the card
can be listened to without hardware:

```
python tools/shepsim.py                  # the wrap-seam set
python tools/shepsim.py --all            # every mode
python tools/shepsim.py --analyse-only   # just the seam verdict, no files
python tools/shepsim.py --spectrogram    # PNGs as well (needs matplotlib)
```

It also answers the card's make-or-break question objectively — *is the octave
wrap detectable?* — by measuring the largest sample-to-sample jump near a wrap
against the largest anywhere in the signal. The check **calibrates itself**:
every run first breaks the window lookup on purpose and confirms the measure
responds, because a detector that never fires looks exactly like one that
cannot.

Current verdict: control (broken) 1.00, real build 0.44–0.96 at every layer
count. No wrap discontinuity.

**Listen for** a click, a lurch, a level dip, or a moment where the pitch
appears to reset rather than continue. `out_wrap_fast_N03.wav` glides an octave
per second, so any seam becomes a 1 Hz rhythm — far easier to notice than a
single event in a slow sweep. N=3 is the exposed case: fewest layers, least
masking.

**What is not a fault:** gentle beating at three layers. That is the sound of
three widely spaced oscillators, and the analysis specifically distinguishes it
from a wrap artefact.

## Building

```
cmake -G Ninja -B build -S .
ninja -C build
```

Then drag `build/shepard.uf2` onto the Pico.

## Status

**Not yet tested on hardware.** Everything above about how it sounds is a
prediction from the algorithm and from the host tests, not an observation.

`docs/BRINGUP.md` is an ordered first session with the card — what to check,
in what order, what each failure would look like, and which test to re-run for
each. Read CV Out 2 before judging the sound.

## Credits

Built on Chris Johnson's header-only
[ComputerCard](https://github.com/TomWhitwell/Workshop_Computer) library for
the Music Thing Modular Workshop System.

The Shepard tone is Roger Shepard's (1964); the continuous glissando form is
Jean-Claude Risset's.

Sibling card to [WorkshopSpectral](https://github.com/uglifruit/WorkshopSpectral),
whose `MulQ15`, soft clippers and boot/paging logic are reused here.

## License

MIT — see LICENSE.
