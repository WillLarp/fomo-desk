
"""One long-lived asyncio process: 5 agents + Telegram + Plan B publisher."""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import sys
from pathlib import Path

import config
import db
import paths
import publisher
from agents import analyst, auditor, executor, risk, scanner
from util import local_now

log = logging.getLogger("desk")


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for name in ("TYPESAFE_API_KEY", "TG_TOKEN", "DATA_PATH"):
            val = os.environ.get(name)
            if val and val in msg:
                record.msg = msg.replace(val, f"[{name}]")
                record.args = ()
        try:
            dp = paths.data_path()
            if dp and dp in record.getMessage():
                record.msg = record.getMessage().replace(dp, "[DATA_PATH]")
                record.args = ()
        except Exception:
            pass
        return True


def setup_logging() -> None:
    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
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


async def publisher_loop(stop: asyncio.Event) -> None:
    last_pages = 0.0
    first = True
    while not stop.is_set():
        try:
            publisher.write_local()
        except Exception:
            log.exception("local publish failed")
        now = __import__("time").time()
        interval = float(config.get("desk.pages_push_s"))
        if first or (now - last_pages) >= interval:
            first = False
            try:
                if publisher.publish_pages():
                    last_pages = now
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


async def amain() -> None:
    setup_logging()
    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
    Path(paths.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    db.init()
    # paper start stack as specified
    if "desk.paper_start_stack" not in {r["key"] for r in db.q("SELECT key FROM settings")}:
        config.set_value("desk.paper_start_stack", 200)
        db.save_setting("desk.paper_start_stack", 200, None)
    if "desk.mode" not in {r["key"] for r in db.q("SELECT key FROM settings")}:
        config.set_value("desk.mode", "PAPER")
        db.save_setting("desk.mode", "PAPER", None)
    if "analyst.mode" not in {r["key"] for r in db.q("SELECT key FROM settings")}:
        config.set_value("analyst.mode", "jev")
        db.save_setting("analyst.mode", "jev", None)
    if "desk.fomo_handle" not in {r["key"] for r in db.q("SELECT key FROM settings")}:
        config.set_value("desk.fomo_handle", "WillLarp")
        db.save_setting("desk.fomo_handle", "WillLarp", None)
    db.start_night(config.get("desk.mode"))
    log.info("desk starting mode=%s python=%s", config.get("desk.mode"), sys.version.split()[0])
    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(scanner.run(stop), name="scanner"),
        asyncio.create_task(auditor.run(stop), name="auditor"),
        asyncio.create_task(analyst.run(stop), name="analyst"),
        asyncio.create_task(executor.run(stop), name="executor"),
        asyncio.create_task(risk.run(stop), name="risk"),
        asyncio.create_task(__import__("telegram_bot").run(stop), name="telegram"),
        asyncio.create_task(publisher_loop(stop), name="publisher"),
        asyncio.create_task(report_loop(stop), name="report"),
    ]
    try:
        await night_start_notice()
    except Exception:
        log.exception("night start notice failed")
    # write first snapshot immediately
    try:
        publisher.write_local()
    except Exception:
        log.exception("first snapshot failed")

    def _stop(*_):
        stop.set()

    import signal as _signal

    loop = asyncio.get_running_loop()
    for sig in (_signal.SIGINT, _signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass
    await stop.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    from sources.http import aclose

    await aclose()
    log.info("desk stopped")


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
