#!/usr/bin/env python3
"""
score_articles.py — rank the day's translated articles against six coverage
axes and the live thesis ledger, and store the scores in carry.db.

    python3 score_articles.py                 # the pipeline step
    python3 score_articles.py --dry-run       # score, print the top 20, write nothing
    python3 score_articles.py --limit 30      # first N articles only (for a look)

Pipeline position: after translate_minimax.py, before the digests, via
run_scraper (non-fatal) — if this fails the two existing digests still ship.

Exit 1 when ZERO articles were scored. A run that produces no ranking must
not be mistaken for a quiet day: that is exactly how the empty Nikkei
digest went unnoticed for weeks. Zero ABOVE threshold is fine and is the
publishers' business.
"""
import argparse, json, os, sqlite3, sys, time, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

from scoring_rubric import load_rubric, AXES, combine, sort_key, is_fresh
from thesis_match import load_theses, all_theses, theses_hash, candidates, clamp_strength
from generate_html import dedup_articles_by_japanese_shingles
from minimax_translate import get_endpoint_and_key, MODEL

HERE = Path(__file__).resolve().parent
RAW_DIR = HERE / "data" / "news_archive" / "raw"
DB_PATH = Path(os.path.expanduser("~/.openclaw/workspace/apps/carry-dash/server/carry.db"))
JST = timezone(timedelta(hours=9))
BATCH = 10
BODY_CHARS = 1200
RETRY_WAIT = 5

# Two fixed reference articles scored with EVERY batch and discarded. Found
# on the first golden run: identical articles re-scored minutes apart moved
# 7 of 15 out of band (a farm takeover went 3.0 -> 8.0) because the model's
# scale floated from call to call. A constant 10 and a constant 0 in each
# batch pin the frame the real articles are scored against.
CALIBRATION = [
    {"url": "calibration://rates-10", "source": "calibration",
     "title_en": "Bank of Japan raises policy rate 25bp to 1.25%, signals further hikes",
     "translated_text": "The Bank of Japan raised its short-term policy rate by 25 basis points to 1.25% "
                        "and said it will keep raising if inflation stays on track. JGB yields rose across "
                        "the curve and the yen strengthened."},
    {"url": "calibration://zero", "source": "calibration",
     "title_en": "Local festival draws record crowds under clear skies",
     "translated_text": "A regional summer festival drew its largest crowd in a decade. Organisers thanked "
                        "volunteers; police reported no incidents."},
]
CALIBRATION_URLS = {a["url"] for a in CALIBRATION}

# Concrete anchors per axis, so a "7" means the same thing in every call.
SCALE = [
    "SCALE (absolute; score each article on its own against these examples — do NOT spread",
    "scores across the batch or grade on a curve):",
    "  10 = a central bank changes its policy rate or guidance; a sovereign-debt shock",
    "   8 = a CB board member shifts expectations; a major CPI/payroll surprise; a G7 fiscal package",
    "   6 = a notable data print or official comment that moves a market a little",
    "   4 = sector or mid-cap company news with a macro angle; a regulatory proposal",
    "   2 = a single company's routine result; a local economic story",
    "   0 = crime, weather, sport, culture, human interest",
    "  corporate: 10 = mega-M&A or accounting fraud at a major listed company; 6 = a large-cap",
    "   strategic move; 3 = a mid-cap acquisition; 1 = a regional firm's news",
    "  commodities: 10 = supply shock or cartel decision moving a benchmark; 5 = a contract/tariff",
    "   story with price read-through; 1 = a local harvest note",
    "  credit: 10 = a systemic funding stress or a $10B+ issuance/financing structure;",
    "   6 = a large-cap bond raise or private-credit deal; 2 = a routine refinancing",
]

def log(m):
    print("[" + datetime.now(JST).strftime("%H:%M:%S") + " JST] " + m, flush=True)

def run_date_jst(now=None):
    now = now or datetime.now(timezone.utc)
    return now.astimezone(JST).strftime("%Y-%m-%d")

# ---- input ------------------------------------------------------------------

