// SHEPARD - a Shepard-Risset infinite-glissando card for the Music Thing
// Modular Workshop System Computer.
//
// A Shepard tone is octave-spaced components sliding together through a fixed
// spectral envelope. Each fades in at the bottom, rises, and fades out at the
// top; because the stack is octave-spaced, one full slide returns it to
// exactly where it started, so the glide can loop forever without a seam.
//
// Structure:
//   shepard.cpp   tables, window, scale quantisation  (tools/shepard_check.py,
//                                                      tools/quant_check.py)
//   hilbert.cpp   analytic pair coefficients          (tools/hilbert_check.py)
//   spiral.h      alt-boot pitch-shifting delay       (tools/spiral_check.py)
//   main.cpp      panel, CV, LEDs, and the audio ISR
//
// CORE SPLIT - deliberately NOT WorkshopSpectral's arrangement.
//
// That card puts its DSP on core 1 because an FFT frame costs ~0.5 ms, 24x the
// per-sample budget: it CANNOT run inline, and the ring buffer is the price of
// admission. An additive oscillator bank has no frame structure at all - every
// sample costs the same, there is nothing to batch and no deadline to miss.
//
// So everything runs in the audio ISR, and the expensive control-rate work
// (one pow2 lookup, the window weights, quantisation) runs inline every 32nd
// sample. That removes the cross-core protocol entirely, along with the ring
// races and stale-slot bugs that dominate the sibling card's debugging notes.
// It also degrades honestly: if this were ever too slow the ISR would overrun
// visibly, where a block renderer turns the same fault into an intermittent
// underrun that is miserable to reproduce.
//
// Core 1 stays free. CV Out 2 reports the real measured load - if that ever
// reads high, the control block is what moves across.

#include "ComputerCard.h"

#include "pico/multicore.h"
#include "hardware/timer.h"
#include "hardware/clocks.h"

#include "fixed.h"
#include "shepard.h"
#include "hilbert.h"
#include "spiral.h"

using namespace shepard;

// --- timing constants, all at 48 kHz ---------------------------------------

// Boot window. Two things must settle before the card can start: the ADC
// (~150 ms) and the SWITCH, which ComputerCard derives through a ~60 Hz filter
// starting at zero - and zero decodes as Down, so every boot reports Down for
// the first few milliseconds regardless of the real position.
//
// The filter maths says ~14 ms. NIBBLE-KO uses half a second and that is
// PROVEN on this hardware, which is worth more than a model that ignores
// supply ramp and ADC reference settling. Same figure here.
static constexpr int32_t kBootMute = 24000;     // 0.5 s

// How long the boot mode pattern stays up after the switch has been read.
// Runs past the end of the mute so it is readable rather than a flicker.
static constexpr int32_t kBootSplash = 36000;   // 0.75 s

// Switch debounce. ReadPanel runs at 48 kHz, so contact bounce on the
// momentary Down position would toggle freeze several times per press - and an
// EVEN number of toggles leaves it off, which reads as "freeze works only
// sometimes". 20 ms outrides bounce while staying far below a deliberate tap.
static constexpr int32_t kSwitchSettle = 960;

// Hold Down this long for SEALED freeze rather than normal freeze.
static constexpr int32_t kLongPress = 96000;    // 2 s

// How long the switch must sit still before a page change commits. Up and
// Middle are NOT adjacent - reaching Down from Up passes through Middle - so
// without this a slow flick registers the transit as a real page change and
// hands page 1 the knob positions meant for page 2. Audible as a lurch.
static constexpr int32_t kPageSettle = 576;     // 12 ms

// Control-rate divider. The pow2 lookup, window weights and quantisation run
// once per 32 samples (1.5 kHz). Worst-case pitch drift within a block is
// 1.6 cents at the fastest glide - inaudible, and it is a smooth staleness
// rather than a discontinuity. Power of two so the counter is a mask.
static constexpr uint32_t kControlMask = 31;

// Main knob deadzone, in ADC counts either side of centre. Gives a definite
// "stopped" position that can be found by feel.
static constexpr int32_t kDeadzone = 50;

