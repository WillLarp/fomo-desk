
"""One long-lived asyncio process: 5 agents + Telegram + Plan B publisher."""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config
import db
import paths
import publisher
import lease
import watchdog
from agents import analyst, auditor, executor, risk, scanner
from util import local_now

log = logging.getLogger("desk")


def _scrub_text(s: str) -> str:
    for name in ("TYPESAFE_API_KEY", "TG_TOKEN", "DATA_PATH", "VAPID_PRIVATE_KEY", "APP_TOKEN"):
        val = os.environ.get(name)
        if val and val in s:
            s = s.replace(val, f"[{name}]")
    try:
        dp = paths.data_path()
        if dp and dp in s:
            s = s.replace(dp, "[DATA_PATH]")
    except Exception:
        pass
    return s


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = _scrub_text(record.getMessage())
            record.msg = msg
            record.args = ()
        except Exception:
            pass
        return True


class _RedactFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # exc_text is built during format(), so scrub the whole line including tracebacks
        return _scrub_text(super().format(record))


def setup_logging() -> None:
    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = _RedactFormatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # clear
    for h in list(root.handlers):
        root.removeHandler(h)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    stream.addFilter(_RedactFilter())
    root.addHandler(stream)
    # per-agent files
    for name in ("scanner", "auditor", "analyst", "executor", "risk", "desk", "telegram", "publisher"):
        fh = logging.handlers.RotatingFileHandler(
            paths.LOG_DIR / f"{name}.log", maxBytes=2_000_000, backupCount=3
        )
        fh.setFormatter(fmt)
        fh.addFilter(_RedactFilter())
        logging.getLogger(name).addHandler(fh)
        logging.getLogger(name).propagate = True


# One worker so write_local and git push never overlap (they share the same tree).
_publish_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="desk-pub")


async def _to_thread(fn, timeout: float):
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(loop.run_in_executor(_publish_pool, fn), timeout=timeout)


async def publisher_loop(stop: asyncio.Event) -> None:
    last_attempt = float(db.kv_get("publisher.last_pages_push") or 0)
    first = last_attempt <= 0
    while not stop.is_set():
        if not lease.is_leader():
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            continue
        try:
            await _to_thread(publisher.write_local, timeout=30)
        except Exception:
            log.exception("local publish failed")
        now = __import__("time").time()
        interval = float(config.get("desk.pages_push_s"))
        if first or (now - last_attempt) >= interval:
            first = False
            last_attempt = now
            db.kv_set("publisher.last_pages_push", last_attempt)
            try:
                await _to_thread(publisher.publish_pages, timeout=publisher.GIT_PUSH_TIMEOUT_S + 30)
            except Exception:
                log.exception("pages publish failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=float(config.get("desk.publish_s")))
        except asyncio.TimeoutError:
            continue


async def report_loop(stop: asyncio.Event) -> None:
    """At desk.report_time local, write the night report (does not create a Grok routine)."""
    from report import night_report_text, write_night_json
    from telegram_bot import push

    last_date = None
    while not stop.is_set():
        now = local_now()
        want = str(config.get("desk.report_time") or "08:00")
        try:
            hh, mm = [int(x) for x in want.split(":", 1)]
        except ValueError:
            hh, mm = 8, 0
        if now.hour == hh and now.minute == mm and last_date != now.date():
            last_date = now.date()
            if lease.is_leader():
                text = night_report_text()
                write_night_json()
                await push(text, kind="report")
                # roll the night so "tonight" resets
                db.end_night()
                db.start_night(config.get("desk.mode"))
        try:
            await asyncio.wait_for(stop.wait(), timeout=20)
        except asyncio.TimeoutError:
            continue


async def night_start_notice() -> None:
    from telegram_bot import push

    check = config.ticket_check()
    mode = config.get("desk.mode")
    msg = f"{mode} night started. Stack ${config.stack():,.2f}."
    if check.get("message"):
        msg += " " + check["message"]
    await push(msg, kind="night")


async def supervised(name: str, factory, stop: asyncio.Event) -> None:
    """Restart an agent loop if it crashes; stop only when the desk is stopping."""
    while not stop.is_set():
        try:
            await factory(stop)
            if not stop.is_set():
                log.warning("%s stopped unexpectedly; restarting", name)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s crashed; restarting", name)
        if stop.is_set():
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except asyncio.TimeoutError:
            continue


def _ensure_defaults() -> None:
    existing = {r["key"] for r in db.q("SELECT key FROM settings")}
    defaults = {
        "desk.paper_start_stack": 200,
        "desk.mode": "PAPER",
        "analyst.mode": "jev",
        "desk.fomo_handle": "WillLarp",
    }
    for key, value in defaults.items():
        if key not in existing:
            config.set_value(key, value)
            db.save_setting(key, value, None)


async def amain() -> None:
    setup_logging()
    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
    Path(paths.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    db.init()
    _ensure_defaults()
    existed_night = db.current_night() is not None
    db.start_night(config.get("desk.mode"))
    log.info(
        "desk starting mode=%s python=%s night=%s instance=%s",
        config.get("desk.mode"),
        sys.version.split()[0],
        "resumed" if existed_night else "new",
        lease.instance_id(),
    )
    stop = asyncio.Event()
    lease.start()
    watchdog.start()
    tasks = [
        asyncio.create_task(supervised("scanner", scanner.run, stop), name="scanner"),
        asyncio.create_task(supervised("auditor", auditor.run, stop), name="auditor"),
        asyncio.create_task(supervised("analyst", analyst.run, stop), name="analyst"),
        asyncio.create_task(supervised("executor", executor.run, stop), name="executor"),
        asyncio.create_task(supervised("risk", risk.run, stop), name="risk"),
        asyncio.create_task(supervised("telegram", __import__("telegram_bot").run, stop), name="telegram"),
        asyncio.create_task(publisher_loop(stop), name="publisher"),
        asyncio.create_task(report_loop(stop), name="report"),
        asyncio.create_task(watchdog.ticker(stop), name="watchdog-tick"),
    ]
    if not existed_night and lease.is_leader():
        try:
            await night_start_notice()
        except Exception:
            log.exception("night start notice failed")
    else:
        log.info("skipped night-start push; resuming open night")
    # write first snapshot off the event loop; only the leader touches the tree
    if lease.is_leader():
        try:
            await _to_thread(publisher.write_local, timeout=30)
        except Exception:
            log.exception("first snapshot failed")

    def _stop(*_):
        stop.set()
        watchdog.stop()
        lease.stop()

    import signal as _signal

    loop = asyncio.get_running_loop()
    for sig in (_signal.SIGINT, _signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass
    await stop.wait()
    watchdog.stop()
    lease.stop()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    from sources.http import aclose

    await aclose()
    log.info("desk stopped")


def main() -> None:
    lease.parse_instance()
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
