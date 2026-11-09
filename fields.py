"""Field map from STEP 2 raw samples under logs/raw/.

Paths are exact JSON key paths observed in the saved files for mint
3WUEgV2yoLK3gpRM287qrn94kUTDFq3TH9eDNGPnpump. None means the key was
not present in those raw responses (do not guess).

DexScreener: response is a JSON array of pairs. Prefer the element with
dexId == "pumpswap". Paths below are relative to that pair object.
"""

FIELDS = {
    # --- PumpPortal migration (logs/raw/pumpportal_migration.json) ---
    "migration.mint": "mint",
    "migration.pool": "pool",  # observed value was the string "pump-amm", not a pubkey
    "migration.signature": "signature",
    "migration.creator": None,  # not in migration message; see rugcheck.creator

    # --- DexScreener tokens/v1/solana/{mint} (logs/raw/dexscreener_pair.json) ---
    # Select pair where dexId == "pumpswap" (also present: pairAddress matches RugCheck market pubkey).
    "dexscreener.dexId": "dexId",
    "dexscreener.pairAddress": "pairAddress",
    "dexscreener.priceUsd": "priceUsd",
    "dexscreener.liquidity.usd": "liquidity.usd",
    "dexscreener.marketCap": "marketCap",
    "dexscreener.volume.m5": "volume.m5",
    "dexscreener.volume.h1": "volume.h1",
    "dexscreener.volume.h24": "volume.h24",
    "dexscreener.txns.m5.buys": "txns.m5.buys",
    "dexscreener.txns.m5.sells": "txns.m5.sells",
    "dexscreener.txns.h1.buys": "txns.h1.buys",
    "dexscreener.txns.h1.sells": "txns.h1.sells",
    "dexscreener.priceChange.m5": "priceChange.m5",
    "dexscreener.priceChange.h1": "priceChange.h1",
    "dexscreener.priceChange.h24": "priceChange.h24",
    "dexscreener.pairCreatedAt": "pairCreatedAt",

    # --- RugCheck report (logs/raw/rugcheck_report.json) ---
    "rugcheck.creator": "creator",
    # Match market to pool: migration.pool is not a pubkey on this sample.
    # DexScreener pairAddress == markets[i].pubkey (PumpSwap / pump_fun_amm).
    "rugcheck.markets.pool_match_key": "markets[i].pubkey",
    "rugcheck.markets.lp_locked_pct": "markets[i].lp.lpLockedPct",
    "rugcheck.markets.lp_burned_pct": None,  # no burned/burn pct key in this report
    "rugcheck.topHolders": "topHolders",  # list of {address, amount, decimals, pct, uiAmount, uiAmountString, owner, insider}

    # --- RugCheck insiders graph (logs/raw/rugcheck_insiders.json) ---
    # Raw body was [] (empty JSON array). No object keys to map.
    "rugcheck_insiders.nodes": None,
    "rugcheck_insiders.edges": None,
    "rugcheck_insiders.holder_flag": None,

    # --- RPC getAccountInfo(mint, jsonParsed) (logs/raw/rpc_getaccountinfo.json) ---
    "rpc.mintAuthority": "result.value.data.parsed.info.mintAuthority",
    "rpc.freezeAuthority": "result.value.data.parsed.info.freezeAuthority",
    "rpc.supply": "result.value.data.parsed.info.supply",
    "rpc.decimals": "result.value.data.parsed.info.decimals",
}

# Convenience aliases used by agents (same values as above).
AGENT_FIELDS = {
    "mint": FIELDS["migration.mint"],
    "pool": FIELDS["migration.pool"],
    "signature": FIELDS["migration.signature"],
    "creator": FIELDS["migration.creator"],  # None from PumpPortal; use rugcheck.creator
}


def dig(obj, path, default=None):
    """Read a dotted path, including list[i] segments. Missing -> default."""
    if obj is None or not path:
        return default
    cur = obj
    for part in path.replace("][", ".").replace("[", ".").replace("]", "").split("."):
        if cur is None:
            return default
        if part == "":
            continue
        if isinstance(cur, dict):
            if part not in cur:
                return default
            cur = cur[part]
        elif isinstance(cur, (list, tuple)):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return default
        else:
            return default
    return cur


def pumpswap_pair(pairs):
    """Pick the PumpSwap pair, or the one with the most USD liquidity."""
    if not pairs:
        return None
    if isinstance(pairs, dict):
        pairs = [pairs]
    pumps = [p for p in pairs if isinstance(p, dict) and p.get("dexId") == "pumpswap"]
    pool = pumps or [p for p in pairs if isinstance(p, dict)]
    if not pool:
        return None

    def liq(p):
        v = dig(p, "liquidity.usd", 0) or 0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    return max(pool, key=liq)