// Glide rate curve coefficients - see the derivation in UpdateControl().
//
// rate_q32 = |defl| * kRateLinear + (defl^3 >> 30) * kRateCubic
//
// The linear term keeps the lower two thirds of the knob useful; the cubic
// term is what reaches a genuine siren at the stops. Full deflection is
// ~8 octaves/second, and about 1 oct/s sits at 70% travel.
static constexpr int32_t kRateLinear = 6;
static constexpr int32_t kRateCubic = 150;

// Base frequency of layer 0 when the master phase is at zero: A-1, 13.75 Hz.
// At 12 layers the top sits near 28 kHz, but its window gain is ~0 there so
// the alias is ~90 dB down. The increment is clamped anyway.
//
// As a Q32 phase increment at 48 kHz: 13.75 / 48000 * 2^32.
static constexpr uint32_t kBaseInc = 1229782u;

class ShepardCard : public ComputerCard {
 public:
  ShepardCard() {
    hilbert_.Init();
    spiral_.Init();

    boot_mute_ = kBootMute;
    page_ = 0;
    last_switch_ = Switch::Middle;

    for (int i = 0; i < kMaxLayers; ++i) {
      osc_q32_[i] = 0;
      mod_q32_[i] = 0;
      win_l_[i] = 0;
      win_r_[i] = 0;
    }

    // Page 1 defaults: stopped, mid density, fully internal voice.
    stored_[0][0] = 16384;    // Main: speed, centred = stationary
    stored_[0][1] = 16384;    // X: density
    stored_[0][2] = 0;        // Y: source - internal synth
    // Page 2 defaults: smooth glissando, mono, full level.
    stored_[1][0] = 0;        // Main: quantise = smooth
    stored_[1][1] = 0;        // X: stereo width = mono
    stored_[1][2] = 32767;    // Y: output level
  }

  virtual void __not_in_flash_func(ProcessSample)() override {
    const uint32_t t0 = timer_hw->timerawl;

    // --- boot mute --------------------------------------------------------
    if (boot_mute_ > 0) {
      --boot_mute_;

      // Alt-boot: hold the switch DOWN through power-on for SPIRAL mode.
      //
      // ONE reading, taken once the window has FULLY elapsed - never "Down
      // seen at any point during the window". The switch reads Down until it
      // settles, so a card that latches on any sighting latches on every boot:
      // WorkshopZX and WorkshopBio both shipped exactly that. Down is safe
      // here, despite being the unsettled value, precisely because the reading
      // happens once, after the filter has resolved from any start position.
      if (boot_mute_ == 0) {
        spiral_mode_ = (SwitchVal() == Switch::Down);
        boot_splash_ = kBootSplash;
        // Start on whichever page the switch is already showing, so the card
        // does not immediately page on the first move. A Down alt-boot springs
        // back to Middle, so page 1 is the right landing place for it.
        last_switch_ = SwitchVal();
        page_ = (last_switch_ == Switch::Up) ? 1 : 0;
        pending_page_ = page_;
      }

      // Boot splash. Half a second of silence with dark LEDs reads as a dead
      // card, so show which mode latched - NIBBLE-KO's convention: EVEN LEDs
      // (0/2/4) normal boot, ODD (1/3/5) alt-boot. Nothing is shown before the
      // switch is read; lighting a guess would be worse than lighting nothing.
      if (boot_splash_ > 0) {
        --boot_splash_;
        for (uint32_t i = 0; i < 6; ++i) {
          LedOn(i, ((i & 1u) == 1u) == spiral_mode_);
        }
      }

      AudioOut1(0);
      AudioOut2(0);
      CVOut1(0);
      CVOut2(0);
      return;
    }

    // --- boot splash, continuing past the mute ----------------------------
    if (boot_splash_ > 0) {
      --boot_splash_;
      for (uint32_t i = 0; i < 6; ++i) {
        LedOn(i, ((i & 1u) == 1u) == spiral_mode_);
      }
      if (boot_splash_ == 0) {
        // Hand the LEDs back cleanly - LedOn and LedBrightness drive the same
        // hardware, so leave nothing latched before the normal path takes over.
        for (uint32_t i = 0; i < 6; ++i) LedOff(i);
        led_phase_ = 240;
      }
    }

    ReadPanel();

    // --- control rate -----------------------------------------------------
    // Everything expensive lives here: one pow2 lookup, the window weights,
    // the scale search. 1.5 kHz rather than 48 kHz.
    if ((sample_count_ & kControlMask) == 0) {
      UpdateControl();
    }
    ++sample_count_;

    int32_t l = 0;
    int32_t r = 0;

    if (spiral_mode_) {
      RenderSpiral(&l, &r);
    } else {
      RenderShepard(&l, &r);
    }

    // Output level, slewed. The knob is live the instant the page changes, so
    // the target can step by the full range in one sample; a one-pole turns
    // that into a fade rather than a click.
    level_smooth_ += (param_level_ - level_smooth_) >> 8;
    l = MulQ15(l, level_smooth_);
    r = MulQ15(r, level_smooth_);

    AudioOut1((int16_t)SpiralDelay::SoftClipOut(l));
    AudioOut2((int16_t)SpiralDelay::SoftClipOut(r));

    // CV 1: master phase as a 0-5 V ramp, one cycle per octave. This is the
    // card's clock - patch it to watch the glide, or to drive something else
    // in step with it.
    CVOut1((int16_t)((int32_t)(master_out_q32_ >> 21) - 2048));

    // CV 2: measured DSP load, as a fraction of the per-sample budget. Full
    // scale = the ISR exactly filling its 20.83 us.
    //
    // This is the authority on whether the card fits, and it exists because
    // the sibling project was MODELLED at 51% and ran at 231%. No peak hold
    // and no decay - the live figure, so it can be watched on a meter while
    // knobs move to find which combination is expensive.
    {
      const uint32_t dt = timer_hw->timerawl - t0;
      load_us_ = dt;
      int32_t load = (int32_t)((dt * 2047u) / 21u);
      if (load > 2047) load = 2047;
      CVOut2((int16_t)load);
    }

    UpdateLeds();
  }

