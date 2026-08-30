# SHEPARD

An infinite glissando for the Music Thing Modular Workshop System Computer —
a pitch that rises or falls forever without ever leaving its range.

> ## ⚠️ DRAFT — on hardware, still being shaken out
>
> The card has been flashed and played. Several rounds of listening have found
> and fixed real bugs — a click at the loop, a silent page, a too-quiet blend,
> a glide running 32× slow — but it is **not finished**, and the fixes from the
> most recent round have not themselves been heard yet.
>
> There is no released binary and the DSP load is still unmeasured. See
> [docs/BRINGUP.md](docs/BRINGUP.md) for the ordered hardware session.

## What it does

A Shepard–Risset tone is a stack of components spaced exactly one octave apart,
all sliding together through a fixed spectral envelope. Each fades in at the
bottom, rises through the envelope, and fades out at the top. Because the stack
is octave-spaced, sliding it by one whole octave returns it to exactly where it
started — so the slide can loop forever and the ear never catches the seam.

Turn Main clockwise and the pitch climbs and keeps climbing. Turn it
anticlockwise and it falls without ever reaching the bottom. Park it at noon
and the stack sits still, a drone made of octaves.

The speed range is deliberately wide, and weighted heavily toward the slow end:
about 46 seconds per octave just off centre, roughly a second per octave at
85% of the travel, and up to eight octaves per second at the stops.

That weighting is not arbitrary. **A Shepard tone only works while you cannot
track the octave cycle** — above about one octave per second the cycle repeats
often enough to hear, and it stops sounding like an endless rise and starts
sounding like a climbing line played over and over. So most of the knob lives
below that threshold, and the last stretch is there for when you want the
siren rather than the illusion.

The card does this two ways at once, and Y crossfades between them:

- **Internal voice** — a bank of 3 to 12 sine oscillators.
- **Live audio** — your signal, octave-stacked: every partial transposed into
  the same octave-spaced layers as the oscillators, gliding forever through
  the same window.

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

**Knobs use pickup across a page change.** Arriving on a page, each knob holds
its previous value until you move it — otherwise every page flick would edit
three parameters at once. While a knob is waiting its LED pulses (left to
right: Main, X, Y), so you can see which ones are still to be picked up.

### Switch MIDDLE — page 1

| Knob | Function |
|---|---|
| **Main** | **Speed and direction.** Centre is stationary, with a deadzone so it can be found by feel. Anticlockwise descends, clockwise ascends. About 85% of the travel sits inside the illusion (46 s down to 1 s per octave); the last stretch runs out to a deliberate siren at 0.13 s. Summed with CV In 1. |
| **X** | **Density** — 3 to 12 octave layers, stepped discretely with hysteresis. Adding a layer respaces the whole stack, so the change is audible by nature; the output level is slewed across it rather than stepping too. Summed with CV In 2. |
| **Y** | **Source** — fully anticlockwise is the internal voice, fully clockwise is the live-audio shifter, anywhere between is a blend. The shifter works with Main parked at noon: it is an effect on the input, not a function of movement. |

### Switch UP — page 2

| Knob | Function |
|---|---|
| **Main** | **Quantise** — smooth glissando, 12-ET chromatic, major, minor, or pentatonic. In a rising glide the chromatic scale is nearly indistinguishable from smooth; the modal settings are what change the character. |
| **X** | **Stereo width** — deliberately subtle. It shifts the window slightly between channels, which spreads the image without ever separating an octave from its neighbours. Wider schemes were tried and they break the illusion: panning octaves apart tells the ear they are separate sources, and the stack stops fusing into one endless rise. |
| **Y** | **Output level.** Holds its current value until actually moved, so changing page never drops the volume. |

### Switch DOWN — momentary

- **Short press** toggles **FREEZE**: the stack stops moving through the
  envelope but keeps sounding. Pulse In 1 does the same from a gate, and
  resumes from exactly where it paused rather than jumping ahead.
- **Hold for two seconds** toggles **SEALED** freeze, which also silences the
  live input — a true static hold. LED 3 pulses in sealed, steady in normal.
- Any short press returns to live.

### Jacks

| Jack | Function |
|---|---|
| Audio In 1 | Source for the frequency shifter, and for SPIRAL |
| CV In 1 | Bipolar speed offset, summed with Main |
| CV In 2 | Bipolar density offset, summed with X |
| Pulse In 1 | **Freeze while high** — resumes from where it stopped, and advances one scale degree on the rising edge in stepped modes |
| Pulse In 2 | **Reverse direction while high** |
| Audio Out 1 / 2 | Stereo output |
| CV Out 1 | Master phase, a 0–5 V ramp — one cycle per octave |
| CV Out 2 | Measured DSP load |
| Pulse Out 1 | One trigger per octave wrap — the card's own clock, locked to the glide |

## The octave stack

The live path transposes whatever is patched into Audio In 1 into **N
octave-spaced copies**, weighted by the same window as the oscillator bank. A
220 Hz partial appears at 55, 110, 220 and 440 Hz — and each copy rises and
fades exactly as an oscillator layer does.