def load_articles(raw_dir=RAW_DIR):
    out = []
    for sec in sorted(os.listdir(raw_dir)):
        sd = Path(raw_dir) / sec
        if not sd.is_dir():
            continue
        for f in sorted(sd.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                log("  SKIP unreadable " + f.name + ": " + type(e).__name__)
                continue
            if not d.get("url") or not (d.get("title_en") or d.get("translated_text")):
                continue
            d["_path"] = str(f)
            out.append(d)
    return out

def dedup(articles):
    kept, removed = dedup_articles_by_japanese_shingles(articles)
    if removed:
        log("dedup: removed %d verbatim duplicates, %d remain" % (removed, len(kept)))
    return kept

# ---- prompt -----------------------------------------------------------------

def build_prompt(batch, cand_by_url, theses_by_id, rubric):
    axes_txt = "\n".join("- %s: %s" % (a, rubric["axis_guidance"][a]) for a in AXES)
    str_txt = "\n".join("- %s: %s" % (k, v) for k, v in rubric["strength_guidance"].items())
    lines = [
        "You score Japanese news articles for a macro/rates investor. Return ONLY a JSON object",
        '{"articles": [...]} with one entry per article, in any order, each:',
        '{"url": <exact URL given>, "axes": {' + ", ".join('"%s": 0-10' % a for a in AXES) + "},",
        ' "thesis": {"id": <one of the candidate ids or null>, "strength": 0-3},',
        ' "event": <short normalised label for the underlying story, e.g. "BoJ signals October hike">,',
        ' "reason": <one sentence>}',
        "No markdown, no code fences, no preamble.",
        "",
        "AXES (0 = irrelevant, 10 = the most important story of the year on that axis):",
        axes_txt,
        "",
        "THESIS STRENGTH (only for a candidate id listed under the article; null otherwise):",
        str_txt,
        "",
        *SCALE,
        "",
        "The first two articles are fixed calibration examples: score them too, on the same scale.",
        "",
    ]
    for a in [*CALIBRATION, *batch]:
        cands = cand_by_url.get(a["url"], [])
        lines.append("=== ARTICLE ===")
        lines.append("URL: " + a["url"])
        lines.append("SOURCE: " + (a.get("source") or ""))
        lines.append("TITLE: " + (a.get("title_en") or a.get("title") or ""))
        lines.append("BODY: " + (a.get("translated_text") or "")[:BODY_CHARS])
        if cands:
            lines.append("CANDIDATE THESES:")
            for cid in cands:
                t = theses_by_id.get(cid, {})
                lines.append("  - %s: %s" % (cid, t.get("title", cid)))
            # A listed candidate is a REQUIRED judgement: return the id with a
            # strength, 0 if unrelated. Allowing null here let a keyword-matched
            # direct link come back empty on one run in two (golden, 2026-09-29):
            # an explicit 0 is a decision, an omission was a shrug.
            lines.append("  You MUST return one of these ids as thesis.id with a strength 0-3 — "
                         "strength 0 if unrelated. Do not return null here.")
        else:
            lines.append("CANDIDATE THESES: none (thesis.id must be null)")
        lines.append("")
    return "\n".join(lines)

# ---- model ------------------------------------------------------------------

TEMPERATURE = 0.2
# M2.5 answers a batch of 12 in ~25 s. Slower models (M2.7, M3 reason before
# answering) can take several minutes; 90 s made them look broken.
CALL_TIMEOUT = 90

def scoring_model(rubric):
    """rubric.json's model, overridable by SCORING_MODEL; the translate step's
    pin is only the last resort. Measured 2026-09-29 on the same 15 articles:
    M3 re-scores with ~half M2.5's run-to-run drift (0.60 vs 1.00 mean axis
    drift, 1 vs 3 outliers) and faster, so scoring and translation now differ."""
    return os.environ.get("SCORING_MODEL") or rubric.get("model") or MODEL

def build_payload(prompt, rubric):
    return {"model": scoring_model(rubric), "max_tokens": 4096, "temperature": TEMPERATURE,
            "messages": [{"role": "user", "content": prompt}]}

_RUBRIC = None
def call_model(prompt, rubric=None):
    global _RUBRIC
    if rubric is None:
        _RUBRIC = _RUBRIC or load_rubric()
        rubric = _RUBRIC
    endpoint, key = get_endpoint_and_key()
    payload = build_payload(prompt, rubric)
    req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "x-api-key": key,
                                          "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=CALL_TIMEOUT) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    blocks = result.get("content", [])
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()

def _strip_fences(text):
    t = text.strip()
    if t.startswith("```"):
        t = "\n".join(t.splitlines()[1:])
        if t.endswith("```"):
            t = t[:-3].rstrip()
    return t

def parse_scores(text, expected_urls):
    try:
        data = json.loads(_strip_fences(text))
    except Exception as e:
        raise ValueError("model output is not JSON: " + type(e).__name__)
    items = data.get("articles") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("model output has no 'articles' list")
    got = {}
    for it in items:
        if not isinstance(it, dict) or not it.get("url"):
            continue
        axes = it.get("axes") or {}
        clean = {}
        for a in AXES:
            v = axes.get(a, 0)
            if v is None:
                v = 0
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    raise ValueError("non-numeric axis %s=%r for %s" % (a, v, it["url"]))
            clean[a] = max(0.0, min(10.0, float(v)))
        th = it.get("thesis") or {}
        got[it["url"]] = {"url": it["url"], "axes": clean,
                          "thesis": {"id": th.get("id"), "strength": th.get("strength", 0)},
                          "event": (it.get("event") or "").strip(),
                          "reason": (it.get("reason") or "").strip()}
    missing = [u for u in expected_urls if u not in got]
    if missing:
        raise ValueError("model omitted %d of %d URLs" % (len(missing), len(expected_urls)))
    return {u: got[u] for u in expected_urls}

def score_batch(batch, cand_by_url, theses_by_id, rubric, call=None):
    # `call` resolves at call time, not definition time, so a test that
    # patches score_articles.call_model is actually honoured — a default of
    # `call=call_model` bound the original and let one test hit the real API.
    call = call or call_model
    urls = [a["url"] for a in CALIBRATION] + [a["url"] for a in batch]
    prompt = build_prompt(batch, cand_by_url, theses_by_id, rubric)
    for attempt in (1, 2):
        try:
            got = parse_scores(call(prompt), urls)
            return [it for u, it in got.items() if u not in CALIBRATION_URLS]
        except Exception as e:
            log("  batch of %d failed (attempt %d): %s" % (len(batch), attempt, type(e).__name__))
            if attempt == 1:
                time.sleep(RETRY_WAIT)
    if len(batch) == 1:
        log("  GIVE UP " + batch[0]["url"])
        return []
    out = []
    for a in batch:                                   # split into singles
        out.extend(score_batch([a], cand_by_url, theses_by_id, rubric, call=call))
    return out

def score_all(todo, cand_by_url, theses_by_id, rubric, call=None, deadline=None):
    """Every batch, in order, until done or the deadline passes.

    The deadline is checked between batches, never mid-call, so the batch in
    flight always completes. A stalled endpoint could otherwise run this for
    hours — past the unit's 3 h ceiling, taking the two existing digests with
    it (review finding, 2026-09-29). What was scored before the cut-off is
    kept; the log says how far it got.
    """
    out = []
    for i in range(0, len(todo), BATCH):
        if deadline is not None and i > 0 and datetime.now(timezone.utc) >= deadline:
            log("deadline reached after %d/%d — storing what was scored" % (i, len(todo)))
            break
        out.extend(score_batch(todo[i:i + BATCH], cand_by_url, theses_by_id, rubric, call=call))
        log("scored %d/%d" % (min(i + BATCH, len(todo)), len(todo)))
    return out

# ---- rows -------------------------------------------------------------------

def make_row(article, item, rubric, matched, run_date, thash):
    axes = item["axes"]
    tid = item["thesis"].get("id") or None
    strength = clamp_strength(item["thesis"].get("strength", 0), matched) if tid else 0
    if strength == 0:
        tid = None          # an explicit "unrelated" is no link, not a link worth nothing
    row = {"url": article["url"], "run_date": run_date,
           "published_at": article.get("published_at") or article.get("scraped_at") or article.get("date"),
           "source": article.get("source") or "", "title_en": article.get("title_en") or article.get("title") or "",
           "event": item.get("event") or article.get("title_en") or article.get("title") or "",
           "thesis_id": tid, "thesis_strength": strength, "thesis_matched": 1 if (tid and matched) else 0,
           "score": combine(axes, strength, rubric), "reason": item.get("reason") or "",
           "rubric_version": rubric["version"], "theses_hash": thash,
           "scored_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    for a in AXES:
        row["ax_" + a] = float(axes.get(a, 0))
    return row

# ---- storage ----------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS news_scores (
  url TEXT PRIMARY KEY, run_date TEXT NOT NULL, published_at TEXT,
  source TEXT NOT NULL, title_en TEXT NOT NULL, event TEXT,
  ax_rates REAL, ax_macro REAL, ax_commodities REAL, ax_credit REAL, ax_politics REAL, ax_corporate REAL,
  thesis_id TEXT, thesis_strength INTEGER, thesis_matched INTEGER,
  score REAL NOT NULL, reason TEXT, rubric_version TEXT NOT NULL, theses_hash TEXT NOT NULL,
  scored_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_scores_run ON news_scores(run_date, score DESC);
"""

def connect(db_path=DB_PATH):
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn

def already_scored(conn, urls, rubric_version):
    if not urls:
        return set()
    q = "SELECT url FROM news_scores WHERE rubric_version=? AND url IN (%s)" % ",".join("?" * len(urls))
    return {r[0] for r in conn.execute(q, [rubric_version, *urls])}

COLS = ["url", "run_date", "published_at", "source", "title_en", "event",
        *["ax_" + a for a in AXES], "thesis_id", "thesis_strength", "thesis_matched",
        "score", "reason", "rubric_version", "theses_hash", "scored_at_utc"]

def store(conn, rows):
    # A re-score (new rubric version) updates everything EXCEPT run_date: the
    # digest an article belongs to is the run that first scored it (spec §4).
    # INSERT OR REPLACE would have pulled the whole 3-day archive into today.
    upd = ", ".join("%s=excluded.%s" % (c, c) for c in COLS if c not in ("url", "run_date"))
    sql = ("INSERT INTO news_scores (%s) VALUES (%s) ON CONFLICT(url) DO UPDATE SET %s"
           % (",".join(COLS), ",".join("?" * len(COLS)), upd))
    conn.executemany(sql, [[r.get(c) for c in COLS] for r in rows])
    conn.commit()
    return len(rows)

# ---- main -------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="score and print, write nothing")
    ap.add_argument("--limit", type=int, default=0, help="only the first N articles")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--raw", default=str(RAW_DIR))
    ap.add_argument("--deadline-min", type=int, default=60,
                    help="stop starting new batches after this many minutes (default 60)")
    args = ap.parse_args(argv)

    rubric = load_rubric()
    ledger = load_theses()                                   # fail loud
    theses = all_theses(rubric, ledger)
    theses_by_id = {t["id"]: t for t in theses}
    thash = theses_hash(theses)
    run_date = run_date_jst()

    arts = dedup(load_articles(Path(args.raw)))
    # Stale articles never reach the model: no tokens, no row. The archive
    # holds three days of scrapes plus republished pieces dated months back.
    max_age = int(rubric.get("max_age_days", 2))
    fresh = [a for a in arts if is_fresh(a.get("published_at") or a.get("scraped_at") or a.get("date"), run_date, max_age)]
    if len(fresh) != len(arts):
        log("freshness: skipped %d articles published more than %d days before %s" % (len(arts) - len(fresh), max_age, run_date))
    arts = fresh
    if args.limit:
        arts = arts[:args.limit]
    conn = connect(Path(args.db))
    done = set() if args.dry_run else already_scored(conn, [a["url"] for a in arts], rubric["version"])
    todo = [a for a in arts if a["url"] not in done]
    log("articles: %d translated · %d already scored (rubric %s) · %d to score"
        % (len(arts), len(done), rubric["version"], len(todo)))

    cand_by_url = {a["url"]: candidates(a, theses) for a in todo}
    deadline = datetime.now(timezone.utc) + timedelta(minutes=args.deadline_min)
    items = score_all(todo, cand_by_url, theses_by_id, rubric, deadline=deadline)
    rows = []
    for it in items:
        a = next(x for x in todo if x["url"] == it["url"])
        matched = bool(it["thesis"].get("id")) and it["thesis"]["id"] in cand_by_url.get(a["url"], [])
        rows.append(make_row(a, it, rubric, matched, run_date, thash))

    # Work to do and nothing scored is a failure, whatever older rows exist.
    # `not done` alone was dead in steady state: with yesterday's articles
    # already in the table, an outage that failed every batch exited 0 with
    # "stored 0 rows" — no digest, no OnFailure (review finding, 2026-09-29).
    if not rows and (todo or not done):
        log("ZERO articles scored — refusing to report a quiet day")
        return 1
    if args.dry_run:
        for r in sorted(rows, key=sort_key)[:20]:
            print("%5.1f  %-22s %s  [%s%s]" % (r["score"], r["source"][:22], r["title_en"][:70],
                  r["thesis_id"] or "-", "" if r["thesis_matched"] or not r["thesis_id"] else "?"))
        log("dry-run: %d scored, nothing written" % len(rows))
        return 0
    n = store(conn, rows)
    log("stored %d rows for run_date %s" % (n, run_date))
    return 0

if __name__ == "__main__":
    sys.exit(main())