 private:
  // --- the oscillator bank ------------------------------------------------
  void __not_in_flash_func(RenderShepard)(int32_t* out_l, int32_t* out_r) {
    int32_t acc_l = 0;
    int32_t acc_r = 0;

    // The live-audio path shares ONE analytic pair across every layer. See
    // hilbert.h: N transforms would cost 12x for bit-identical results, and
    // the layers must share it to stay phase-coherent.
    int32_t hi = 0, hq = 0;
    if (source_mix_ > 0) {
      const int32_t in = sealed_ ? 0 : ((int32_t)AudioIn1() << 9);
      hilbert_.Process(in, &hi, &hq);
    }

    for (int i = 0; i < layers_; ++i) {
      // --- internal voice ---
      int32_t synth = 0;
      if (source_mix_ < 32767) {
        osc_q32_[i] += inc_[i];
        synth = SinQ15(osc_q32_[i]);
      }

      // --- live audio, frequency-shifted ---
      int32_t shifted = 0;
      if (source_mix_ > 0) {
        mod_q32_[i] += mod_inc_[i];
        const int32_t c = SinQ15(mod_q32_ [i] + 0x40000000u);   // cos
        const int32_t s = SinQ15(mod_q32_[i]);                  // sin
        // Sign of the Q term sets the direction. One flip, not two code paths.
        const int32_t q = shift_up_ ? hq : -hq;

        // Scale the analytic pair to FULL Q15 before modulating, so the live
        // path meets the synth path at the same level in the crossfade below.
        //
        // The input is left-shifted by 9 for the Hilbert filter's precision
        // (its Q30 coefficients need the headroom). Shifting by the same 9
        // here would exactly undo that, leaving the live signal at its raw
        // +-2048 while SinQ15 delivers +-32767 - a 24 dB mismatch, so Y read
        // as a fade to almost nothing rather than as a crossfade.
        //
        // Observed on hardware as "feeding audio in and changing blend gives
        // a very quiet signal". >> 5 instead: +-2^20 becomes +-32768, which
        // is full Q15 and matches the oscillator.
        //
        // The two quadrature terms sum in power, not amplitude, so the pair
        // peaks at ~1.41x a single term; the >> 1 keeps that inside Q15.
        shifted = (MulQ15(hi >> 5, c) + MulQ15(q >> 5, s)) >> 1;
      }

      // Crossfade the two sources, then apply this layer's window gain.
      const int32_t v = MulQ15(synth, 32767 - source_mix_) +
                        MulQ15(shifted, source_mix_);

      acc_l += MulQ15(v, win_l_[i]);
      acc_r += MulQ15(v, win_r_[i]);
    }

    // 1/sqrt(N), because the layers are incoherent so their POWERS add.
    //
    // The final >> 5 is the output scaling, and it is MEASURED, not assumed -
    // tools/passthru_check.py runs the whole integer chain and reports the
    // peak. At >> 4 the card peaks at 1754 against a 2047 rail and sits in
    // soft clip 2-10% of the time; at >> 6 it is needlessly quiet. >> 5 gives
    // about 7 dB of headroom with rms ~443.
    //
    // If any gain in this path changes, re-run that test. The sibling card
    // shipped a 512x scaling error to hardware that every MODELLED test
    // passed - only running the real arithmetic catches it.
    const int32_t g = kInvSqrtN[layers_];
    *out_l = MulQ15(acc_l, g) >> 5;
    *out_r = MulQ15(acc_r, g) >> 5;
  }

