from math import comb
from decimal import Decimal, getcontext

# Plenty of precision for the final division
getcontext().prec = 80


def scientific(x, sig_digits=8):
    """Return x in the form C × 10^e."""
    x = Decimal(x)

    if x == 0:
        return "0"

    exponent = x.adjusted()
    coefficient = x.scaleb(-exponent)

    return f"{coefficient:.{sig_digits - 1}f} × 10^{exponent}"


def compute_F(n, k):
    numerator = comb(n, 2*k) * comb(2*k, k)

    denominator = sum(
        comb(k, i)
        * comb(n - 2*k, k - i)
        * comb(n - 2*k + i, k)
        for i in range(k + 1)
    )

    value = Decimal(numerator) / Decimal(denominator)

    return numerator, denominator, value


pairs = [
    (128,45),
    (128,44),
]

for n, k in pairs:
    numerator, denominator, value = compute_F(n, k)

    print(f"F({n}, {k})")
    print(f"  numerator   = {numerator}")
    print(f"              ≈ {scientific(numerator)}")

    print(f"  denominator = {denominator}")
    print(f"              ≈ {scientific(denominator)}")

    print(f"  F({n},{k})   ≈ {scientific(value)}")
    print()