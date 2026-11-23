"""Build vectors.txt for TB.v from the supplied FP32/FP16 operand files.

Each output row contains precision, eight 32-bit input ports, and the expected
32-bit result. Expected outputs model Q2.50 magnitude alignment, truncation,
signed Q5.50 accumulation, and nearest-even output rounding. The exact fused
reference remains available separately for numerical comparisons.
"""

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def unpack(bits, eb, fb):
    """Decode raw bits using eb exponent bits and fb fraction bits.

    Return (kind, coefficient, exponent). For finite values, the exact number
    is coefficient * 2**exponent. For NaN/infinity, coefficient holds the sign
    and exponent is unused. Signed-zero information remains in the raw bits.
    """
    sign = -1 if bits >> (eb + fb) else 1
    exp = (bits >> fb) & ((1 << eb) - 1)
    frac = bits & ((1 << fb) - 1)

    if exp == (1 << eb) - 1:
        return ('nan' if frac else 'inf', sign, 0)
    # Subnormals have no hidden leading one and use the minimum exponent.
    bias = (1 << (eb - 1)) - 1
    return ('finite', sign * (frac if exp == 0 else (1 << fb) | frac),
            (1 if exp == 0 else exp) - bias - fb)


def round_shift_right(n, shift):
    """Scale nonnegative n by 2**(-shift), rounding to nearest, ties to even."""
    if shift <= 0:
        return n << -shift

    # The remainder distinguishes below-half, above-half, and exact tie cases.
    q, rem = divmod(n, 1 << shift)
    half = 1 << (shift - 1)
    return q + (rem > half or (rem == half and (q & 1)))


def pack(n, exponent, eb, fb, negative_zero=False):
    """Round exact n * 2**exponent into a finite-format bit pattern.

    Use nearest-even rounding, gradual underflow, and infinity on overflow.
    negative_zero preserves an explicitly requested negative sign when n is zero.
    """
    sign = int(n < 0 or (n == 0 and negative_zero)) << (eb + fb)
    n = abs(n)
    if not n:
        return sign

    bias = (1 << (eb - 1)) - 1
    emin, emax = 1 - bias, bias
    leading = n.bit_length() - 1 + exponent
    # Clamp the rounding quantum at the subnormal spacing near zero.
    quantum = max(leading, emin) - fb
    sig = round_shift_right(n, quantum - exponent)

    # Rounding can carry out of the significand and increase the exponent.
    if sig >= 1 << (fb + 1):
        sig >>= 1
        quantum += 1
    unbiased = quantum + fb

    if unbiased > emax:
        return sign | (((1 << eb) - 1) << fb)
    if sig < 1 << fb:
        return sign | sig
    return sign | ((unbiased + bias) << fb) | (sig - (1 << fb))


def compute_dot_product(values, half, aligned_fraction_bits=None):
    """Return the fused DP4 result bits for x1..x4 followed by y1..y4.

    half selects FP16; otherwise use FP32. Inputs and return values are raw
    format-width bits, before FP16 placement in the upper half of a port.
    Invalid operations return a canonical quiet NaN; no exception flags are modeled.
    aligned_fraction_bits selects finite-width magnitude alignment when supplied;
    its default preserves the original exact fused reference.
    """
    eb, fb = (5, 10) if half else (8, 23)
    nan = (((1 << eb) - 1) << fb) | (1 << (fb - 1))
    inf = ((1 << eb) - 1) << fb
    terms, infinities, zero_signs = [], set(), []

    # Resolve special products and retain every finite product exactly.
    for a, b in zip(values[:4], values[4:]):
        ka, na, ea = unpack(a, eb, fb)
        kb, nb, exb = unpack(b, eb, fb)
        if 'nan' in (ka, kb):
            return nan
        if 'inf' in (ka, kb):
            if (ka == 'finite' and na == 0) or (kb == 'finite' and nb == 0):
                return nan
            # Infinity signs come from raw operand sign bits.
            infinities.add(((a ^ b) >> (eb + fb)) & 1)
        else:
            terms.append((na * nb, ea + exb))
            zero_signs.append(((a ^ b) >> (eb + fb)) & 1)

    # Opposite infinities cannot produce a defined finite sum.
    if len(infinities) > 1:
        return nan
    if infinities:
        return inf | (next(iter(infinities)) << (eb + fb))

    # Exact and architectural references share decoding and final format rounding.
    if aligned_fraction_bits is None:
        base = min(e for _, e in terms)
        total = sum(n << (e - base) for n, e in terms)
    else:
        # Zero products cannot choose the alignment reference. Truncate positive
        # magnitudes before applying signs, matching logical shifts in the RTL.
        nonzero = [(n, e) for n, e in terms if n]
        maximum = max((e + 2 * fb for _, e in nonzero), default=0)
        base = maximum - aligned_fraction_bits
        total = 0
        for n, exponent in terms:
            shift = exponent - base
            magnitude = abs(n) << shift if shift >= 0 else abs(n) >> -shift
            total += -magnitude if n < 0 else magnitude
    # Exact cancellation gives +0; four negative-zero products preserve -0.
    all_negative_zero = all(n == 0 for n, _ in terms) and all(zero_signs)
    return pack(total, base, eb, fb, all_negative_zero)


