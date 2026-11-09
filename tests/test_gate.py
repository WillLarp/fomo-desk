"""Acceptance test 3: Analyst gate is code-only."""

from agents.analyst import apply_gate, rules_answers
import config


def setup_module():
    config._cache.clear()


def test_gate_thresholds():
    ok = {"alive_6h": 0.70, "organic": 0.65, "flush": 0.0, "chase": 0.30}
    score, passed, failed = apply_gate(ok)
    # score = 100 * (0.70+0.65+0+0.70)/4 = 51.25 -> 51, below 70
    assert passed is False
    assert any("score" in f for f in failed)

    good = {"alive_6h": 0.9, "organic": 0.9, "flush": 1.0, "chase": 0.1}
    score, passed, failed = apply_gate(good)
    assert score == 92
    assert passed is True
    assert failed == []


def test_gate_each_rule():
    base = {"alive_6h": 0.9, "organic": 0.9, "flush": 1.0, "chase": 0.1}
    s, p, f = apply_gate({**base, "alive_6h": 0.69})
    assert p is False and any("alive_6h" in x for x in f)
    s, p, f = apply_gate({**base, "organic": 0.64})
    assert p is False and any("organic" in x for x in f)
    s, p, f = apply_gate({**base, "chase": 0.31})
    assert p is False and any("chase" in x for x in f)


def test_rules_mode_formulas():
    pair = {
        "volume": {"h1": 12500, "m5": 100},
        "txns": {"h1": {"buys": 75, "sells": 20}, "m5": {"buys": 10, "sells": 5}},
        "priceChange": {"m5": -6, "h1": 40},
    }
    ans = rules_answers(pair, {"bundled": 0})
    assert abs(ans["alive_6h"] - 0.5) < 1e-6
    assert abs(ans["organic"] - 0.5) < 1e-6
    assert ans["flush"] == 1.0
    assert abs(ans["chase"] - 0.5) < 1e-6
    ans2 = rules_answers(pair, {"bundled": 3})
    assert abs(ans2["organic"] - 0.25) < 1e-6