  // --- the alt-boot delay --------------------------------------------------
  void __not_in_flash_func(RenderSpiral)(int32_t* out_l, int32_t* out_r) {
    const int32_t in = sealed_ ? 0 : ((int32_t)AudioIn1() << 3);
    const int32_t wet =
        spiral_.Process(in, spiral_rate_q16_, spiral_time_, spiral_feedback_);

    // Dry and wet share the output budget. Returning >> 3 undoes the drive, so
    // "wet at unity" is the same size as the dry.
    const int32_t dry_sig = (int32_t)AudioIn1();
    const int32_t wet_sig = wet >> 3;

    const int32_t mixed = MulQ15(dry_sig, 32767 - spiral_mix_) +
                          MulQ15(wet_sig, spiral_mix_);
    *out_l = mixed;
    *out_r = mixed;
  }

  // --- control rate --------------------------------------------------------
  void __not_in_flash_func(UpdateControl)() {
    // Layer count from X, 3..12.
    //
    // Plain int32 throughout: a Q15 knob value is at most 32767 and the
    // multiplier is 10, so the product is 327670 - nowhere near needing 64
    // bits. An (int64_t) cast here would emit a call to __aeabi_lmul, which is
    // exactly what this card's arithmetic exists to avoid.
    layers_ = kMinLayers + ((stored_[0][1] * (kMaxLayers - kMinLayers + 1)) >> 15);
    if (layers_ < kMinLayers) layers_ = kMinLayers;
    if (layers_ > kMaxLayers) layers_ = kMaxLayers;

    source_mix_ = ClampQ15(stored_[0][2]);

    // Quantisation mode from page-2 Main, 0..3. Same reasoning.
    scale_ = (stored_[1][0] * kScaleCount) >> 15;
    if (scale_ >= kScaleCount) scale_ = kScaleCount - 1;
    if (scale_ < 0) scale_ = 0;

    // --- glide rate, from Main + CV 1 ---
    // The deadzone applies to the KNOB only; CV is summed after it, so CV can
    // drive the glide with the knob parked at centre. Without that a centred
    // knob would swallow small CV entirely.
    int32_t defl = stored_[0][0] - 16384;
    if (defl > -kDeadzone * 8 && defl < kDeadzone * 8) defl = 0;
    const int32_t cv = Connected(Input::CV1) ? (int32_t)CVIn1() : 0;
    defl += cv << 3;

    // Rate curve: a LINEAR term plus a CUBIC one.
    //
    // A pure cube was the first attempt and it was wrong twice over. It was far
    // too slow - two MulQ15s leave a full-scale cube at 4096 rather than 2^31,
    // so the `* 12` multiplier delivered 0.55 oct/s at full deflection against
    // a comment claiming 2. And the shape wasted the knob: at 70% travel it
    // gave 29 SECONDS per octave, which is not distinguishable from stopped.
    //
    // The cubic term alone is what makes the extremes reachable; the linear
    // term is what stops the first two thirds of the travel being dead. The
    // measured spread:
    //
    //     55% travel   0.12 oct/s    8.6 s per octave   a slow drift
    //     70%          0.88          1.1 s              clearly moving
    //     85%          3.1           0.32 s             a fast sweep
    //    100%          8.0           0.13 s             a siren
    //
    // Note the top speed costs precision: pitch drifts 6.4 cents within a
    // 32-sample control block at 8 oct/s, against 0.4 at the old top speed.
    // That is smooth staleness rather than a discontinuity - the pitch is
    // momentarily behind, then catches up - and at a rate this fast the ear
    // is tracking the sweep, not the tuning. If it ever reads as gritty,
    // lower kControlMask to 15 rather than capping the speed.
    const int32_t mag = defl >= 0 ? defl : -defl;
    const int32_t sq = MulQ15(defl, defl);
    const int32_t cube = MulQ15(sq, mag);
    int32_t rate = mag * kRateLinear + cube * kRateCubic;
    if (defl < 0) rate = -rate;
    rate_q32_ = rate;

    shift_up_ = (defl >= 0);

    if (spiral_mode_) {
      UpdateSpiralControl(defl);
      return;
    }

    // --- master phase ---
    // A free-running continuous phase always advances smoothly; the quantised
    // target is derived from it, and the phase actually USED slews toward that
    // target. Snapping directly would make all N oscillators jump together,
    // which is audible as a thump even though a pitch step is not itself a
    // click.
    //
    // MULTIPLIED BY THE CONTROL DIVIDER. rate_q32_ is a PER-SAMPLE increment,
    // but this runs once per control block, so advancing by it directly made
    // the glide 32x too slow - and worse, it made the pitch move in 32-sample
    // STEPS large enough to hear. On hardware that was "the pitch climbs, then
    // plateaus, then clicks and resets": the plateau is the master barely
    // moving, and the click is the accumulated error arriving all at once.
    //
    // The window and the layer frequencies both derive from master, so they
    // stayed consistent with each other - which is why the illusion still
    // half-worked and the fault read as a wrap artefact rather than as a
    // broken rate.
    master_free_q32_ += (uint32_t)rate_q32_ * (kControlMask + 1u);

    // Pulse In 1 advances one degree in stepped modes. Counted, not flagged -
    // a bool set by the ISR and cleared elsewhere loses any trigger arriving
    // between the read and the clear.
    const uint32_t tc = trigger_count_;
    if (tc != trigger_seen_) {
      trigger_seen_ = tc;
      if (scale_ != kScaleSmooth) {
        master_free_q32_ = StepScale(master_free_q32_, scale_,
                                     shift_up_ ? 1 : -1);
      }
    }

    if (scale_ == kScaleSmooth) {
      master_out_q32_ = master_free_q32_;
    } else {
      const uint32_t target = SnapToScale(master_free_q32_, scale_);
      // The int32_t cast is LOAD-BEARING: a signed difference takes the
      // shorter way round the octave automatically. Unsigned, a one-semitone
      // step would sometimes slew the long way - an eleven-semitone sweep in
      // the wrong direction.
      const int32_t err = (int32_t)(target - master_out_q32_);
      master_out_q32_ += (uint32_t)(err >> 6);      // ~1.4 ms approach
    }

    if (freeze_) {
      // Freeze holds the phase ADVANCE: the stack stops moving through the
      // envelope but the oscillators keep sounding.
      master_out_q32_ = frozen_q32_;
    } else {
      frozen_q32_ = master_out_q32_;
    }

    // --- per-layer increments and window gains ---
    // ONE pow2 lookup serves every layer: because the layers are exactly an
    // octave apart, inc[i] is inc[0] shifted left by i. This is the economy
    // that makes a 12-layer bank affordable at all.
    const int32_t m = Pow2Q30(master_out_q32_);
    // inc0 = kBaseInc * (m / 2^30), in int32 only.
    //
    // m is Q30 in [2^30, 2^31), so shifting it to Q15 first keeps the product
    // inside int32: 1229782 * 65535 would overflow, but 1229782 * 65535 >> 15
    // computed as (kBaseInc >> 5) * (m >> 15) >> 10 does not. The 5 bits given
    // up cost 0.003 cents of tuning, far below the LUT's own 0.0016.
    const uint32_t inc0 = ((kBaseInc >> 5) * (uint32_t)(m >> 15)) >> 10;

    // Shift amount for the live path, one octave apart per layer likewise.
    const uint32_t shift_base = (uint32_t)((rate_q32_ < 0 ? -rate_q32_
                                                          : rate_q32_) >> 6);

    // Window-position divisors, computed once per control block rather than
    // per layer. Three divides at 1.5 kHz instead of 24 at 48 kHz.
    const uint32_t n = (uint32_t)layers_;
    master_div_ = master_out_q32_ / n;
    oct_div_ = 0xFFFFFFFFu / n + 1u;              // 2^32 / n
    width_div_ = ((uint32_t)stored_[1][1] << 17) / n;

    for (int i = 0; i < layers_; ++i) {
      // Clamp rather than let an increment wrap: a wrapped increment is not a
      // subtle artefact, it is a loud wrong note.
      uint32_t inc = inc0 << i;
      if (i >= 20 || (inc0 != 0 && (inc >> i) != inc0)) inc = 0x7FFFFFFFu;
      if (inc > 0x7FFFFFFFu) inc = 0x7FFFFFFFu;
      inc_[i] = inc;

      mod_inc_[i] = shift_base << i;

      // Window position: u_i = (master_frac + i) / layers.
      //
      // Stereo width offsets the WINDOW, not the oscillator phase. Offsetting
      // phase would comb-filter on a mono sum; offsetting the window gives
      // amplitude decorrelation instead, which is mono-safe - summing L+R just
      // gives a different valid window sum, never a null.
      // u_i = (master_frac + i) / layers, in Q32.
      //
      // The numerator is master + i*2^32, which needs 36 bits - but the
      // division by `layers` brings it back under 32. Rather than carry a
      // 64-bit intermediate, split it: i*2^32/n is a whole-number-of-layers
      // offset computed once, and master/n fits in 32 bits directly.
      //
      //     (master + i*2^32) / n  ==  master/n + i*(2^32/n)   (to within 1 LSB)
      //
      // 2^32/n is exact for n = 4, 8 and approximate otherwise; the residual
      // is under 1 part in 2^32 and is absorbed by the window's smoothness.
      // Verified against the exact form in tools/shepard_check.py.
      const uint32_t u_l = master_div_ + (uint32_t)i * oct_div_;
      const uint32_t u_r = u_l + width_div_;
      win_l_[i] = (int16_t)HannQ15(u_l);
      win_r_[i] = (int16_t)HannQ15(u_r);
    }
  }

