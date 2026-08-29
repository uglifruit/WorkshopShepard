// spiral.h - the alt-boot SPIRAL DELAY: a pitch-shifting feedback loop.
//
// A delay whose feedback path is pitch-shifted, so every repeat comes back
// transposed. Repeats climb or fall forever, spiralling away - the time-domain
// cousin of the Shepard illusion the card makes in its normal mode.
//
// STRUCTURE: two read heads running through one delay line at a fractional
// rate, half a window apart, crossfaded. The rotating-head pitch shifter -
// cheapest structure that stays robust without an FFT, and already proven on
// this hardware.
//
// Deliberately built from WorkshopSpectral's tape.h: the Q16 fractional read,
// the damping/DC-block loop, and both clippers are lifted rather than
// rewritten. All of them are already hardware-validated, and SoftClipOut in
// particular was fixed only AFTER a build reached hardware and clipped.

#ifndef SHEPARD_SPIRAL_H_
#define SHEPARD_SPIRAL_H_

#include <stdint.h>
#include <string.h>

#include "fixed.h"
#include "shepard.h"    // sin_lut, for the crossfade window

namespace shepard {

// 65536 samples = 1.365 s at 48 kHz. Power of two so the wrap is a mask -
// there is no hardware divide on this chip. Same size and type as
// WorkshopSpectral's tape buffer, so the RAM figure carries over: 128 KB.
static constexpr int kSpiralBits = 16;
static constexpr int kSpiralLen = 1 << kSpiralBits;
static constexpr uint32_t kSpiralMask = kSpiralLen - 1;

// Crossfade window length, in samples. Sets the head-recycle rate, which is
// the audible artefact of this kind of shifter:
//
//     shift    rate     recycle period   flutter
//     -12 st   0.500    0.341 s          2.9 Hz
//      -7 st   0.667    0.513 s          1.9 Hz
//      +7 st   1.498    0.342 s          2.9 Hz
//     +12 st   2.000    0.171 s          5.9 Hz
//
// 8192 keeps flutter at or below ~6 Hz even at the octave extremes - slow
// enough to read as shimmer rather than buzz. 16384 would halve the flutter
// but doubles transient smearing, which is the wrong trade for an effect whose
// input is already being fed back on itself.
static constexpr int kSpiralWin = 8192;

// Maximum loop gain, Q15. 31129/32768 = 0.95.
//
// HIGHER than TAPE's 0.85, deliberately. TAPE must always decay; SPIRAL is
// supposed to sustain - repeats climbing forever IS the instrument.
//
// What keeps it stable is not the damping filter. As WorkshopSpectral's notes
// warn at length, that lowpass has a DC gain of exactly 1.0 and contributes
// NOTHING to the decay. What actually bounds this loop is the pitch shift
// itself: energy shifted up eventually leaves through Nyquist, energy shifted
// down leaves through the loop highpass. That is why a shimmer can run at 0.95
// where a flat delay at the same gain would ring for minutes.
//
// At unity shift (the deadzone at noon) neither escape route operates, so the
// loop is a plain delay at 0.95 and WILL run long. That is the documented
// behaviour of the centre position, not a fault - tools/spiral_check.py
// reports the tail length at every setting so the figure is on record.
static constexpr int32_t kSpiralMaxFeedback = 31129;

class SpiralDelay {
 public:
  // The buffer is passed in rather than owned, so it can be SHARED with the
  // octave stack: SPIRAL only runs in alt-boot and the octave stack only in
  // normal boot, so they never coexist and 128 KB serves both.
  void Init(int16_t* buf) {
    buf_ = buf;
    memset(buf_, 0, sizeof(int16_t) * kSpiralLen);
    write_ = 0;
    read_q16_ = 0;
    damp_state_ = 0;
    dc_state_ = 0;
  }

