# SHEPARD — hardware bring-up

An ordered first session with the card. The point of the order is that each
step depends only on things already confirmed, so when something fails you
know the fault is in what you just added rather than anywhere in the card.

Nothing here has been done yet. Every expectation below is a prediction from
the algorithm and the host tests.

**Flash `FLASHME/shepard.uf2`** (hold BOOTSEL, drag, it reboots on its own).

That file is refreshed automatically by every build, so it always matches the
source you have. It did not used to be — it was copied by hand once and went
stale, holding a build from before two knob mappings were fixed. If you ever
find the card behaving in a way the documentation flatly contradicts, check
that first: rebuild, and compare timestamps.

    ninja -C build
    ls -l FLASHME/shepard.uf2 build/shepard.uf2

---

## 0. Before you plug anything in

Have to hand:

- A voltmeter for CV Out 2. This is the single most informative instrument
  for this card — it reports the real DSP load, and the whole cost model is
  unverified until you read it.
- Headphones or a monitor on Audio Out 1 and 2.
- Nothing patched into the inputs yet. The internal voice needs no input, and
  starting with a bare card removes a whole class of confusion.

---

## 1. Does it boot at all?

**Switch MIDDLE, power on.**

| Expect | Meaning |
|---|---|
| LEDs 0, 2, 4 light together for ~0.75 s | normal boot latched |
| Then LED 4 steady, LEDs 0–2 showing a moving dot | running |

**If the LEDs stay dark:** the card is not running. Most likely the crystal
startup delay — check `PICO_XOSC_STARTUP_DELAY_MULTIPLIER=64` survived the
build. The signature of that bug is *fails on cold power-up, works from a warm
reset*, so try a reset before concluding anything.

**If LEDs 1, 3, 5 light instead:** the alt-boot latched when it should not
have. That is the WorkshopZX/WorkshopBio bug — the switch being read before it
settles — and it would mean the single-reading logic is wrong.

---

## 2. The DSP load, before anything else

**Meter on CV Out 2.** Sweep X (density) from fully anticlockwise to fully
clockwise, with Y at both extremes.

| Reading | Load | Verdict |
|---|---|---|
| ~1.5–2.5 V | 30–50% | as predicted, comfortable |
| ~3.5 V | ~70% | tighter than modelled but fine |
| 5 V (pinned) | ≥100% | **stop** — the ISR is overrunning |

**Do this before judging the sound.** An overrunning ISR produces artefacts
that are easy to mistake for DSP bugs, and chasing those first wastes the
session.

The estimate is ~22% raw, ~45–50% compiled. It is an *estimate*: the sibling
card SPECTRAL was modelled at 51% and measured 231%. This meter is the
authority.

**Worst case is X fully clockwise (12 layers) with Y at noon** — both engines
running at full density. Check that corner specifically.

**If it pins:** the fix in order of preference is (1) cap the live-audio path
to fewer layers than the internal voice, (2) move `UpdateControl` to core 1
(see CLAUDE.md — it needs a `__dmb()` before the index flip), (3) reduce
`kMaxLayers`.

---

## 3. The illusion — the make-or-break test

**Switch MIDDLE. X to about 2 o'clock (8–10 layers). Y fully anticlockwise
(internal voice only). Main slightly clockwise of centre.**

Expect a pitch that rises and keeps rising, with no point at which it audibly
restarts.

Listen for a full minute. The wrap arrives every few seconds at this rate; the
question is whether you can *hear* where.

| Symptom | Likely cause |
|---|---|
| A click once per cycle | window not reaching zero at the boundary |
| A level dip once per cycle | window sum not constant — run `shepard_check.py` |
| Pitch audibly "resets" | layer increments wrong; check `inc[i] = inc0 << i` |
| Gentle beating, no distinct event | **normal** — see below |

**Then turn X fully anticlockwise (3 layers).** This is the exposed case: the
fewest components, so the least masking.

**Expect audible gentle beating here, and do not treat it as a fault.** Three
widely spaced oscillators beat against each other; the host analysis
specifically distinguishes that from a wrap artefact and found no
discontinuity. What would be a fault is a *click* or a distinct event at a
regular interval.

Compare against `tools/out_wrap_fast_N03.wav` if in doubt — that render glides
an octave per second, so a seam would appear as a 1 Hz rhythm.

---

## 4. Direction and the deadzone

- **Main anticlockwise of centre** — pitch should fall forever.
- **Main at centre** — should be *completely still*. A steady octave-stack
  drone with no drift.

Any slow wandering at centre means the deadzone is not zeroing the rate.

Find the deadzone edges by ear: there should be a definite region either side
of noon where nothing moves, wide enough to locate by feel.

