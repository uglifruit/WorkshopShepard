// hilbert.cpp - the allpass coefficients.
//
// Const, read-only -> flash is correct for these (unlike sin_lut / pow2_lut in
// shepard.cpp, which are WRITTEN at init and must be in RAM).

#include "hilbert.h"

namespace shepard {

// a^2 in Q30, from the classic Niemitalo polyphase halfband design.
//
// The published values are `a`:
//
//     A: 0.6923878, 0.9360654, 0.9882295, 0.9987488
//     B: 0.4021921, 0.8561710, 0.9722910, 0.9952885
//
// and what the difference equation needs is a SQUARED. Storing the published
// numbers directly costs about 8x the phase error (0.46 -> 3.62 degrees at
// 1 kHz) - the filter still runs, still passes audio, and the shifter still
// "works", just with a badly leaking sideband. tools/hilbert_check.py asserts
// the squaring.
//
// MEASURED performance, from a 50-point log sweep rather than a handful of
// spot frequencies:
//
//     worst quadrature error   2.0 degrees at 33 Hz
//     -> sideband rejection   ~35 dB worst case
//     mid-band (100 Hz - 20 kHz)  0.2 - 0.7 degrees, 44 - 55 dB
//
// A sparse sample suggests 0.70 degrees / 44 dB; that misses the equiripple
// peaks and is optimistic. 35 dB is ample here - the unwanted sideband sits
// well below a signal that is then octave-stacked and windowed - but the
// number on record should be the one the filter delivers.
//
// Q30 quantisation costs NOTHING: integer and float agree to 0.001 degrees, so
// the ripple above is the filter's own, not the fixed point's.
const int32_t kHilbertA[kHilbertSections] = {
   514752760,   // 0.6923878^2
   940832379,   // 0.9360654^2
  1048613629,   // 0.9882295^2
  1071056573,   // 0.9987488^2
};

const int32_t kHilbertB[kHilbertSections] = {
   173686851,   // 0.4021921^2
   787083661,   // 0.8561710^2
  1015061606,   // 0.9722910^2
  1063647790,   // 0.9952885^2
};

}  // namespace shepard
