"""Check the Python golden-result generator, not the Verilog DUT.

Known answers exercise special values and rounding boundaries. A separate
Fraction-based decoder checks whether randomized finite results are nearest
to the exact dot product. Run with python -B test_reference.py to avoid caches.
These checks provide evidence for the reference model, not exhaustive proof.
"""

import random
import unittest
from fractions import Fraction

from generate_vectors import compute_architectural_dot_product, compute_dot_product, pack


def bits_to_fraction(bits, eb, fb):
    """Decode finite raw bits into an exact Fraction, independently of unpack().

    eb and fb specify exponent and fraction widths. NaN and infinity must not
    be passed to this helper; its callers restrict inputs to finite values.
    """
    sign = -1 if bits >> (eb+fb) else 1
    exp = (bits >> fb) & ((1 << eb)-1)
    mant = bits & ((1 << fb)-1)

    # Restore the implicit one only for normal values.
    if exp:
        mant += 1 << fb

    power = max(exp, 1) - ((1 << (eb-1))-1) - fb
    return sign * Fraction(mant) * Fraction(2)**power


class ReferenceTests(unittest.TestCase):
    """Exercise exact and finite-width arithmetic models for FP16 and FP32."""

    def test_known(self):
        """Check hand-derived results for zero, cancellation, ties, and extremes."""
        for half, eb, fb in [(True,5,10),(False,8,23)]:
            one = ((1 << (eb-1))-1) << fb
            inf = ((1 << eb)-1) << fb
            neg = 1 << (eb+fb)

            # Four ones sum to four; opposite terms cancel to positive zero.
            self.assertEqual(compute_dot_product([one]*8,half),one+(2 << fb))
            self.assertEqual(compute_dot_product([one,one|neg,0,0]+[one]*4,half),0)

            # Preserve all-negative zero and the smallest subnormal.
            self.assertEqual(compute_dot_product([neg]*4+[one]*4,half),neg)
            self.assertEqual(compute_dot_product([1,0,0,0]+[one]*4,half),1)

            # Infinity is valid unless multiplied by zero, which produces NaN.
            self.assertEqual(compute_dot_product([inf,0,0,0]+[one]*4,half),inf)
            self.assertEqual(compute_dot_product([inf,0,0,0]+[0]*4,half),inf|(1 << (fb-1)))

            # Exact halfway values round to an even least-significant bit.
            self.assertEqual(pack(1,-fb-((1 << (eb-1))-2)-1,eb,fb),0)
            self.assertEqual(pack((1 << (fb+1))+1,-fb-1,eb,fb),one)
            self.assertEqual(pack((1 << (fb+1))+3,-fb-1,eb,fb),one+2)

            # Four maximum finite values overflow to infinity.
            self.assertEqual(compute_dot_product([inf-1]*4+[one]*4,half),inf)

    def test_fraction_nearest_neighbors(self):
        """Check 500 finite cases per format against exact neighbor distances.

        The result must be at least as close to the exact magnitude as either
        adjacent representable value. Equal distances require an even low bit.
        This magnitude check does not independently verify the result sign.
        """
        # A fixed seed makes any failure reproducible.
        rng = random.Random(310)
        for half,eb,fb in [(True,5,10),(False,8,23)]:
            # Finite bounded inputs avoid overflow in this independent check.
            for _ in range(500):
                row = [(rng.randrange(1 << (eb+fb-2)) |
                        (rng.randrange(2) << (eb+fb))) for _ in range(8)]

                # Fraction keeps products and accumulation exact without floats.
                exact = sum((bits_to_fraction(row[i],eb,fb)*bits_to_fraction(row[i+4],eb,fb)
                             for i in range(4)),Fraction(0))
                result = compute_dot_product(row,half)

                # Strip the sign to compare adjacent positive encodings.
                magnitude = result & ((1 << (eb+fb))-1)
                distance = abs(abs(exact)-abs(bits_to_fraction(result,eb,fb)))

                for neighbor in [magnitude-1,magnitude+1]:
                    # Skip negative encodings and the infinity boundary.
                    if 0 <= neighbor < (((1 << eb)-1) << fb):
                        other = abs(abs(exact)-bits_to_fraction(neighbor,eb,fb))
                        self.assertLessEqual(distance,other)
                        if distance == other:
                            self.assertEqual(magnitude & 1,0)

    def test_architectural_cancellation(self):
        """Distinguish intentional alignment loss from an exact fused result."""
        for half, eb, fb in [(True, 5, 10), (False, 8, 23)]:
            one = ((1 << (eb - 1)) - 1) << fb
            maximum = (((1 << eb) - 1) << fb) - 1
            sign = 1 << (eb + fb)
            row = [maximum, maximum | sign, 1, 0] + [maximum, maximum, one, one]

            self.assertEqual(compute_dot_product(row, half), 1)
            self.assertEqual(compute_architectural_dot_product(row, half), 0)

    def test_architectural_fraction_grid(self):
        """Independently quantize finite products on the shared alignment grid."""
        rng = random.Random(550)
        for half, eb, fb in [(True, 5, 10), (False, 8, 23)]:
            bias = (1 << (eb - 1)) - 1
            for _ in range(500):
                # Bounded finite operands retain coverage of both signs and zeros.
                row = [rng.randrange(((1 << eb) - 1) << fb) |
                       (rng.randrange(2) << (eb + fb)) for _ in range(8)]
                exponents = [max((v >> fb) & ((1 << eb) - 1), 1) - bias
                             for v in row]
                products = [bits_to_fraction(row[i], eb, fb) *
                            bits_to_fraction(row[i + 4], eb, fb) for i in range(4)]
                maximum = max((exponents[i] + exponents[i + 4]
                               for i in range(4) if products[i]), default=0)
                quantum = Fraction(2) ** (maximum - 50)
                quantized = sum(int(value / quantum) for value in products)

                self.assertEqual(compute_architectural_dot_product(row, half),
                                 pack(quantized, maximum - 50, eb, fb))


if __name__ == '__main__':
    unittest.main()
