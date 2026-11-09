"""RugCheck report and insiders graph. Fetch each once per audit and reuse."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fields import dig
from sources.http import client

log = logging.getLogger("rugcheck")

REPORT = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
GRAPH = "https://api.rugcheck.xyz/v1/tokens/{mint}/insiders/graph"


async def report(mint: str) -> tuple[dict | None, str | None]:
    """-> (report dict, error reason)."""
    try:
        r = await client().get(REPORT.format(mint=mint), timeout=20.0)
    except httpx.HTTPError as e:
        log.warning("report error %s: %s", mint[:8], type(e).__name__)
        return None, type(e).__name__
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        data = r.json()
    except Exception:
        return None, "bad json"
    return data if isinstance(data, dict) else None, None


async def insiders_graph(mint: str) -> tuple[Any, str | None]:
    """-> (raw body, error). Body may be [] or an object."""
    try:
        r = await client().get(GRAPH.format(mint=mint), timeout=20.0)
    except httpx.HTTPError as e:
        log.warning("graph error %s: %s", mint[:8], type(e).__name__)
        return None, type(e).__name__
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        data = r.json()
    except Exception:
        return None, "bad json"
    return data, None


def lp_locked_pct(report_obj: dict | None, pair_address: str | None) -> float | None:
    """Layer 5: markets[i].lp.lpLockedPct where markets[i].pubkey == pairAddress."""
    if not report_obj or not pair_address:
        return None
    for m in report_obj.get("markets") or []:
        if not isinstance(m, dict):
            continue
        if m.get("pubkey") == pair_address:
            v = dig(m, "lp.lpLockedPct")
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None
    return None


def pool_exclude_accounts(report_obj: dict | None, pair_address: str | None) -> set[str]:
    """Token accounts / owners belonging to the pool, to drop from top-10."""
    out: set[str] = set()
    if pair_address:
        out.add(pair_address)
    if not report_obj:
        return out
    for m in report_obj.get("markets") or []:
        if not isinstance(m, dict):
            continue
        if pair_address and m.get("pubkey") != pair_address:
            continue
        for k in (
            "pubkey",
            "liquidityAAccount",
            "liquidityBAccount",
            "mintLPAccount",
            "liquidityA",
            "liquidityB",
        ):
            v = m.get(k)
            if isinstance(v, str) and v:
                out.add(v)
            elif isinstance(v, dict):
                for kk in ("address", "pubkey", "owner"):
                    if v.get(kk):
                        out.add(v[kk])
    return out


def bundled_from_graph(graph: Any, holder_wallets: set[str] | None = None) -> tuple[int, str | None]:
    """Return (biggest group size, error). Empty graph ([] or no edges) is 0, no error.

    A failed call is handled by the caller. An object with no edges key, or
    edges=[], counts as no bundles (owner decision).
    """
    if graph is None:
        return 0, "no data: bundles"
    if graph == [] or graph == {}:
        return 0, None
    if not isinstance(graph, dict):
        # unexpected shape that is not a list-of-nothing
        if isinstance(graph, list):
            return 0, None
        return 0, "no data: bundles"
    if "edges" not in graph:
        return 0, None
    edges = graph.get("edges") or []
    nodes = graph.get("nodes") or []
    # Keep only wallets that hold this mint when we can tell.
    hold = holder_wallets
    node_ids = set()
    for n in nodes:
        if isinstance(n, dict):
            nid = n.get("id") or n.get("address") or n.get("wallet")
            if not nid:
                continue
            if hold is not None:
                flag = n.get("holder") or n.get("holds") or n.get("isHolder")
                if flag is False:
                    continue
                if hold and nid not in hold and n.get("address") not in hold:
                    # if we have a holder set, require membership
                    addr = n.get("address") or nid
                    if addr not in hold and nid not in hold:
                        continue
            node_ids.add(nid)
        elif isinstance(n, str):
            node_ids.add(n)
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for e in edges:
        a = b = None
        if isinstance(e, dict):
            a = e.get("from") or e.get("source") or e.get("a") or e.get("src")
            b = e.get("to") or e.get("target") or e.get("b") or e.get("dst")
            if isinstance(a, dict):
                a = a.get("id") or a.get("address")
            if isinstance(b, dict):
                b = b.get("id") or b.get("address")
        elif isinstance(e, (list, tuple)) and len(e) >= 2:
            a, b = e[0], e[1]
        if not a or not b:
            continue
        if hold and a not in hold and a not in node_ids:
            continue
        if hold and b not in hold and b not in node_ids:
            continue
        union(str(a), str(b))
        node_ids.add(str(a))
        node_ids.add(str(b))
    if not parent:
        return 0, None
    sizes: dict[str, int] = {}
    for n in list(parent.keys()) + list(node_ids):
        sizes[find(n)] = sizes.get(find(n), 0) + 1
    biggest = max(sizes.values()) if sizes else 0
    return biggest, None
