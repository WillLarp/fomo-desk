# FOMO desk

A five-agent paper trading desk for coins that graduate from pump.fun onto PumpSwap. It never places an order and never holds a key. Every signal ends with a FOMO button; you tap Buy or Sell yourself.

Python 3.13 in `.venv` (the original brief asked for 3.12; 3.13 is what this machine has). One long-lived asyncio process runs Scanner, Auditor, Analyst, Executor and Risk, plus a Telegram bot.

## Plan B hosting

The phone app is a static page on GitHub Pages. The process writes JSON locally every 15 seconds and publishes that tree at most every 6-10 minutes (Pages branch builds are soft-limited to about 10 per hour, and the CDN can cache for about 10 minutes). Telegram pushes are live and are the only notifier. There is no web push and no public API.

The app is opened once as `{Pages URL}#d={DATA_PATH}`. The fragment is stored in the browser and then stripped. `DATA_PATH` is 32+ random characters, kept outside the repo (see the secrets file on the machine that runs the desk). It is not on the `main` branch. The deploy branch of this public repo does contain it, so treat that branch as unlisted, not private.

`/d/` and unknown paths return 404 (no index file there).

## Run

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# TYPESAFE_API_KEY and TG_TOKEN come from the environment.
# DATA_PATH lives in a chmod 600 file outside this repo.
./run.sh          # start (idempotent, uses setsid nohup)
./run.sh health   # heartbeats
./run.sh restart
./run.sh stop
```

It starts in PAPER mode with a $200 paper stack. At 8% that is a $16 ticket, which is bumped to the $47.50 minimum; the desk warns about that once at night start.

## Telegram

Owner chat is locked. Commands: `/status`, `/why <mint>`, `/pause`, `/resume`, `/set <key> <value>`, `/stack [amount]`, `/paper`, `/live LIVE`, `/invite`, `/report`.

Settings, pause and mode changes go through Telegram. The Account tab is read-only.

## Morning report

No routines are created here. To print and write the night's data:

```bash
.venv/bin/python report.py
```

That prints the night report and writes `data/night_report.json` (stats + text) for a routine to pick up.

## Tests

```bash
.venv/bin/pytest -q
```
