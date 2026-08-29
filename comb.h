// comb.h - the rising comb filter: the live-audio half of the card.
//
// N narrow resonant bandpasses, centred on the SAME octave-spaced frequencies
// as the oscillator bank and weighted by the SAME window. As the master phase
// glides, every band centre slides upward together, fading in at the bottom
// and out at the top - so whatever is patched in appears to be filtered by a
// comb that rises forever.
//
// This REPLACED a Hilbert frequency shifter, which was the original design and
// was wrong for the job in two ways:
//
//   1. A frequency shifter is LINEAR (every partial moves by the same hertz),
//      so it breaks the harmonic series and clangs. That was inherent, not a
//      bug, but it meant the live path never sounded like the card.
//   2. It processed the input into something unrecognisable rather than
//      FILTERING it, so the relationship between what went in and what came
//      out was inaudible.
//
// A comb keeps the source recognisable and applies the illusion to it, which
// is what "Shepard tones on live audio" should mean. It is also CHEAPER:
// ~264 cycles at N=12 stereo against ~510 for one Hilbert pair plus twelve
// complex modulators.
//
// Topology is the Chamberlin state-variable filter - the cheapest resonant
// bandpass in fixed point, two multiplies and four adds per band:
//
//     lp += f * bp
//     hp  = x - lp - q * bp
//     bp += f * hp
//
// with f = 2*sin(pi*fc/SR) in Q20. `bp` is the bandpass output.

#ifndef SHEPARD_COMB_H_
#define SHEPARD_COMB_H_

#include <stdint.h>
#include <string.h>

#include "fixed.h"
#include "shepard.h"

namespace shepard {

// Resonance. Lower q is narrower and more resonant; the filter is stable while
// q > f, so this also sets the highest usable centre frequency.
//
// 2000 (Q ~ 16) measured 16386 at the centre against ~680 an octave away -
// about 27 dB of rejection, which is narrow enough to read as a distinct comb
// tooth while still passing enough signal to be musical. Much narrower and the
// output becomes a set of sine tones with the source barely audible in them.
static constexpr int32_t kCombQ = 2000;

// Highest centre frequency the SVF stays well-behaved at. The Chamberlin form
// needs f = 2*sin(pi*fc/SR) < 1, i.e. fc < SR/6 = 8 kHz; 7500 keeps a margin.
//
// Bands above this are CLAMPED rather than skipped. At N=12 the top layers sit
// at 14 and 28 kHz with real window gain (up to 0.43), so simply letting them
// run would make the filter blow up. Clamped, they pile up at the top of the
// range and - measured - fall naturally quiet, because a 28 kHz component
// driving a 7.5 kHz band produces almost nothing.
static constexpr int32_t kCombMaxHz = 7500;

class CombBank {
 public:
  void Init() {
    memset(lp_, 0, sizeof(lp_));
    memset(bp_, 0, sizeof(bp_));
  }

  // One band. `f` is the Q20 tuning coefficient for this band's centre.
  // Returns the bandpass output.
  //
  // The FILTER is shared between channels - only the window gain differs, the
  // same economy as the oscillator bank. Running two sets of filters would
  // double the cost for a difference the ear cannot hear, since the stereo
  // image comes from the window weighting rather than from the filtering.
  // `f` is Q20, not Q15 - see TuneFromInc for why.
  int32_t __not_in_flash_func(Process)(int i, int32_t x, int32_t f) {
    lp_[i] += MulQ20(f, bp_[i]);
    const int32_t hp = x - lp_[i] - MulQ15(kCombQ, bp_[i]);
    bp_[i] += MulQ20(f, hp);
    return bp_[i];
  }

  // Tuning coefficient for a centre frequency given as a Q32 phase increment
  // (the same representation the oscillator bank uses, so a band and its
  // matching oscillator are guaranteed to agree).
  //
  //     f = 2*sin(pi*fc/SR)
  //
  // and since inc = fc/SR * 2^32, the argument is just pi*inc/2^32 - which is
  // the first half of the existing sine table, no new table and no divide.
  // Q20 multiply, same hi/lo split as MulQ15. The coefficient needs the extra
  // precision (see TuneFromInc); the signal does not, so only the shift
  // differs and the cost is identical.
  static inline int32_t __not_in_flash_func(MulQ20)(int32_t w, int32_t x) {
    const int32_t hi = x >> 15;
    const int32_t lo = x - (hi << 15);
    return ((w * hi) >> 5) + ((w * lo) >> 20);
  }

  static inline int32_t __not_in_flash_func(TuneFromInc)(uint32_t inc) {
    // Clamp to kCombMaxHz: 7500/48000 * 2^32.
    if (inc > 671088640u) inc = 671088640u;

    // Returned in Q20, and computed two different ways depending on how low
    // the band is. Both parts are needed.
    //
    // The sine TABLE is 512 points across the half-cycle, so at a small angle
    // the interpolation runs between 0 and 201 - a 13.75 Hz band resolves to a
    // handful of LSBs no matter what Q the result is in, giving 29 cents of
    // detuning. The bands would drift off the oscillators they are meant to
    // sit on, and the two sources would beat instead of reinforcing.
    //
    // Below 750 Hz, sin(x) == x to well under a cent, so the coefficient is
    // computed directly from the increment at full precision:
    //
    //     f = 2*sin(pi*inc/2^32) ~= 2*pi*inc/2^32,  in Q20 = (inc>>11)*pi
    //
    // Above 750 Hz the linear form diverges (5 cents at 2 kHz, 67 at 7 kHz)
    // and the table is accurate, so the table takes over. Worst error across
    // the whole range is then 3.0 cents at 13.75 Hz - inaudible, and windowed
    // to silence there anyway - and under 0.74 cents everywhere above 27.5 Hz.
    int32_t f;
    if (inc < (1u << 26)) {
      f = (int32_t)(((inc >> 11) * 205887u) >> 16);   // 205887/65536 = pi
    } else {
      const uint32_t idx = (inc >> 23) & 0x1FFu;
      const int32_t frac = (int32_t)((inc >> 8) & 0x7FFF);
      const int32_t s0 = sin_lut[idx];
      const int32_t s1 = sin_lut[(idx + 1) & kSinLutMask];
      const int32_t sn = s0 + MulQ15(frac, s1 - s0);
      f = (sn << 1) * 32;                             // Q15 -> Q20
    }
    const int32_t kMax = 2 << 20;
    return f > kMax ? kMax : (f < 1 ? 1 : f);
  }

 private:
  int32_t lp_[kMaxLayers];
  int32_t bp_[kMaxLayers];
};

}  // namespace shepard

#endif  // SHEPARD_COMB_H_
