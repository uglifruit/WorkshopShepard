// hilbert.h - analytic-signal pair and the complex modulator bank.
//
// This is the live-audio half of the card: it drags whatever is patched into
// Audio In 1 through the same infinite-glide structure as the internal
// oscillators.
//
// WHAT A FREQUENCY SHIFTER IS, AND IS NOT
//
// A frequency shifter moves every partial by the same number of HERTZ. A pitch
// shifter multiplies every partial by the same RATIO. They are different
// operations and only the second preserves harmonic relationships:
//
//     input 200/400/600/800 Hz, shifted +100 Hz -> 300/500/700/900
//     ratios 1.500 / 1.250 / 1.167 / 1.125      -> inharmonic, metallic
//
// That inharmonicity is inherent and is NOT a defect to be fixed. It is the
// character of this mode, and it is why Y crossfades to it rather than
// replacing the internal voice - Risset's own tape pieces sound like this.
//
// THE STRUCTURE, AND WHY IT MUST BE THIS ONE
//
//   AudioIn1 -> ONE Hilbert pair -> (I, Q) -> N complex modulators -> window
//
// The analytic signal depends only on the INPUT, not on how far it is
// subsequently shifted. Running N Hilbert transforms would cost 12x for
// bit-identical results - it is waste, not a trade-off.
//
// More importantly it must be shared for CORRECTNESS: all N modulators have to
// see the same (I, Q) for the layers to stay phase-coherent. N separate
// transforms would each contribute their own small phase error, decorrelating
// the layers and smearing the illusion.
//
// Layer i shifts by shift_base * 2^i - the same shift-per-layer trick as the
// oscillator bank, so one increment computed at control rate serves all
// layers via a shift.

#ifndef SHEPARD_HILBERT_H_
#define SHEPARD_HILBERT_H_

#include <stdint.h>

#include "fixed.h"

namespace shepard {

// Two 4-section polyphase allpass branches. Each section is
//
//     y[n] = a^2 * (x[n] + y[n-2]) - x[n-2]
//
// which is the difference equation for H(z) = (a^2 - z^-2) / (1 - a^2 z^-2):
// unity gain at every frequency, with a frequency-dependent phase. The two
// branches are designed so their phases differ by 90 degrees across the band.
static constexpr int kHilbertSections = 4;

// Coefficients are a^2 in Q30. Defined in hilbert.cpp as const globals
// (read-only, so flash is correct).
//
// TWO TRAPS, both of which produce a card that sounds plausible but is wrong.
// Recorded here because neither is visible in the output without a phase
// measurement:
//
//   1. The published constants are `a`. What is STORED is a SQUARED. Using the
//      published values directly as a^2 gives 13.3 degrees of phase error
//      instead of 0.70.
//
//   2. The extra z^-1 goes on branch A - the one listed first. Putting it on
//      branch B instead makes the two branches identical, so there is no
//      quadrature at all and the "shifter" passes audio through unshifted.
//      Measured error in that case: 89.9 degrees.
//
// tools/hilbert_check.py asserts both.
extern const int32_t kHilbertA[];   // [kHilbertSections] branch A, a^2 Q30
extern const int32_t kHilbertB[];   // [kHilbertSections] branch B, a^2 Q30

// One second-order allpass section.
struct AllpassSection {
  int32_t x1, x2;    // input history
  int32_t y1, y2;    // output history
};

// The analytic-signal filter. Produces (I, Q) 90 degrees apart.
class Hilbert {
 public:
  void Init() {
    for (int i = 0; i < kHilbertSections; ++i) {
      a_[i].x1 = a_[i].x2 = a_[i].y1 = a_[i].y2 = 0;
      b_[i].x1 = b_[i].x2 = b_[i].y1 = b_[i].y2 = 0;
    }
    delay_ = 0;
  }

  // Feed one sample, get the analytic pair back.
  //
  // Input is expected pre-scaled into roughly +-2^20 so the Q30 multiplies
  // keep their precision without overflowing MulQ30's headroom.
  void __not_in_flash_func(Process)(int32_t x, int32_t* i_out, int32_t* q_out) {
    int32_t a = x;
    for (int k = 0; k < kHilbertSections; ++k) {
      a = Section(&a_[k], a, kHilbertA[k]);
    }

    int32_t b = x;
    for (int k = 0; k < kHilbertSections; ++k) {
      b = Section(&b_[k], b, kHilbertB[k]);
    }

    // The extra unit delay belongs on branch A. See the trap note above.
    *i_out = delay_;
    delay_ = a;
    *q_out = b;
  }

 private:
  static inline int32_t __not_in_flash_func(Section)(AllpassSection* s,
                                                     int32_t x, int32_t a2) {
    // y[n] = a^2 * (x[n] + y[n-2]) - x[n-2]
    const int32_t y = MulQ30(a2, x + s->y2) - s->x2;
    s->x2 = s->x1;
    s->x1 = x;
    s->y2 = s->y1;
    s->y1 = y;
    return y;
  }

  AllpassSection a_[kHilbertSections];
  AllpassSection b_[kHilbertSections];
  int32_t delay_;
};

}  // namespace shepard

#endif  // SHEPARD_HILBERT_H_
