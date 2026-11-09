"""Acceptance test 2: scanner kill order and copycat keying."""

from util import copycat_key, is_copycat


def test_copycat_normalize_and_match():
    assert copycat_key("Mr Beast 2") == "mrbeast"
    assert copycat_key("MRBEAST") == "mrbeast"
    runners = [{"name": "MR BEAST", "symbol": "BEAST"}]
    assert is_copycat("mr beast", "foo", runners) == "MR BEAST"
    assert is_copycat("ZZZCOIN", "ZZZ", runners) is None


from agents.scanner import first_kill


def test_kill_order_liquidity_wins_first():
    # All four would fail; liquidity must be the reported reason.
    reason = first_kill(
        liq=31_200, top10=38, h1=112, name="Mr Beast 2", symbol="BEAST",
        runners=[{"name": "MR BEAST", "symbol": "BEAST"}],
        min_liq=40_000, max_top=25, max_h1=80,
    )
    assert reason == "liq $31.2K"


def test_kill_order_top10_then_h1_then_copycat():
    assert first_kill(
        liq=80_000, top10=38, h1=112, name="Mr Beast 2", symbol="BEAST",
        runners=[{"name": "MR BEAST", "symbol": "BEAST"}],
    ) == "top 10 hold 38%"
    assert first_kill(
        liq=80_000, top10=16, h1=112, name="Mr Beast 2", symbol="BEAST",
        runners=[{"name": "MR BEAST", "symbol": "BEAST"}],
    ) == "+112% in 1h"
    assert first_kill(
        liq=80_000, top10=16, h1=10, name="Mr Beast 2", symbol="BEAST",
        runners=[{"name": "MR BEAST", "symbol": "BEAST"}],
    ) == "copycat of MR BEAST"
    assert first_kill(
        liq=80_000, top10=16, h1=10, name="Unique", symbol="UNIQ",
        runners=[{"name": "MR BEAST", "symbol": "BEAST"}],
    ) is None