  // in        the dry input, already scaled for the buffer
  // rate_q16  read-pointer advance: 65536 = unity, 32768 = -1 oct, 131072 = +1
  // time      Q15 delay length
  // feedback  Q15 loop gain
  // Returns the wet signal; the caller mixes it with the dry.
  int32_t __not_in_flash_func(Process)(int32_t in, uint32_t rate_q16,
                                       int32_t time, int32_t feedback) {
    // --- delay length -------------------------------------------------------
    // Guard band at both ends so neither head can reach the write pointer or
    // run off the interpolation edge.
    const int32_t min_samples = 1200;                          // 25 ms
    const int32_t span = kSpiralLen - min_samples - kSpiralWin - 512;
    // MulQ15 rather than an int64 product: span is ~55600 and time is at most
    // 32767, so the exact product needs 31 bits and would compile to a call to
    // __aeabi_lmul in the audio path. MulQ15 gives the same result in int32.
    const int32_t delay = min_samples + MulQ15(time, span);

    // --- two heads, half a window apart -------------------------------------
    // The read pointer advances at `rate` rather than 1.0, which is what
    // transposes. Because it drifts relative to the write head it must be
    // recycled, and recycling is what the crossfade hides.
    read_q16_ += rate_q16;
    // Power-of-two window, so the wrap is a mask rather than a modulo - there
    // is no hardware divide on this chip.
    const uint32_t phase = (read_q16_ >> 16) & (uint32_t)(kSpiralWin - 1);

    const uint32_t base = ((uint32_t)(write_ - delay) << 16);
    const uint32_t p0 = base + read_q16_;
    const uint32_t p1 = p0 + ((uint32_t)(kSpiralWin / 2) << 16);

    const int32_t s0 = ReadInterp(p0);
    const int32_t s1 = ReadInterp(p1);

    // Crossfade with sin^2 / cos^2, so the two gains sum to a constant. The
    // SAME constant-sum property the oscillator bank's window relies on, and
    // reusing sin_lut for a third time. A linear crossfade would amplitude-
    // modulate at the recycle rate, which is audible as tremolo.
    // phase / kSpiralWin as Q32. kSpiralWin is a power of two (8192 = 2^13),
    // so this is a shift, not a divide - and certainly not a 64-bit one.
    const uint32_t u = phase << (32 - 13);
    const int32_t g0 = HannQ15(u);
    const int32_t g1 = 32767 - g0;

    const int32_t wet = MulQ15(s0, g1) + MulQ15(s1, g0);

    // --- loop filtering -----------------------------------------------------
    // Both lifted verbatim from tape.h. The lowpass darkens successive repeats
    // (tone only - unity at DC, so it does NOT set the decay); the highpass
    // stops DC and rumble accumulating over a long tail.
    damp_state_ += (wet - damp_state_) >> 2;
    dc_state_ += (damp_state_ - dc_state_) >> 9;
    const int32_t damped = damp_state_ - dc_state_;

    // --- write back ---------------------------------------------------------
    const int32_t fb_amt = MulQ15(feedback, kSpiralMaxFeedback);
    const int32_t fb = MulQ15(damped, fb_amt);
    buf_[write_ & kSpiralMask] = (int16_t)Saturate(in + fb);
    write_ = (write_ + 1u) & kSpiralMask;

    return wet;
  }

  // Soft clip at the card's output scale, for the dry+wet sum.
  //
  // LIFTED FROM tape.h, including the derivation. Linear below the knee, then
  // a RATIONAL curve that approaches the limit asymptotically and never pins.
  // That last property is the point: the quadratic knee it replaced had a
  // narrow soft region and simply flattened above it, which is what made loud
  // repeats sound clipped on hardware even after the gain staging was right.
  //
  //     y = knee + over*room/(room + over),  over = |x| - knee
  static inline int32_t __not_in_flash_func(SoftClipOut)(int32_t x) {
    const int32_t lim = 2047;
    const int32_t knee = 1450;
    const int32_t ax = x < 0 ? -x : x;
    if (ax <= knee) return x;
    const int32_t over = ax - knee;
    const int32_t room = lim - knee;
    const int32_t y = knee + (over * room) / (room + over);
    return x < 0 ? -y : y;
  }

  // Soft clip into the int16 buffer. Lifted from tape.h unchanged.
  static inline int32_t __not_in_flash_func(Saturate)(int32_t x) {
    const int32_t lim = 32767;
    const int32_t knee = 24576;
    if (x > knee) {
      const int32_t over = x - knee;
      const int32_t room = lim - knee;
      if (over >= room * 2) return lim;
      // over is bounded by room*2 = 16382 here, so over*over fits int32
      // comfortably (2.7e8). tape.h used an int64 cast defensively; in the
      // audio path that costs an __aeabi_lmul call, and it is not needed.
      return knee + over - ((over * over) / (room * 4));
    }
    if (x < -knee) {
      const int32_t over = -knee - x;
      const int32_t room = lim - knee;
      if (over >= room * 2) return -lim;
      return -(knee + over - ((over * over) / (room * 4)));
    }
    return x;
  }

 private:
  // Q16 fractional read with linear interpolation. tape.h's, verbatim.
  int32_t __not_in_flash_func(ReadInterp)(uint32_t p_q16) const {
    const uint32_t idx = (p_q16 >> 16) & kSpiralMask;
    const int32_t frac = (int32_t)(p_q16 & 0xFFFF);
    const int32_t a = buf_[idx];
    const int32_t b = buf_[(idx + 1) & kSpiralMask];
    return a + (int32_t)(((b - a) * frac) >> 16);
  }

  int16_t* buf_ = nullptr;
  uint32_t write_;
  uint32_t read_q16_;
  int32_t damp_state_;
  int32_t dc_state_;
};

}  // namespace shepard

#endif  // SHEPARD_SPIRAL_H_