  void __not_in_flash_func(UpdateSpiralControl)(int32_t defl) {
    // Main sets the shift, +-12 semitones, with the same deadzone giving a
    // true unity-rate region at noon - a clean unshifted delay, and a
    // necessary reference point.
    // The shift is +-1 octave, and the knob is linear in PITCH across it.
    //
    // Deflection is +-16384, and one octave is 2^32 in the pow2 LUT's input,
    // so the conversion is a shift of 18 - not a Q15 semitone intermediate.
    // The first version went through `(defl * 12) >> 4` and then `<< 13`,
    // which does not land on the LUT's Q32 octave scale at all: it spanned
    // +-0.28 SEMITONES instead of +-12, a factor of 43. The alt-boot would
    // have sounded like a slightly detuned echo rather than a shimmer, and
    // nothing in the host tests covered it because spiral_check.py exercises
    // SpiralDelay::Process directly with a rate handed to it.
    const int32_t oct_q32 = defl << 18;               // +-1 octave

    // Pow2Q30 takes the FRACTIONAL part and returns 2^f in [1,2) as Q30. For
    // a negative octave the unsigned wrap gives the right fraction, and the
    // integer octave below is then one extra right shift.
    const int32_t p = Pow2Q30((uint32_t)oct_q32);
    uint32_t rate = (oct_q32 >= 0) ? (uint32_t)(p >> 14)   // 1.0 .. 2.0 in Q16
                                   : (uint32_t)(p >> 15);  // 0.5 .. 1.0
    if (rate < 16384u) rate = 16384u;
    if (rate > 262144u) rate = 262144u;
    spiral_rate_q16_ = rate;

    spiral_time_ = stored_[0][1];        // page 1 X
    spiral_mix_ = stored_[0][2];         // page 1 Y
    spiral_feedback_ = stored_[1][0];    // page 2 Main
  }

