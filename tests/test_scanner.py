"""Acceptance test 2: scanner kill order and copycat keying."""

from util import copycat_key, is_copycat


def test_copycat_normalize_and_match():
    assert copycat_key("Mr Beast 2") == "mrbeast"
    assert copycat_key("MRBEAST") == "mrbeast"
    runners = [{"name": "MR BEAST", "symbol": "BEAST"}]
    assert is_copycat("mr beast", "foo", runners) == "MR BEAST"
    assert is_copycat("ZZZCOIN", "ZZZ", runners) is None