That is the point: the internal voice is octave-stacked sines, and this is
octave-stacked *you*. Your source doesn't have an effect applied over it; it
becomes the illusion.

Feed it anything with clear pitch content — a voice, a synth line, a drone, a
chord. Percussion works too but the transposed copies smear, since each read
head spans a 341 ms window.

**Grain length varies with the shift ratio**, and this is deliberate. The read
window is a fixed span of the buffer, so a head playing at quarter speed
produces grains four times longer in real time — descending lines audibly
lengthen before being replaced by short high ones. Compensating for it gives
uniform grains but wrecks the downshifts (the lowest layer drops to the point
where artefacts are louder than the wanted octave), so quality wins. The
barber's pole works macroscopically; an individual line shows its grain.

Two earlier designs were tried and rejected, and the reason is the same for
both: they treated the input as something to *process* while the structure
moved past it. A **frequency shifter** moved every partial by the same hertz,
breaking the harmonic series and making the source unrecognisable. A **rising
comb filter** swept resonant bands across the input, so any given partial was
only heard while a band happened to cross it — it came and went rather than
participating in the glide.

## Stepped modes

With Main on page 2 past its first quarter, the glide snaps to a scale. The
transition is **slewed rather than snapped** — about 1.4 ms — because twelve
oscillators jumping together is audible as a thump even though a pitch step is
not itself a click.

Pulse In 1 advances one degree. Glide and stepping work together: park Main at
centre for pure manual stepping, or leave it off-centre and the pulses nudge an
already-moving glide.

**Pulse Out 1 fires once per octave wrap.** Self-patch it to Pulse In 1 and the
card advances a scale step every time the stack completes a cycle; or use it to
clock something else in time with the illusion. At a slow glide that is a pulse
every few seconds, at full speed about eight per second.

## Reading the DSP load

CV Out 2 reports the measured cost of the audio ISR as a fraction of its
20.83 µs budget, live and with no smoothing, so it can be watched on a
voltmeter while knobs are moved. Full scale means the ISR is exactly filling
its budget.

This is the authority on whether the card fits — not any figure in this README.

**It has not been read yet.** The estimate is roughly 45–50% of budget, but the
sibling card SPECTRAL was modelled at 51% and measured 231% on real hardware.
Until a voltmeter says otherwise, the cost of this card is unknown.

## PING mode (alt-boot)

**Hold the switch DOWN at power-on.**

The pole keeps climbing — but silently. A trigger on Pulse In 1 voices a
**ping** at whatever pitches the pole has reached, and that ping decays
*without climbing*, because it is a snapshot rather than a window onto the
moving stack.

So the illusion runs the whole time and you only hear where it happens to be
at the moments you strike it. Trigger repeatedly and each ping comes from a
different point on the pole, building a chord out of one silently-rising
structure. Because the pole wraps every octave, the pitches available are
endless but bounded — the barber's pole becomes something you play rather than
something you listen to.

### Switch MIDDLE — page 1

| Knob | Function |
|---|---|
| **Main** | **Pole speed** — how fast the invisible climb moves between strikes, and which way |
| **X** | **Density** — 3 to 12 layers in each strike |
| **Y** | **Source** — internal sines anticlockwise, your own input octave-stacked clockwise |

### Switch UP — page 2

| Knob | Function |
|---|---|
| **Main** | **Quantise** — smooth, 12-ET, major, minor, pentatonic. Strikes land on scale degrees rather than anywhere on the glide |
| **X** | **Ping decay** — 17 ms click to a 1.7 s ring |
| **Y** | **Output level** |

Page 1 is identical to normal boot. On page 2 only X differs: it is decay here
rather than stereo width, and width sits at a fixed modest spread.

**Knobs use pickup across a page change** — arriving on a page, each knob holds
its previous value until moved about 4°, and its LED pulses until it does. Page
2 starts at smooth / shortest decay / full level, so on the first visit the
decay knob does nothing until you nudge X.

Four voices, oldest stolen when all are busy, so the newest four strikes are
always the ones sounding.

**Patch Pulse Out 1 back into Pulse In 1** and the card strikes itself once per
octave — a self-playing instrument whose pitch sequence never repeats within a
cycle. Or drive Pulse In 1 from anything else to play it deliberately.

Note that Pulse In 1 does **not** freeze the pole in this mode — it is the
strike trigger here, and the pole keeps climbing underneath regardless of how
fast you play. The Down switch still freezes it by hand, which holds the pole
still so every strike gives the same chord.

With Y clockwise the live input is **gated by the ping envelope and transposed
to the struck pitches**, so your own signal is what rings — the pole's pitch
appears in your source material rather than alongside it.

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

Then hold BOOTSEL and drag `FLASHME/shepard.uf2` onto the Pico. Every build
refreshes that copy, so it always matches the source you just compiled.

`UF2/` is for released binaries and is populated deliberately, not by the
build — it is empty until the card has been tested on hardware.

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
