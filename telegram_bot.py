
"""Telegram long-poll + pushes. Owner is locked to owner_chat_id. Never log the token."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

import config
import db
import links
import paths
from sources.http import client

log = logging.getLogger("telegram")

API = "https://api.telegram.org/bot{token}/{method}"
_offset = 0


def _token() -> str | None:
    return os.environ.get("TG_TOKEN") or None


def _chat() -> int | None:
    return paths.owner_chat_id()


async def _api(method: str, payload: dict | None = None, retries: int = 3) -> dict | None:
    token = _token()
    if not token:
        return None
    url = API.format(token=token, method=method)
    last = None
    for i in range(retries):
        try:
            r = await client().post(url, json=payload or {}, timeout=40.0)
        except httpx.HTTPError as e:
            last = e
            await asyncio.sleep(1 + i)
            continue
        if r.status_code == 200:
            try:
                return r.json()
            except Exception:
                return None
        log.warning("telegram %s HTTP %s", method, r.status_code)
        await asyncio.sleep(1 + i)
    if last:
        log.warning("telegram %s failed: %s", method, type(last).__name__)
    return None


def _keyboard(buttons: list[tuple[str, str]] | None) -> dict | None:
    if not buttons:
        return None
    row = []
    for label, target in buttons:
        if target.startswith("cb:"):
            row.append({"text": label, "callback_data": target})
        else:
            row.append({"text": label, "url": target})
    return {"inline_keyboard": [row]}


async def push(text: str, buttons: list[tuple[str, str]] | None = None, mint: str | None = None, kind: str = "") -> bool:
    chat = _chat()
    if not chat or not _token():
        log.warning("push skipped: no chat or token")
        return False
    payload: dict[str, Any] = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
    kb = _keyboard(buttons)
    if kb:
        payload["reply_markup"] = kb
    data = await _api("sendMessage", payload, retries=3)
    ok = bool(data and data.get("ok"))
    if not ok:
        log.warning("push failed kind=%s", kind)
    return ok


async def alert_owner(text: str) -> bool:
    return await push(text, kind="alert")


async def _reply(chat_id: int, text: str) -> None:
    if chat_id != _chat():
        return
    await _api("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})


async def _handle_callback(cq: dict) -> None:
    chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id")
    if chat_id != _chat():
        return
    data = cq.get("data") or ""
    cid = cq.get("id")
    await _api("answerCallbackQuery", {"callback_query_id": cid})
    if data.startswith("cb:bought:"):
        sid = int(data.split(":")[-1])
        from actions import bought

        msg = await bought(sid)
        await _reply(chat_id, msg)
    elif data.startswith("cb:skip:"):
        sid = int(data.split(":")[-1])
        from actions import skip

        msg = await skip(sid)
        await _reply(chat_id, msg)
    elif data.startswith("cb:sold:"):
        pid = int(data.split(":")[-1])
        from actions import sold

        msg = await sold(pid)
        await _reply(chat_id, msg)


async def _handle_text(chat_id: int, text: str) -> None:
    if chat_id != _chat():
        return
    raw = (text or "").strip()
    if not raw:
        return
    if raw.startswith("/"):
        parts = raw.split(maxsplit=2)
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        rest = parts[2] if len(parts) > 2 else ""
    else:
        # "stack 1240"
        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        rest = ""
        if cmd != "stack":
            return

    if cmd in ("/start",):
        await _reply(chat_id, "Desk is online. Try /status")
        return
    if cmd in ("/status",):
        await _reply(chat_id, _status_text())
        return
    if cmd in ("/pause",):
        if db.open_positions() and cmd == "/pause":
            # pause new entries; risk stays on
            pass
        key, old, new = config.set_value("desk.paused", True)
        db.save_setting(key, new, old)
        await _reply(chat_id, "Paused new entries. Open positions stay guarded.")
        return
    if cmd in ("/resume",):
        key, old, new = config.set_value("desk.paused", False)
        db.save_setting(key, new, old)
        await _reply(chat_id, "Resumed.")
        return
    if cmd in ("/paper",):
        key, old, new = config.set_value("desk.mode", "PAPER")
        db.save_setting(key, new, old)
        await _reply(chat_id, "Mode is PAPER.")
        return
    if cmd in ("/live",):
        if arg.upper() != "LIVE" and arg.upper() != "CONFIRM":
            await _reply(chat_id, 'Send /live LIVE to switch. The desk still never places an order.')
            return
        key, old, new = config.set_value("desk.mode", "LIVE")
        db.save_setting(key, new, old)
        await _reply(chat_id, "Mode is LIVE. I will send Buy / Sell buttons; you tap them on FOMO.")
        return
    if cmd in ("/stack", "stack"):
        if arg:
            try:
                key, old, new = config.set_value("desk.live_stack", arg)
                db.save_setting(key, new, old)
                await _reply(chat_id, f"Live stack set to ${float(new):,.2f}.")
            except Exception as e:
                await _reply(chat_id, str(e))
            return
        await _reply(chat_id, _stack_text())
        return
    if cmd in ("/set",):
        if not arg:
            await _reply(chat_id, "Usage: /set <key> <value>")
            return
        value = rest or ""
        try:
            key, old, new = config.set_value(arg, value)
            db.save_setting(key, new, old)
            await _reply(chat_id, f"{key}: {old} → {new}")
        except Exception as e:
            await _reply(chat_id, str(e))
        return
    if cmd in ("/why",):
        await _reply(chat_id, _why(arg))
        return
    if cmd in ("/invite",):
        handle = str(config.get("desk.fomo_handle") or "")
        await _reply(
            chat_id,
            f"Signup: {links.SIGNUP_URL}\nProfile: {links.profile_url(handle) if handle else links.SIGNUP_URL}",
        )
        return
    if cmd in ("/report",):
        from report import night_report_text

        await _reply(chat_id, night_report_text())
        return
    if cmd in ("/help",):
        await _reply(
            chat_id,
            "Commands: /status /why <mint> /pause /resume /set <key> <value> "
            "/stack [amount] /paper /live LIVE /invite /report",
        )
        return


def _status_text() -> str:
    mode = config.get("desk.mode")
    paused = "paused" if config.get("desk.paused") else "running"
    bal = config.stack()
    opens = db.open_positions()
    pnl, pct = db.today_pnl()
    lines = [
        f"{mode} · {paused}",
        f"Stack ${bal:,.2f} · today {pct:+.1f}% ({pnl:+.2f})",
        f"Open positions: {len(opens)}",
    ]
    for name in ("scanner", "auditor", "analyst", "executor", "risk"):
        row = db.q1("SELECT * FROM heartbeats WHERE name=?", (name,))
        if row:
            lines.append(f"{name}: {row['status']} · {row['detail'] or ''} · {row['count']}")
        else:
            lines.append(f"{name}: —")
    return "\n".join(lines)


def _stack_text() -> str:
    lines = [f"Mode {config.get('desk.mode')} · stack ${config.stack():,.2f} · free ${config.free_stack():,.2f}"]
    for p in db.open_positions():
        lines.append(
            f"{p.get('name') or p['mint'][:6]} · ${p.get('size_usd'):.2f} @ {p.get('entry_price')} · {p.get('status')}"
        )
    if len(lines) == 1:
        lines.append("No open positions.")
    return "\n".join(lines)


def _why(mint: str) -> str:
    if not mint:
        return "Usage: /why <mint>"
    mint = mint.strip()
    scan = db.latest_scan(mint)
    audit = db.latest_audit(mint)
    verdict = db.latest_verdict(mint)
    lines = [mint]
    if scan:
        lines.append(f"Scanner: {scan.get('verdict')} {scan.get('reason') or ''}")
    if audit:
        lines.append(f"Auditor: {'pass' if audit.get('passed') else 'red'} {audit.get('reason') or ''}")
        for layer in audit.get("layers") or []:
            flag = "ok" if layer.get("pass") else ("—" if layer.get("pass") is None else "red")
            lines.append(f"  {layer.get('n')} {layer.get('name')}: {flag} {layer.get('value')}")
    if verdict:
        lines.append(
            f"Analyst: score {verdict.get('score')} {'pass' if verdict.get('passed') else 'reject'} "
            f"{verdict.get('failed') or verdict.get('reason') or ''}"
        )
    if len(lines) == 1:
        return f"No decision on {mint} tonight."
    return "\n".join(lines)


async def run(stop: asyncio.Event) -> None:
    global _offset
    if not _token():
        log.warning("TG_TOKEN missing; telegram off")
        return
    if not _chat():
        log.warning("owner chat missing; telegram off")
        return
    log.info("telegram polling as owner chat locked")
    while not stop.is_set():
        data = await _api(
            "getUpdates",
            {"timeout": 20, "offset": _offset, "allowed_updates": ["message", "callback_query"]},
            retries=2,
        )
        if not data or not data.get("ok"):
            try:
                await asyncio.wait_for(stop.wait(), timeout=3)
            except asyncio.TimeoutError:
                continue
            continue
        for upd in data.get("result") or []:
            _offset = int(upd.get("update_id", 0)) + 1
            try:
                if "callback_query" in upd:
                    await _handle_callback(upd["callback_query"])
                elif "message" in upd:
                    msg = upd["message"]
                    chat_id = (msg.get("chat") or {}).get("id")
                    text = msg.get("text") or ""
                    await _handle_text(chat_id, text)
            except Exception:
                log.exception("update failed")
