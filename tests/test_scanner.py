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



def test_pool_age_ignores_stale_dex_pairCreatedAt():
    """Night 1 C5xph2yL: DexScreener pairCreatedAt was 2026-09-13, but the
    PumpSwap migrate landed 2026-11-10 04:04:00 CET (blockTime == migrated_at).
    Scanner used the Dex date (age 82494m) and passed; auditor used migrated_at
    (age 0m) and rejected 'pool too young'. Both must use migrate time.
    """
    from util import pool_age_min, pool_created_ts, age_min, iso_to_ts

    migrated_at = 1794279840.763118  # 2026-11-10 04:04:00 CET
    pair_created_at_ms = 1789330218000  # DexScreener: 2026-09-13 22:10:18 CEST
    scan_now = 1794279864.0  # 2026-11-10 03:04:24Z, 24s after migrate

    # old scanner path, kept as evidence
    assert age_min(iso_to_ts(pair_created_at_ms), scan_now) == 82494

    created = pool_created_ts(migrated_at, pair_created_at_ms)
    assert created == migrated_at
    assert pool_age_min(migrated_at, pair_created_at_ms, now=scan_now) == 0
    assert pool_age_min(migrated_at, pair_created_at_ms, now=scan_now) < 10
    # after the 10-minute wait, both agents agree the pool is old enough
    later = migrated_at + 10 * 60
    assert pool_age_min(migrated_at, pair_created_at_ms, now=later) == 10

    # if we only have a fresh Dex date, still usable as fallback
    fresh_ms = 1794279840000
    assert pool_age_min(None, fresh_ms, now=scan_now) == 0
    assert abs(pool_created_ts(None, fresh_ms) - 1794279840.0) < 0.001