  // --- panel ---------------------------------------------------------------
  void __not_in_flash_func(ReadPanel)() {
    // Switch geometry: MIDDLE and UP are the two stable rest positions; DOWN
    // is spring-loaded momentary and returns to Middle when released. So Down
    // cannot hold a latched state - it is a button, and freeze is TOGGLED.
    const Switch sw = SwitchVal();
    if (switch_settle_ > 0) --switch_settle_;

    // Long press selects SEALED freeze. The decision has to happen on RELEASE
    // since at press time the length is unknown; the exception is the 2 s mark
    // itself, which engages while still held so there is feedback before
    // letting go.
    if (sw == Switch::Down) {
      if (down_held_ < kLongPress) {
        ++down_held_;
        if (down_held_ == kLongPress) {
          freeze_latch_ = true;
          sealed_latch_ = true;
        }
      }
    }

    if (sw != last_switch_ && switch_settle_ == 0) {
      const Switch prev = last_switch_;
      last_switch_ = sw;
      switch_settle_ = kSwitchSettle;

      if (sw == Switch::Down) {
        down_held_ = 0;
        // Reaching Down cancels any pending page change. Without this a flick
        // from Up that paused at Middle would arm a change and then commit it
        // while Down was held, dropping the page.
        page_settle_ = 0;
      } else if (prev == Switch::Down) {
        if (down_held_ < kLongPress) {
          if (freeze_latch_) {
            freeze_latch_ = false;
            sealed_latch_ = false;
          } else {
            freeze_latch_ = true;
            sealed_latch_ = false;
          }
        }
        down_held_ = 0;
      } else {
        // A page change between the two stable positions, DEFERRED - see
        // kPageSettle. Coming back from a Down press must not re-page.
        pending_page_ = (sw == Switch::Up) ? 1 : 0;
        page_settle_ = kPageSettle;
      }
    }

    if (page_settle_ > 0 && --page_settle_ == 0) {
      if (pending_page_ != page_) {
        page_ = pending_page_;
        if (page_ == 1) {
          // Capture the arrival position from the KNOB, not from stored_.
          //
          // stored_[1][2] holds whatever page 2 last wrote - on the first
          // visit that is the constructor default (32767), which has no
          // relationship to where Y is actually pointing. Capturing that made
          // the very first knob read look like a large movement, so
          // level_live_ latched true immediately and the level jumped to Y's
          // physical position. With Y anticlockwise that is SILENCE, and the
          // whole "hold until moved" protection did precisely the opposite of
          // its purpose.
          //
          // Observed on hardware as "switch upwards and everything goes
          // silent". KnobVal is the live ADC reading, so this is correct on
          // the first visit as well as on later ones.
          level_arrival_ = KnobVal(Knob::Y) << 3;
          level_live_ = false;
        }
      }
    }

    // Pulse In 2 freezes while held, independently of the latch. Always normal
    // freeze - sealed is a deliberate, held-switch act.
    const bool pulse2 = PulseIn2();
    freeze_ = freeze_latch_ || pulse2;
    sealed_ = sealed_latch_ && !pulse2;

    // Pulse In 1: counted on the rising edge.
    const bool pulse1 = PulseIn1();
    if (pulse1 && !last_pulse1_) ++trigger_count_;
    last_pulse1_ = pulse1;

    // One knob per sample, round-robin. Each still updates at ~16 kHz, far
    // faster than a hand.
    switch (panel_phase_) {
      case 0: UpdateKnob(0, KnobVal(Knob::Main)); break;
      case 1: UpdateKnob(1, KnobVal(Knob::X)); break;
      case 2: UpdateKnob(2, KnobVal(Knob::Y)); break;
      default: break;
    }
    if (++panel_phase_ > 2) panel_phase_ = 0;

    // OUTPUT LEVEL holds its previous value until Y is actually MOVED on this
    // visit. Arriving on page 2 with Y at 9 o'clock and suddenly playing at a
    // quarter volume reads as a fault rather than as a control.
    param_level_ = level_live_ ? stored_[1][2] : level_held_;
    if (level_live_) level_held_ = stored_[1][2];
  }

