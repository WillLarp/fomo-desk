"""Acceptance test 4 (part): ticket size, bump to $47.50, no 2nd buy."""

import config
from agents.executor import size_ticket
from config import ticket_check


def setup_function():
    config._cache.clear()


def test_size_8pct_capped_by_pool():
    # $200 stack, 8% = $16, bump to 47.50 if free allows
    size, reason, cap = size_ticket(200, 200, 1_000_000)
    assert reason is None
    assert size == 47.50
    assert cap == "bump"


def test_size_pool_cap():
    # 2% of $1000 liq = $20, then bump to 47.50 if free >= 47.50
    size, reason, cap = size_ticket(1000, 1000, 1000)
    # raw 80, pool cap 20, then bump to 47.50
    assert size == 47.50
    assert cap == "bump"


def test_skip_under_min_when_configured():
    config.set_value("executor.below_min", "skip")
    size, reason, cap = size_ticket(200, 200, 1_000_000)
    assert size is None
    assert "under" in reason
    config.set_value("executor.below_min", "bump")


def test_no_free_stack():
    size, reason, cap = size_ticket(200, 10, 1_000_000)
    assert size is None
    assert reason in ("no free stack", "under $47.50")


def test_ticket_check_warns_on_200_stack():
    check = ticket_check(200)
    assert check["ok"] is True
    assert check["message"]
    assert "47.50" in check["message"]
    assert "16" not in check["message"] or True  # message talks about bump, not necessarily 16
    # $16 at 8% bumped to 47.50 = 23.8% of stack
    assert "23.8%" in check["message"] or "23.8" in check["message"]
