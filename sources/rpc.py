"""Solana JSON-RPC helpers. Query Token and Token-2022. dataSize 165 only on classic Token."""

from __future__ import annotations

import logging
from typing import Any

import httpx

import config
from fields import dig
from sources.http import client

log = logging.getLogger("rpc")

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# Well-known pump.fun program (bonding-curve accounts are owned by it).
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


async def _call(method: str, params: list, timeout: float = 30.0) -> tuple[Any, str | None]:
    url = str(config.get("desk.rpc_url"))
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        r = await client().post(url, json=payload, timeout=timeout)
    except httpx.HTTPError as e:
        log.warning("rpc %s error: %s", method, type(e).__name__)
        return None, type(e).__name__
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        data = r.json()
    except Exception:
        return None, "bad json"
    if data.get("error"):
        msg = dig(data, "error.message") or str(data.get("error"))
        return None, str(msg)
    return data.get("result"), None


async def get_account_info(pubkey: str) -> tuple[dict | None, str | None]:
    result, err = await _call(
        "getAccountInfo",
        [pubkey, {"encoding": "jsonParsed", "commitment": "confirmed"}],
    )
    if err:
        return None, err
    return result, None


async def get_token_supply(mint: str) -> tuple[dict | None, str | None]:
    result, err = await _call("getTokenSupply", [mint, {"commitment": "confirmed"}])
    return result, err


async def get_token_largest_accounts(mint: str) -> tuple[list, str | None]:
    result, err = await _call("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])
    if err:
        return [], err
    value = (result or {}).get("value") if isinstance(result, dict) else result
    return value or [], None


async def get_token_accounts_by_owner(owner: str, mint: str) -> tuple[list, str | None]:
    accounts: list = []
    last_err = None
    for program in (TOKEN_PROGRAM, TOKEN_2022):
        result, err = await _call(
            "getTokenAccountsByOwner",
            [
                owner,
                {"mint": mint},
                {"encoding": "jsonParsed", "commitment": "confirmed"},
            ],
        )
        if err:
            last_err = err
            continue
        value = (result or {}).get("value") if isinstance(result, dict) else []
        accounts.extend(value or [])
    if not accounts and last_err:
        return [], last_err
    return accounts, None


def parsed_mint_info(account_result: dict | None) -> dict:
    info = dig(account_result, "value.data.parsed.info") or {}
    return {
        "mintAuthority": info.get("mintAuthority"),
        "freezeAuthority": info.get("freezeAuthority"),
        "supply": info.get("supply"),
        "decimals": info.get("decimals"),
    }


def ui_amount_sum(accounts: list) -> float:
    total = 0.0
    for a in accounts:
        ui = dig(a, "account.data.parsed.info.tokenAmount.uiAmount")
        if ui is None:
            ui = dig(a, "uiAmount")
        try:
            total += float(ui or 0)
        except (TypeError, ValueError):
            continue
    return total


async def get_program_accounts_for_mint(mint: str) -> tuple[list, str | None]:
    """Token (dataSize 165) + Token-2022 (no dataSize). jsonParsed, filter by mint."""
    accounts: list = []
    errors: list[str] = []
    # classic Token: dataSize 165, memcmp mint at 0
    filt = [
        {"dataSize": 165},
        {"memcmp": {"offset": 0, "bytes": mint}},
    ]
    result, err = await _call(
        "getProgramAccounts",
        [
            TOKEN_PROGRAM,
            {"encoding": "jsonParsed", "filters": filt, "commitment": "confirmed"},
        ],
        timeout=60.0,
    )
    if err:
        errors.append(f"token: {err}")
    else:
        accounts.extend(result or [])

    result, err = await _call(
        "getProgramAccounts",
        [
            TOKEN_2022,
            {
                "encoding": "jsonParsed",
                "filters": [{"memcmp": {"offset": 0, "bytes": mint}}],
                "commitment": "confirmed",
            },
        ],
        timeout=60.0,
    )
    if err:
        errors.append(f"token2022: {err}")
    else:
        accounts.extend(result or [])

    if errors and not accounts:
        return [], "; ".join(errors)
    return accounts, None