  // Knobs are always LIVE on the page being looked at. Pickup was tried on the
  // sibling card and removed: after every page change all three knobs felt
  // dead, with no indication that anything was waiting.
  void __not_in_flash_func(UpdateKnob)(int idx, int32_t raw) {
    const int32_t val = raw << 3;      // KnobVal is 0..4095; params are Q15
    if (page_ == 1 && idx == 2 && !level_live_) {
      const int32_t d = val - level_arrival_;
      if (d > 1200 || d < -1200) level_live_ = true;
    }
    stored_[page_][idx] = val;
  }

  void __not_in_flash_func(UpdateLeds)() {
    // The boot splash owns the LEDs while it runs - without this the normal
    // refresh repaints them within ~5 ms and the mode pattern is never seen.
    if (boot_splash_ > 0) return;

    if (++led_phase_ < 240) return;    // ~200 Hz is plenty for LEDs
    led_phase_ = 0;

    // Top row: the master phase as a moving dot, so the glide is visible even
    // when it is too slow to hear moving.
    const uint32_t p = master_out_q32_ >> 30;      // 0..3
    for (uint32_t i = 0; i < 3; ++i) {
      LedBrightness(i, (uint16_t)((p == i) ? 3000 : 200));
    }

    // Bottom row: freeze state and page.
    led_pulse_ += 40;
    const int32_t tri = (led_pulse_ & 2047) < 1024
                            ? (led_pulse_ & 1023)
                            : 1023 - (led_pulse_ & 1023);
    if (sealed_) {
      LedBrightness(3, (uint16_t)(1000 + tri * 3));   // pulse = SEALED
    } else {
      LedBrightness(3, freeze_ ? 4095 : 0);           // steady = normal
    }

    // In SPIRAL mode both page LEDs carry a slow counter-phase glow, so the
    // alt-boot stays obvious after the splash has gone.
    if (spiral_mode_) {
      const uint16_t glow = (uint16_t)(400 + tri);
      LedBrightness(4, page_ == 0 ? 2500 : glow);
      LedBrightness(5, page_ == 1 ? 2500 : glow);
    } else {
      LedBrightness(4, page_ == 0 ? 2500 : 0);
      LedBrightness(5, page_ == 1 ? 2500 : 0);
    }
  }