def compute_architectural_dot_product(values, half):
    """Return the schematic's shared Q5.50 result, rounded once per format."""
    return compute_dot_product(values, half, aligned_fraction_bits=50)


def create_edge_cases(half):
    """Return grouped operands targeting arithmetic and alignment boundaries."""
    eb, fb = (5, 10) if half else (8, 23)
    bias = (1 << (eb - 1)) - 1
    one = bias << fb
    neg = 1 << (eb + fb)
    inf = ((1 << eb) - 1) << fb
    maxfinite = inf - 1
    smallest_normal = 1 << fb
    # Lists are always x1..x4,y1..y4.
    return [
        # Zero, signed zero, ordinary addition, cancellation, and overflow.
        [0]*8,
        [neg]*4 + [one]*4,
        [one]*8,
        [one, one | neg, one, one | neg] + [one]*4,
        [maxfinite]*4 + [one]*4,

        # Smallest subnormal and interaction with the smallest normal.
        [1, 0, 0, 0] + [one]*4,
        [smallest_normal, 1, 0, 0] + [one]*4,

        # Infinity propagation, opposite infinities, zero * infinity, and NaN.
        [inf, 0, 0, 0] + [one]*4,
        [inf, inf | neg, 0, 0] + [one]*4,
        [inf, 0, 0, 0] + [0]*4,
        [inf | 1, 0, 0, 0] + [one]*4,

        # Half-ULP ties with even/odd retained bits, cancellation, and underflow.
        [one, (bias-fb-1) << fb, 0, 0] + [one]*4,
        [one+1, (bias-fb-1) << fb, 0, 0] + [one]*4,
        [maxfinite, maxfinite | neg, 1, 0] + [one]*4,
        [1, 0, 0, 0] + [(bias-1) << fb]*4,

        # Large finite products must cancel before output overflow is decided.
        [maxfinite, maxfinite | neg, 1, 0] + [maxfinite, maxfinite, one, one],
        [maxfinite, maxfinite | neg, one, 0] + [maxfinite, maxfinite, one, one],

        # Negative underflow, a subnormal rounding carry, and large alignment gaps.
        [neg | 1, 0, 0, 0] + [(bias-1) << fb]*4,
        [smallest_normal - 1, (bias-fb-1) << fb, 0, 0] + [one, smallest_normal, 0, 0],
        [one, (bias-51) << fb if bias > 51 else 1, 0, 0] + [one]*4,

        # Infinity signs, NaN priority over overflow, and mixed signed zeros.
        [inf | neg, 0, 0, 0] + [one]*4,
        [inf, inf, 0, 0] + [one, one | neg, one, one],
        [maxfinite, inf | 1, 0, 0] + [maxfinite, one, one, one],
        [neg, 0, neg, 0] + [one]*4,
    ]


def read_inputs(path, half):
    """Read eight hex operands per row as x1 x2 x3 x4 y1 y2 y3 y4.

    Reject empty files, wrong column counts, and values outside the selected
    format width.
    """
    rows = []
    width = 16 if half else 32

    for number, line in enumerate(path.read_text().splitlines(), 1):
        fields = line.split()
        if len(fields) != 8:
            raise ValueError(f'{path}:{number}: expected eight operands')
        row = [int(s, 16) for s in fields]
        if any(n < 0 or n >= 1 << width for n in row):
            raise ValueError(f'{path}:{number}: operand out of range')
        rows.append(row)

    if not rows:
        raise ValueError(f'{path}: empty input')
    return rows


def main():
    """Parse the precision schedule and write the balanced TB input file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schedule', choices=['alternating', 'grouped'], default='alternating')
    args = parser.parse_args()

    # Preserve supplied operands and append the same number of cases per mode.
    rows = {h: read_inputs(ROOT/'testdata'/('FP16.txt' if h else 'FP32.txt'), h)
            + create_edge_cases(h) for h in (False, True)}
    if len(rows[False]) != len(rows[True]):
        raise ValueError('Equal FP32/FP16 counts required for 50% workload')

    # Alternating exercises mode changes; grouped gives sustained mode blocks.
    if args.schedule == 'alternating':
        sequence = [(h, rows[h][i]) for i in range(len(rows[False])) for h in (False, True)]
    else:
        sequence = [(h, row) for h in (False, True) for row in rows[h]]

    # ===================================================================================
    #                          START vectors.txt write
    # ===================================================================================
    out = ROOT/'testdata/vectors.txt'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for half, values in sequence:
        # Poison ignored FP16 lower bits to check input slicing in the DUT.
        packed = [(n << 16) | (0xa500 + i) if half else n
                  for i, n in enumerate(values)]
        expected = compute_architectural_dot_product(values, half) << (16 if half else 0)
        lines.append(f'{int(half)} ' + ' '.join(f'{n:08x}' for n in packed + [expected]))

    out.write_text('\n'.join(lines)+'\n', encoding='ascii')
    print(f'{len(lines)} cases; {len(rows[True])} FP16; schedule={args.schedule}')
    # ===================================================================================
    #                          END vectors.txt write
    # ===================================================================================


if __name__ == '__main__':
    main()
