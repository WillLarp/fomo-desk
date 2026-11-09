"""FOMO ordinary-Solana spot fee schedule, used for the PAPER book only.

Published bands (FOMO help center, compiled 2026-09-18):

- [0, 5) USDC: flat 0.10
- [5, 47.50): 2%
- [47.50, 190): flat 0.95
- [190, inf): 0.50%

A referral discount applies to the top band only: 0.50% * (1 - discount).
At 0.10 that is 0.45%. Smaller bands stay as published. This desk defaults
the discount to 0 because the owner did not sign up through the referral link.
FOMO shows the real fee before a person confirms a swap. This module only
prices the local paper book. Gas is treated as 0 (FOMO covers it on Solana).
"""

from __future__ import annotations

_BANDS = (
    (0.0, 5.0, "flat", 0.10),
    (5.0, 47.50, "pct", 0.02),
    (47.50, 190.0, "flat", 0.95),
    (190.0, None, "pct", 0.005),
)

_TOP_BAND_RATE = 0.005


def fomo_solana_fee(notional_usd: float, ref_discount: float = 0.0) -> float:
    """FOMO fee in USDC for one Solana spot leg."""
    notional = float(notional_usd or 0)
    if notional <= 0:
        return 0.0
    discount = min(0.9, max(0.0, float(ref_discount)))
    for lower, upper, kind, value in _BANDS:
        if notional < lower:
            continue
        if upper is not None and notional >= upper:
            continue
        if kind == "flat":
            return float(value)
        rate = float(value)
        if value == _TOP_BAND_RATE:
            rate = rate * (1.0 - discount)
        return notional * rate
    return 0.0


def side_fee(ticket_usd: float, ref_discount: float = 0.0) -> float:
    return round(fomo_solana_fee(ticket_usd, ref_discount), 4)


def round_trip_fee(entry_notional: float, exit_notional: float, ref_discount: float = 0.0) -> float:
    return round(
        fomo_solana_fee(entry_notional, ref_discount)
        + fomo_solana_fee(exit_notional, ref_discount),
        4,
    )


# alias used by Risk
round_trip = round_trip_fee