  Hilbert hilbert_;
  SpiralDelay spiral_;

  int32_t boot_mute_;
  int32_t boot_splash_ = 0;
  bool spiral_mode_ = false;
  uint32_t sample_count_ = 0;

  int panel_phase_ = 0;
  int page_;
  Switch last_switch_;
  int32_t switch_settle_ = 0;
  int32_t down_held_ = 0;
  int32_t page_settle_ = 0;
  int pending_page_ = 0;
  bool last_pulse1_ = false;

  int32_t stored_[2][3];      // [page][knob]

  // Oscillator bank state.
  uint32_t osc_q32_[kMaxLayers];
  uint32_t mod_q32_[kMaxLayers];
  uint32_t inc_[kMaxLayers];
  uint32_t mod_inc_[kMaxLayers];
  int16_t win_l_[kMaxLayers];
  int16_t win_r_[kMaxLayers];

  uint32_t master_div_ = 0;      // master_out_q32_ / layers
  uint32_t oct_div_ = 0;         // 2^32 / layers
  uint32_t width_div_ = 0;       // stereo width offset / layers

  uint32_t master_free_q32_ = 0;
  uint32_t master_out_q32_ = 0;
  uint32_t frozen_q32_ = 0;
  int32_t rate_q32_ = 0;
  int layers_ = 6;
  int scale_ = 0;
  int32_t source_mix_ = 0;
  bool shift_up_ = true;

  // SPIRAL state.
  uint32_t spiral_rate_q16_ = 65536;
  int32_t spiral_time_ = 16384;
  int32_t spiral_feedback_ = 16384;
  int32_t spiral_mix_ = 16384;

  bool freeze_ = false;
  bool sealed_ = false;
  bool freeze_latch_ = false;
  bool sealed_latch_ = false;
  uint32_t trigger_count_ = 0;
  uint32_t trigger_seen_ = 0;

  int32_t param_level_ = 32767;
  int32_t level_smooth_ = 32767;
  int32_t level_held_ = 32767;
  int32_t level_arrival_ = 0;
  bool level_live_ = false;

  uint32_t load_us_ = 0;
  int led_phase_ = 0;
  int32_t led_pulse_ = 0;
};

// Global, not on the stack: the SPIRAL buffer alone is 128 KB and the stack is
// only 4 KB.
ShepardCard card;

int main() {
  // 192 MHz. Proven on this hardware by grains/51 and glitter/53, and by the
  // sibling card.
  set_sys_clock_khz(192000, true);

  ShepardInit();

  card.EnableNormalisationProbe();
  card.Run();
}
