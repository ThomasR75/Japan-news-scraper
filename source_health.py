#!/usr/bin/env python3
"""
source_health.py — Telegram when a news source has been returning nothing
for several runs in a row, and again when it comes back.

    python3 source_health.py --log logs/pipeline_2026-10-30.log   # what run.sh does
    python3 source_health.py --log ... --status                   # parse + decide, send nothing
    python3 source_health.py --status                             # today's log

Why this exists: on 2026-10-30 four of the nineteen non-Nikkei sources were
found to have logged "Found 0 article links" every night — Mainichi since
2026-08-22 (a CloudFront UA block), Yomiuri Local since the first retained
log in June (wrong URL shape) — and the Nikkei half had been empty for four
mornings on expired cookies. Every one of those runs exited 0. The pipeline
only ever alerted on a crash; a source that answers politely with nothing is
invisible to it.

It reads the pipeline's own log rather than asking the scrapers for a report,
so the two scraper repos stay untouched and a scraper that dies halfway still
gets its missing sources counted as dark. Only the LAST run block in the log
is parsed — the file is appended to across same-day reruns.

Per source the signal is "how many candidate articles did the listing give
us", not how many were saved: a source can save 0 legitimately (everything
already archived, every piece under the length floor) but a listing that
yields 0 links/items/articles is broken.

Alerts fire on transitions, in the unit_failure_alert.py sense: once when a
source reaches THRESHOLD consecutive dark runs, a reminder every REMIND_EVERY
runs while it stays dark, once on recovery. Sources that have come back
disappear from the state so the file never grows.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_PATH = HERE / "data" / "source_health_state.json"
JST = dt.timezone(dt.timedelta(hours=9))

THRESHOLD = 3       # dark runs before the first alert
REMIND_EVERY = 7    # then every this many runs while still dark

RUN_HEADER = re.compile(r"^==== Japan News pipeline ")
SECTION = re.compile(r"^--- (?P<name>[A-Z][A-Za-z]+ [A-Za-z]+) ---$")
FOUND = re.compile(r"^\s+Found (?P<n>\d+) article links")
RSS = re.compile(r"^\s+RSS feed: (?P<n>\d+) items")
RSS_FAIL = re.compile(r"^\s+RSS (returned|parse error)")
NIKKEI_FETCH = re.compile(r"^\[(?P<name>Nikkei [A-Za-z]+)\] fetching ")
NIKKEI_DONE = re.compile(r"^\[(?P<name>Nikkei [A-Za-z]+)\] saved=(?P<s>\d+) skipped=(?P<k>\d+)")
NIKKEI_AUTH = re.compile(r"^\s+AUTH CHALLENGE PAGE")


# --- pure logic --------------------------------------------------------------

def last_run_block(text: str) -> str:
    """Everything after the final run header (the log is appended to all day)."""
    lines = text.splitlines()
    starts = [i for i, l in enumerate(lines) if RUN_HEADER.match(l)]
    return "\n".join(lines[starts[-1]:] if starts else lines)


def parse_log(text: str) -> dict[str, int]:
    """{source: candidate-article count} for the last run in `text`.

    Non-Nikkei sources are the '--- Name ---' blocks of scraper_non_nikkei.py;
    requests/Yomiuri print 'Found N article links', NHK prints 'RSS feed: N
    items'. Nikkei sections are '[Nikkei X] saved=N skipped=M' (saved+skipped,
    since both are articles the listing produced) or an AUTH CHALLENGE line.
    A section header with no count line after it reads as 0 — that is a
    scraper that crashed or an RSS fetch that failed.
    """
    counts: dict[str, int] = {}
    current: str | None = None
    for line in last_run_block(text).splitlines():
        m = SECTION.match(line)
        if m:
            current = str(m["name"])
            counts.setdefault(current, 0)
            continue
        m = NIKKEI_FETCH.match(line)
        if m:
            current = str(m["name"])
            counts.setdefault(current, 0)
            continue
        if current is None:
            continue
        if (m := FOUND.match(line)) or (m := RSS.match(line)):
            counts[current] = int(m["n"])
        elif (m := NIKKEI_DONE.match(line)) and m["name"] == current:
            counts[current] = int(m["s"]) + int(m["k"])
        elif RSS_FAIL.match(line) or NIKKEI_AUTH.match(line):
            counts[current] = 0
    return counts


def update(state: dict, counts: dict[str, int], threshold: int = THRESHOLD,
           remind_every: int = REMIND_EVERY) -> tuple[dict, list[tuple[str, int]], list[tuple[str, int]]]:
    """Fold one run into the streak state.

    Returns (new_state, alerts, recoveries). `alerts` is [(source, dark_runs)]
    for sources that should be announced THIS run; `recoveries` is
    [(source, dark_runs_it_had)] for sources back after an announced outage.
    A source known from earlier runs but absent from this run's log counts as
    dark — the scraper never got to it.
    """
    new = {}
    alerts, recoveries = [], []
    known = set(state) | set(counts)
    for src in sorted(known):
        n = counts.get(src, 0)
        runs = state.get(src, {}).get("dark_runs", 0)
        if n > 0:
            if runs >= threshold:
                recoveries.append((src, runs))
            continue                       # healthy sources carry no state
        runs += 1
        new[src] = {"dark_runs": runs}
        if runs == threshold or (runs > threshold and (runs - threshold) % remind_every == 0):
            alerts.append((src, runs))
    return new, alerts, recoveries


def render(alerts: list[tuple[str, int]], recoveries: list[tuple[str, int]]) -> str:
    lines = []
    if alerts:
        lines += ["📰 <b>News sources returning nothing</b>", ""]
        for src, runs in alerts:
            lines.append(f"• <b>{src}</b> — 0 candidate articles for {runs} runs")
        lines += ["", "Listing fetched fine but yielded no links/items: a UA block, "
                      "a moved URL, expired cookies, or a layout change. "
                      "<code>grep -B2 'Found 0' logs/pipeline_*.log</code> shows since when."]
    if recoveries:
        if lines:
            lines.append("")
        lines += ["✅ <b>Back</b>: " + ", ".join(f"{src} (after {runs} dark runs)"
                                                 for src, runs in recoveries)]
    return "\n".join(lines)


# --- I/O ----------------------------------------------------------------------

def load_state(path: Path = STATE_PATH) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(tmp, path)


def send_telegram(text: str) -> bool:
    from publish_ranked import CHAT_ID, send_message, token  # same bot, same chat
    try:
        return send_message(text, token(), CHAT_ID)
    except Exception as exc:
        print(f"telegram: send failed ({type(exc).__name__}: {exc})", file=sys.stderr)
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", help="pipeline log to read (default: today's, JST)")
    ap.add_argument("--status", action="store_true", help="parse and decide, change nothing")
    args = ap.parse_args()

    log = Path(args.log) if args.log else HERE / "logs" / f"pipeline_{dt.datetime.now(JST):%Y-%m-%d}.log"
    if not log.exists():
        print(f"no log at {log}", file=sys.stderr)
        return 1
    counts = parse_log(log.read_text(errors="replace"))
    if not counts:
        print("no source blocks found in the last run — nothing to judge", file=sys.stderr)
        return 1

    state = load_state()
    new_state, alerts, recoveries = update(state, counts)
    dark = sorted(new_state)
    print(f"{len(counts)} sources parsed · {sum(1 for v in counts.values() if v == 0)} at zero this run · "
          f"dark streaks: " + (", ".join(f"{s}×{new_state[s]['dark_runs']}" for s in dark) or "none"))
    for src, runs in alerts:
        print(f"  ALERT {src} — {runs} runs")
    for src, runs in recoveries:
        print(f"  RECOVERED {src} — was dark {runs} runs")
    if args.status:
        return 0

    save_state(new_state)
    if alerts or recoveries:
        ok = send_telegram(render(alerts, recoveries))
        print(f"telegram: {'sent' if ok else 'FAILED'}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
