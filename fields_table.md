# STEP 2 field map

Mint used: `3WUEgV2yoLK3gpRM287qrn94kUTDFq3TH9eDNGPnpump` (PumpPortal migration, 2026-11-09 Europe/Budapest).

Raw files: `logs/raw/pumpportal_migration.json`, `dexscreener_pair.json`, `rugcheck_report.json`, `rugcheck_insiders.json`, `rpc_getaccountinfo.json`.

`null` = key not present in those raw files (not guessed).

| Need | Source file | JSON key path | Notes |
|------|-------------|---------------|-------|
| migration mint | pumpportal_migration.json | `mint` | |
| migration pool | pumpportal_migration.json | `pool` | Value was `"pump-amm"` (label), **not** a pool pubkey |
| migration signature | pumpportal_migration.json | `signature` | |
| migration creator | pumpportal_migration.json | **null** | Not in message. Use RugCheck `creator` |
| dexId | dexscreener_pair.json | `dexId` | On pumpswap element of the array |
| pairAddress | dexscreener_pair.json | `pairAddress` | `DjDobLuqBUQWM7LXpXqRrCsRbifpyhBKFNa8LsdFu82p` |
| priceUsd | dexscreener_pair.json | `priceUsd` | |
| liquidity.usd | dexscreener_pair.json | `liquidity.usd` | |
| marketCap | dexscreener_pair.json | `marketCap` | |
| volume m5 | dexscreener_pair.json | `volume.m5` | |
| volume h1 | dexscreener_pair.json | `volume.h1` | |
| volume h24 | dexscreener_pair.json | `volume.h24` | |
| txns m5 buys | dexscreener_pair.json | `txns.m5.buys` | |
| txns m5 sells | dexscreener_pair.json | `txns.m5.sells` | |
| txns h1 buys | dexscreener_pair.json | `txns.h1.buys` | |
| txns h1 sells | dexscreener_pair.json | `txns.h1.sells` | |
| priceChange m5 | dexscreener_pair.json | `priceChange.m5` | |
| priceChange h1 | dexscreener_pair.json | `priceChange.h1` | |
| priceChange h24 | dexscreener_pair.json | `priceChange.h24` | |
| pairCreatedAt | dexscreener_pair.json | `pairCreatedAt` | |
| RugCheck creator | rugcheck_report.json | `creator` | |
| markets ↔ pool | rugcheck_report.json | `markets[i].pubkey` | Equals DexScreener `pairAddress`; migration `pool` is not a pubkey on this sample |
| LP locked % | rugcheck_report.json | `markets[i].lp.lpLockedPct` | Observed `100` |
| LP burned % | rugcheck_report.json | **null** | No burned/burn percent key in this report |
| top holders | rugcheck_report.json | `topHolders` | Array of holder objects |
| insiders nodes | rugcheck_insiders.json | **null** | Body was `[]` |
| insiders edges | rugcheck_insiders.json | **null** | Body was `[]` |
| insiders holder flag | rugcheck_insiders.json | **null** | Body was `[]` |
| mintAuthority | rpc_getaccountinfo.json | `result.value.data.parsed.info.mintAuthority` | |
| freezeAuthority | rpc_getaccountinfo.json | `result.value.data.parsed.info.freezeAuthority` | |
| supply | rpc_getaccountinfo.json | `result.value.data.parsed.info.supply` | |
| decimals | rpc_getaccountinfo.json | `result.value.data.parsed.info.decimals` | |

Machine-readable copy: `fields.py` (`FIELDS` dict).
