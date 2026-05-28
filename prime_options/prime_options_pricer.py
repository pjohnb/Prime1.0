"""
PRIME v1.0 Options Pricer (IDX-OPT-001).

Black-Scholes pricer + Greeks (delta, gamma, theta, vega).
Used as fallback when Schwab doesn't return Greeks.
"""

import math
from typing import Dict


def _cdf(x: float) -> float:
    """Standard normal CDF approximation (Abramowitz & Stegun)."""
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x / 2.0)
    return 0.5 * (1.0 + sign * y)


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes(
    spot: float,
    strike: float,
    dte: int,
    iv: float,
    rate: float = 0.05,
    option_type: str = "CALL",
) -> Dict[str, float]:
    """Black-Scholes pricing with Greeks.

    Returns {price, delta, gamma, theta, vega}.
    """
    if dte <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return {"price": 0.0, "delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}

    t = dte / 365.0
    sqrt_t = math.sqrt(t)

    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t

    discount = math.exp(-rate * t)

    if option_type.upper() == "CALL":
        price = spot * _cdf(d1) - strike * discount * _cdf(d2)
        delta = _cdf(d1)
    else:
        price = strike * discount * _cdf(-d2) - spot * _cdf(-d1)
        delta = _cdf(d1) - 1.0

    gamma = _pdf(d1) / (spot * iv * sqrt_t)
    theta = (-(spot * _pdf(d1) * iv) / (2.0 * sqrt_t)
             - rate * strike * discount * _cdf(d2 if option_type.upper() == "CALL" else -d2)) / 365.0
    vega = spot * _pdf(d1) * sqrt_t / 100.0

    return {
        "price": round(price, 4),
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta, 4),
        "vega": round(vega, 4),
    }