---

## 5. Density and level

Sweep **X** slowly, fully anticlockwise to fully clockwise, with Main gliding.

The **perceived loudness should not change.** That is what 1/√N buys, and the
host test says it holds within 0.10 dB.

If dense settings sound noticeably quieter, the normalisation is wrong —
1/N rather than 1/√N would produce exactly that.

What *should* change is the texture: sparse and hollow at 3, smooth and
seamless at 12.

---

## 6. CV Out 1 — the phase ramp

**Meter or scope on CV Out 1.** A 0–5 V ramp, one full cycle per octave,
resetting cleanly.

Patch it to an oscillator's pitch input if you want to hear the glide
transposed onto something else. It is the card's clock.

---

## 7. CV In 1 — speed control

**Park Main exactly at centre** (stationary), then patch a slow LFO or an
offset into CV In 1.

The glide should respond. This specifically tests that CV is summed *after*
the deadzone — if a centred knob swallows the CV entirely, that ordering is
wrong.

---

## 8. Freeze

- **Short press Down** — the stack stops moving but keeps sounding. LED 3
  steady.
- **Press again** — resumes.
- **Press several times quickly** — must toggle reliably every time. If it
  works only sometimes, the debounce has failed: contact bounce toggling an
  even number of times leaves it off.
- **Hold Down for 2 s** — LED 3 changes to a slow pulse at the 2 s mark, while
  still held. That is SEALED freeze.
- **Gate into Pulse In 2** — freezes while high, independently of the switch.

---

## 9. Page 2

**Flick to UP.** LED 5 should light instead of LED 4.

- **Main** — quantisation. Anticlockwise quarter is smooth; beyond that the
  glide snaps to scale degrees. Steps should be *articulated but not clicky*
  (the slew is 1.4 ms).
- **X** — stereo width. At zero, both channels identical. **Check the mono
  sum**: L+R must not thin out or cancel at any setting. Host test says
  −0.22 dB worst.
- **Y** — output level. **Flick between pages with Y at some odd position and
  confirm the volume does not jump.** The level should hold until Y is
  actually moved.

**Flick between MIDDLE and UP repeatedly, including quickly.** No lurch in the
sound. Page changes are deferred 12 ms precisely because Up and Middle are not
adjacent — a flick to Down passes through Middle.

---

## 10. Pulse In 1 — stepping

**Page 2 Main past its first quarter** (a stepped mode). **Page 1 Main at
centre.**

Each pulse should advance exactly one scale degree. No skipped steps, no
double steps — the trigger is counted rather than flagged specifically so a
pulse arriving at an awkward moment is not lost.

Then move Main off centre: the glide should run *and* pulses should nudge it.
Both work together.

---

## 11. The live path

**Patch a sustained source into Audio In 1** — a pad, a drone, noise, feedback.
**Y fully clockwise.**

**Expect it to sound inharmonic and clangy.** A frequency shifter moves
partials by hertz, not by ratio, so a harmonic input comes back stretched.
That is the mode, not a fault.

What *would* be a fault: a strong unshifted copy of the input coming through.
That would mean the quadrature is broken — the sideband rejection is ~35 dB
worst case, so any leak should be well down.

**Then sweep Y through noon.** The blend of internal voice and shifted input is
the most musical setting and the one worth spending time on.

---

## 12. SPIRAL (alt-boot)

**Hold the switch DOWN while powering on.** LEDs 1, 3, 5 should light — odd
LEDs mean alt-boot. Both page LEDs then carry a slow counter-phase glow so the
mode stays obvious.

| Knob | Check |
|---|---|
| Main (page 1) | centre = clean unshifted delay; either side transposes repeats |
| X (page 1) | delay time, 25 ms to 1.365 s |
| Y (page 1) | dry/wet |
| Main (page 2) | feedback |

**At high feedback with Main off-centre**, repeats should climb or fall away
into a shimmer and eventually leave — energy shifted up exits through Nyquist,
down through the loop highpass.

**At high feedback with Main at centre (unity)**, neither escape route
operates, so it will ring for a long time. That is documented behaviour of that
position, not a fault.

Nothing should ever grow without bound or lock into a clipped roar. The host
test runs the real loop at DC and found it stable at every setting.

---

## Recording notes

Whatever you find, put it in `CLAUDE.md` under a "measured on hardware"
heading, especially:

- the actual CV Out 2 reading at the worst-case corner
- whether the wrap is audible at N=3, and at N=12
- anything that sounded wrong and what fixed it

The value of that sibling card's notes is entirely that they record what was
*measured* rather than what was expected. Same here.
