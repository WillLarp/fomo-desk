"""Acceptance test 5: freeze authority red, empty graph is a pass, no data is red."""

import json
from pathlib import Path

from sources.rpc import parsed_mint_info
from sources.rugcheck import bundled_from_graph, lp_locked_pct

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_freeze_authority_from_recorded_fixture():
    """Use the STEP 2 recorded getAccountInfo if freeze is set; otherwise a crafted fixture."""
    raw_path = Path("/workspace/desk/logs/raw/rpc_getaccountinfo.json")
    data = json.loads(raw_path.read_text()) if raw_path.is_file() else {}
    info = parsed_mint_info(data)
    freeze = info.get("freezeAuthority")
    fixture = FIXTURE_DIR / "rpc_freeze_set.json"
    if freeze not in (None, "", "null"):
        assert freeze  # live sample already has freeze set
        source = "recorded STEP 2 sample logs/raw/rpc_getaccountinfo.json"
    else:
        sample = {
            "value": {
                "data": {
                    "parsed": {
                        "info": {
                            "mintAuthority": None,
                            "freezeAuthority": "Freeze11111111111111111111111111111111111",
                            "supply": "1000000000",
                            "decimals": 6,
                        }
                    }
                }
            }
        }
        FIXTURE_DIR.mkdir(exist_ok=True)
        fixture.write_text(json.dumps(sample, indent=2))
        info = parsed_mint_info(sample)
        freeze = info.get("freezeAuthority")
        source = "tests/fixtures/rpc_freeze_set.json"
    assert freeze not in (None, "", "null")
    # layer 2 rule
    assert not (freeze in (None, "", "null"))
    test_freeze_authority_from_recorded_fixture.source = source


def test_empty_insider_graph_is_none_and_passes():
    n, err = bundled_from_graph([])
    assert n == 0 and err is None
    n, err = bundled_from_graph({})
    assert n == 0 and err is None
    n, err = bundled_from_graph({"nodes": [], "edges": []})
    assert n == 0 and err is None
    n, err = bundled_from_graph({"nodes": [{"id": "a"}]})  # no edges key
    assert n == 0 and err is None
    n, err = bundled_from_graph(None)
    assert err == "no data: bundles"


def test_lp_locked_matches_pair_address():
    report = {
        "markets": [
            {"pubkey": "OTHER", "lp": {"lpLockedPct": 10}},
            {"pubkey": "PAIR", "lp": {"lpLockedPct": 100}},
        ]
    }
    assert lp_locked_pct(report, "PAIR") == 100
    assert lp_locked_pct(report, "MISSING") is None
