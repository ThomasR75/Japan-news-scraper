# Ranked Japan News — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score every translated article on six coverage axes plus a link to the live thesis ledger, store the scores in `carry.db`, publish a ranked top-50 HTML and a top-5 Telegram message alongside the existing digests, and expose the ranking as a carry-dash tab.

**Architecture:** A new non-fatal pipeline step (`score_articles.py`) runs after translation: shingle-dedup → batches of 10 to MiniMax → `news_scores` rows (WAL). Two small publishers read that table: `generate_ranked_html.py` (top 50, same-story markers) and `publish_ranked.py` (Telegram document + top-5 events message). carry-dash reads the same table through two new routes and a `News.jsx` tab. All tunables live in `rubric.json`; every row records `rubric_version` and `theses_hash`.

**Tech Stack:** Python 3.12 stdlib (`sqlite3`, `urllib`, `json`, `concurrent.futures`), MiniMax-M2.5 via the existing `minimax_translate.get_endpoint_and_key()`, Node/Express + `better-sqlite3` (existing), React (existing, house tokens from `Kev.jsx`).

**Spec:** `docs/specs/2026-09-28-news-relevance-scoring-design.md` (approved, revised 2026-09-29). Amendment carried by this plan: **the ranked HTML is the top 50 articles**, fixed size, each with source — not "everything above threshold". Threshold still governs the Telegram top-5 and the tab's default filter.

## Global Constraints

- Python: `python3` is 3.12; no new third-party packages (spec: stdlib + existing MiniMax client). `bs4` is available but not needed.
- Existing digests are **untouched**: `generate_html.py`, `send_digests.sh`, and their two documents keep shipping exactly as today.
- New pipeline steps run through `run_scraper` (non-fatal). The existing two digests must ship even if scoring fails.
- **Zero articles scored → exit non-zero and publish nothing.** Zero above threshold → publish a quiet-day digest.
- `carry.db` is opened with `PRAGMA journal_mode=WAL` and `timeout=30`; never skip the write and publish anyway.
- Scores are written once per `(url, rubric_version)`; re-scoring only when `rubric.json:version` changes.
- Thesis links of strength 2–3 **must** trace to a keyword in `theses.json` (matched on JP original or EN translation); an unmatched model-proposed link is clamped to strength 1.
- `commodities-book` does **not** exist. One pseudo-thesis only: `hyperscaler-financing`.
- `score = best_axis + bonus`, **no cap**; ties break on the sum of the six axes.
- Digest membership is `run_date` (JST date of the run that first scored the article), never `published_at`.
- Secrets: MiniMax key via `MINIMAX_API_KEY` env or `~/.openclaw/openclaw.json` (existing helper); Telegram token from `~/.openclaw/openclaw.json['channels']['telegram']['botToken']`; chat `8004116253`. Nothing hardcoded in new files.
- Tests are standalone `python3 test_*.py` files printing `✓` lines and exiting 1 on failure (house style). carry-dash JS tests follow `server/test_stir_current.js` (plain `node`, `assert`).
- Timezone: JST for `run_date` (`timezone(timedelta(hours=9))`).
- Atomic writes for any JSON/HTML file: `tmp + os.replace`.

## Review Focus

1. **An article with `title_en` but empty `translated_text`** (translation partially failed) — expected: still scored on the title alone, not silently dropped. Pinned in Task 3 (`t_title_only_article_is_scored`).
2. **The model returns a URL not in the batch, or omits one** — expected: extras ignored, a missing URL is an error that triggers the retry/split path, never a silent gap. Pinned in Task 3 (`t_parse_rejects_missing_url_and_ignores_extras`).
3. **Axis values outside 0–10 or non-numeric** (`11`, `"high"`, `null`) — expected: numeric out-of-range clamped, non-numeric treated as parse failure. Pinned in Task 3 (`t_axis_values_are_clamped_or_rejected`).
4. **`event` label missing or empty** — expected: fall back to `title_en` so collapse still functions and the row is not lost. Pinned in Task 3 (`t_missing_event_falls_back_to_title`).
5. **The same day re-run** (as happened 2026-09-29) — expected: already-scored articles are not re-sent to MiniMax; the HTML for that `run_date` includes rows from both runs. Pinned in Task 3 (`t_already_scored_are_skipped`) and Task 5 (`t_fetch_rows_spans_the_whole_run_date`).

---

## File Structure

| File | Responsibility |
|---|---|
| `japan_news_scraper/rubric.json` | All tunables: version, axes, bonus table, threshold, pseudo-thesis, top-N sizes |
| `japan_news_scraper/scoring_rubric.py` | Pure maths: combine, sort key, event normalisation, event collapse. No I/O. |
| `japan_news_scraper/thesis_match.py` | Load `theses.json`, hash live ids, keyword candidates (JP+EN), strength clamp |
| `japan_news_scraper/score_articles.py` | The pipeline step: load → dedup → prompt/batch → parse → rows → `news_scores` |
| `japan_news_scraper/generate_ranked_html.py` | Top-50 HTML from `news_scores` for a run date |
| `japan_news_scraper/publish_ranked.py` | Telegram: sendDocument (HTML) + top-5 events message |
| `japan_news_scraper/run.sh` | Wire the three steps in (non-fatal) |
| `japan_news_scraper/test_scoring_rubric.py`, `test_thesis_match.py`, `test_score_articles.py`, `test_generate_ranked_html.py`, `test_publish_ranked.py`, `test_scoring_golden.py` | Tests, one per module |
| `japan_news_scraper/golden/golden_set.json` | ~15 real articles with corrected scores |
| `apps/carry-dash/server/db.js` | `newsTableExists`, `getNewsRuns`, `getNewsScored` |
| `apps/carry-dash/server/index.js` | `GET /api/news/runs`, `GET /api/news/scored` |
| `apps/carry-dash/server/test_news_api.js` | DB helper tests against a temp DB |
| `apps/carry-dash/client/src/api.js`, `News.jsx`, `Nav.jsx`, `App.jsx` | The tab |

---

### Task 1: rubric.json + scoring maths

**Files:**
- Create: `japan_news_scraper/rubric.json`
- Create: `japan_news_scraper/scoring_rubric.py`
- Test: `japan_news_scraper/test_scoring_rubric.py`

**Interfaces:**
- Produces:
  - `RUBRIC_PATH: Path`, `load_rubric(path=RUBRIC_PATH) -> dict`
  - `AXES = ["rates","macro","commodities","credit","politics","corporate"]`
  - `best_axis(axes: dict) -> tuple[str, float]`
  - `combine(axes: dict, strength: int, rubric: dict) -> float`  (best + bonus, no cap)
  - `sort_key(row: dict) -> tuple`  (row has `score` and `ax_*` keys; returns `(-score, -axis_sum)`)
  - `event_key(label: str) -> str`  (lowercased, punctuation stripped, whitespace collapsed)
  - `same_event(a: str, b: str) -> bool`  (exact key match, else token-Jaccard ≥ 0.6)
  - `collapse_events(rows: list[dict]) -> list[dict]`  (each `{"lead": row, "also": [row,...]}` in `sort_key` order; rows need `event`, `score`, `ax_*`, `source`)

- [ ] **Step 1: Write the rubric file**

`japan_news_scraper/rubric.json`:
```json
{
  "version": "2026-09-29.1",
  "axes": ["rates", "macro", "commodities", "credit", "politics", "corporate"],
  "axis_guidance": {
    "rates": "central bank policy, STIR, sovereign yields, JGBs",
    "macro": "growth, inflation, FX, capital flows, fiscal",
    "commodities": "wheat, sugar, coffee, cocoa, natgas, cattle, copper, oil, soy, iron, aluminium; energy and shipping",
    "credit": "issuance, spreads, financing structure; hyperscaler/AI capex funding lands here",
    "politics": "only insofar as it moves policy or fiscal outcomes",
    "corporate": "single names, M&A, earnings"
  },
  "bonus": {"0": 0.0, "1": 0.5, "2": 1.5, "3": 2.5},
  "strength_guidance": {
    "1": "weak: same sector, no read-through to the position",
    "2": "related: moves a driver of the thesis",
    "3": "direct: moves the thesis itself"
  },
  "publish_threshold": 6.0,
  "top_n_html": 50,
  "top_n_telegram": 5,
  "event_jaccard": 0.6,
  "pseudo_theses": [
    {
      "id": "hyperscaler-financing",
      "title": "How AI/datacentre capex is funded",
      "keywords": ["datacenter", "data center", "data centre", "データセンター", "hyperscaler",
                   "capex", "設備投資", "bond issuance", "社債", "private credit",
                   "sale-leaseback", "spv", "vendor financing", "microsoft", "oracle",
                   "amazon web services", "aws", "google cloud", "meta platforms",
                   "coreweave", "nvidia", "gpu"]
    }
  ]
}
```

- [ ] **Step 2: Write the failing tests**

`japan_news_scraper/test_scoring_rubric.py`:
```python
"""
test_scoring_rubric.py — the arithmetic and grouping behind the ranking.

    cd japan_news_scraper && python3 test_scoring_rubric.py

Everything here is pure: no model, no database. If these are wrong the whole
ranking is wrong in a way no golden set would explain, so they are pinned
exactly rather than within a band.
"""
import sys
from scoring_rubric import (load_rubric, best_axis, combine, sort_key,
                            event_key, same_event, collapse_events, AXES)

R = load_rubric()

def ax(**kw):
    d = {a: 0 for a in AXES}
    d.update(kw)
    return d

def row(score, event, source, **axes):
    r = {"score": score, "event": event, "source": source}
    for a in AXES:
        r["ax_" + a] = axes.get(a, 0)
    return r


def t_rubric_loads_with_the_agreed_shape():
    assert R["version"], "version missing"
    assert R["axes"] == AXES, R["axes"]
    assert R["bonus"] == {"0": 0.0, "1": 0.5, "2": 1.5, "3": 2.5}, R["bonus"]
    assert R["publish_threshold"] == 6.0
    assert [p["id"] for p in R["pseudo_theses"]] == ["hyperscaler-financing"], \
        "commodities-book must not exist — it double-counts the commodities axis"
    print("  ✓ rubric.json has the agreed shape and exactly one pseudo-thesis")


def t_best_axis_picks_the_max():
    assert best_axis(ax(rates=9, macro=7)) == ("rates", 9)
    print("  ✓ best_axis returns the strongest axis")


def t_combine_is_best_plus_bonus_no_cap():
    assert combine(ax(rates=9), 0, R) == 9.0
    assert combine(ax(macro=8), 3, R) == 10.5
    assert combine(ax(rates=9), 3, R) == 11.5, "the cap at 10 was removed"
    assert combine(ax(corporate=4), 3, R) == 6.5
    print("  ✓ score = best_axis + bonus, and 11.5 is a legal score")


def t_no_link_still_ranks_on_coverage():
    boj = combine(ax(rates=9), 0, R)
    linked_dull = combine(ax(corporate=4), 3, R)
    assert boj > linked_dull, (boj, linked_dull)
    print("  ✓ a BoJ hike with no thesis outranks a linked dull earnings piece")


def t_commodity_story_scores_its_axis_and_nothing_more():
    assert combine(ax(commodities=6), 0, R) == 6.0
    print("  ✓ iron ore at 6 stays at 6 — the double-count regression")


def t_ties_break_on_axis_sum():
    a = row(9.0, "e1", "Nikkei", rates=9, macro=2)
    b = row(9.0, "e2", "Nikkei", rates=9)
    assert sorted([b, a], key=sort_key)[0] is a
    print("  ✓ equal scores are ordered by the sum of all axes")


def t_event_key_normalises():
    assert event_key("BoJ signals October hike.") == event_key("  boj SIGNALS october hike ")
    print("  ✓ event keys ignore case, punctuation and whitespace")


def t_same_event_tolerates_wording_drift():
    assert same_event("BoJ signals October hike", "BoJ signals an October hike")
    assert not same_event("BoJ signals October hike", "Nippon Steel iron ore talks stall")
    print("  ✓ small wording drift groups; different stories do not")


def t_collapse_groups_and_keeps_rank_order():
    rows = [
        row(9.0, "BoJ signals October hike", "Nikkei Economy", rates=9),
        row(8.5, "BoJ signals an October hike", "NHK Economy", rates=8),
        row(7.0, "Nippon Steel iron ore talks stall", "Nikkei Business", commodities=7),
        row(8.0, "boj signals october hike", "Asahi Business", rates=8),
    ]
    g = collapse_events(rows)
    assert len(g) == 2, len(g)
    assert g[0]["lead"]["source"] == "Nikkei Economy"
    assert sorted(r["source"] for r in g[0]["also"]) == ["Asahi Business", "NHK Economy"]
    assert g[1]["lead"]["source"] == "Nikkei Business" and g[1]["also"] == []
    print("  ✓ three outlets on one event collapse to one lead + two also; order kept")


if __name__ == "__main__":
    print("Running scoring-rubric tests...")
    failed = False
    for fn in (t_rubric_loads_with_the_agreed_shape, t_best_axis_picks_the_max,
               t_combine_is_best_plus_bonus_no_cap, t_no_link_still_ranks_on_coverage,
               t_commodity_story_scores_its_axis_and_nothing_more, t_ties_break_on_axis_sum,
               t_event_key_normalises, t_same_event_tolerates_wording_drift,
               t_collapse_groups_and_keeps_rank_order):
        try:
            fn()
        except Exception as e:
            print("  ✗ %s: %s: %s" % (fn.__name__, type(e).__name__, e))
            failed = True
    print("FAILED" if failed else "All tests passed.")
    sys.exit(1 if failed else 0)
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd ~/.openclaw/workspace/japan_news_scraper && python3 test_scoring_rubric.py`
Expected: `ModuleNotFoundError: No module named 'scoring_rubric'`

- [ ] **Step 4: Implement `scoring_rubric.py`**

```python
"""
scoring_rubric.py — the arithmetic and grouping behind the ranking. Pure.

score = best_axis + bonus[strength]     no cap; ties on the sum of the axes
event collapse: exact normalised match, else token-Jaccard >= rubric.event_jaccard
"""
import json, re
from pathlib import Path

RUBRIC_PATH = Path(__file__).resolve().parent / "rubric.json"
AXES = ["rates", "macro", "commodities", "credit", "politics", "corporate"]

def load_rubric(path=RUBRIC_PATH):
    r = json.loads(Path(path).read_text(encoding="utf-8"))
    if r.get("axes") != AXES:
        raise ValueError("rubric.json axes must be exactly %s" % AXES)
    return r

def best_axis(axes):
    name = max(AXES, key=lambda a: float(axes.get(a, 0) or 0))
    return name, float(axes.get(name, 0) or 0)

def combine(axes, strength, rubric):
    _, best = best_axis(axes)
    return round(best + float(rubric["bonus"][str(int(strength))]), 2)

def axis_sum(row):
    return sum(float(row.get("ax_" + a, 0) or 0) for a in AXES)

def sort_key(row):
    return (-float(row["score"]), -axis_sum(row))

_PUNCT = re.compile(r"[^\w\s぀-ヿ一-龯]+")

def event_key(label):
    s = (label or "").lower()
    s = _PUNCT.sub(" ", s)
    return " ".join(s.split())

def _tokens(label):
    return set(event_key(label).split())

def same_event(a, b, threshold=0.6):
    ka, kb = event_key(a), event_key(b)
    if ka and ka == kb:
        return True
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold

def collapse_events(rows, threshold=0.6):
    groups = []
    for r in sorted(rows, key=sort_key):
        for g in groups:
            if same_event(g["lead"]["event"], r["event"], threshold):
                g["also"].append(r)
                break
        else:
            groups.append({"lead": r, "also": []})
    return groups
```

- [ ] **Step 5: Run to verify it passes**

Run: `python3 test_scoring_rubric.py`
Expected: 9 `✓` lines, `All tests passed.`

- [ ] **Step 6: Commit**

```bash
cd ~/.openclaw/workspace/japan_news_scraper
git add rubric.json scoring_rubric.py test_scoring_rubric.py
git commit -m "scoring: rubric.json and the pure ranking maths

best_axis + bonus with no cap, tie-break on axis sum, event normalisation
and collapse. One pseudo-thesis; commodities-book deliberately absent."
```

---

### Task 2: Thesis matching

**Files:**
- Create: `japan_news_scraper/thesis_match.py`
- Test: `japan_news_scraper/test_thesis_match.py`

**Interfaces:**
- Consumes: `load_rubric()` from Task 1.
- Produces:
  - `THESES_PATH: Path` = `~/.openclaw/workspace/thesis_ledger/theses.json`
  - `load_theses(path=THESES_PATH) -> list[dict]`  raises `RuntimeError` if unreadable/malformed (fail loud)
  - `all_theses(rubric, ledger) -> list[dict]`  ledger + `rubric["pseudo_theses"]`, each with `id`, `title`, `keywords`, `pseudo: bool`
  - `theses_hash(theses) -> str`  sha1 of sorted ids
  - `candidates(article, theses) -> list[str]`  ids with any keyword (case-insensitive substring) in `title`, `title_en`, `extracted_text`, `translated_text`
  - `clamp_strength(strength: int, matched: bool) -> int`  `min(strength, 1)` when not matched; `max(0, min(3, …))` always

- [ ] **Step 1: Write the failing tests**

`japan_news_scraper/test_thesis_match.py`:
```python
"""
test_thesis_match.py — keyword linkage to the live thesis ledger.

    cd japan_news_scraper && python3 test_thesis_match.py

A strength-2/3 link must trace to a keyword a human wrote in theses.json.
The contract with that file is checked against the REAL file, so a ledger
edit that breaks it fails here rather than silently stopping all linkage.
"""
import sys
from scoring_rubric import load_rubric
from thesis_match import (load_theses, all_theses, theses_hash, candidates,
                          clamp_strength, THESES_PATH)

R = load_rubric()

def t_real_ledger_has_the_contract():
    t = load_theses()
    assert isinstance(t, list) and t, "theses.json must hold a non-empty list under 'theses'"
    for x in t:
        assert x.get("id") and isinstance(x.get("keywords"), list), x.get("id")
    print("  ✓ real theses.json: %d theses, each with id + keywords" % len(t))


def t_unreadable_ledger_fails_loud():
    try:
        load_theses("/nonexistent/theses.json")
    except RuntimeError:
        print("  ✓ a missing ledger raises rather than scoring on coverage alone")
        return
    raise AssertionError("expected RuntimeError")


def t_pseudo_theses_are_appended_and_flagged():
    t = all_theses(R, [{"id": "x", "title": "X", "keywords": ["foo"]}])
    ids = [x["id"] for x in t]
    assert ids == ["x", "hyperscaler-financing"], ids
    assert t[0]["pseudo"] is False and t[1]["pseudo"] is True
    print("  ✓ pseudo-theses follow the ledger and are flagged")


def t_hash_is_order_independent():
    a = theses_hash([{"id": "b"}, {"id": "a"}])
    b = theses_hash([{"id": "a"}, {"id": "b"}])
    assert a == b and len(a) == 40
    print("  ✓ theses_hash is a sha1 over sorted ids")


def t_candidates_match_japanese_and_english():
    th = [{"id": "jgb", "keywords": ["gpif", "jgb long end"], "pseudo": False},
          {"id": "hyp", "keywords": ["データセンター", "private credit"], "pseudo": True}]
    en_only = {"title_en": "GPIF shifts into super-long JGBs", "translated_text": ""}
    jp_only = {"title": "データセンター投資が急増", "extracted_text": ""}
    neither = {"title_en": "Local festival draws crowds", "translated_text": "..."}
    assert candidates(en_only, th) == ["jgb"]
    assert candidates(jp_only, th) == ["hyp"]
    assert candidates(neither, th) == []
    print("  ✓ keywords match on the Japanese original as well as the translation")


def t_paraphrased_translation_still_matches_via_japanese():
    th = [{"id": "jgb", "keywords": ["gpif"], "pseudo": False}]
    art = {"title": "GPIFが超長期国債へ", "title_en": "Government Pension Investment Fund shifts to super-long bonds"}
    assert candidates(art, th) == ["jgb"]
    print("  ✓ 'Government Pension Investment Fund' still links because the JP title says GPIF")


def t_clamp():
    assert clamp_strength(3, matched=True) == 3
    assert clamp_strength(3, matched=False) == 1
    assert clamp_strength(2, matched=False) == 1
    assert clamp_strength(0, matched=False) == 0
    assert clamp_strength(7, matched=True) == 3 and clamp_strength(-2, matched=True) == 0
    print("  ✓ an unmatched link is clamped to strength 1; range is enforced")


if __name__ == "__main__":
    print("Running thesis-match tests...")
    failed = False
    for fn in (t_real_ledger_has_the_contract, t_unreadable_ledger_fails_loud,
               t_pseudo_theses_are_appended_and_flagged, t_hash_is_order_independent,
               t_candidates_match_japanese_and_english,
               t_paraphrased_translation_still_matches_via_japanese, t_clamp):
        try:
            fn()
        except Exception as e:
            print("  ✗ %s: %s: %s" % (fn.__name__, type(e).__name__, e))
            failed = True
    print("FAILED" if failed else "All tests passed.")
    sys.exit(1 if failed else 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 test_thesis_match.py`
Expected: `ModuleNotFoundError: No module named 'thesis_match'`

- [ ] **Step 3: Implement `thesis_match.py`**

```python
"""
thesis_match.py — link articles to the live thesis ledger by keyword.

A strength-2/3 link MUST trace to a keyword a human wrote in theses.json. The
model may propose an unmatched link, but clamp_strength caps it at 1 (+0.5) —
small enough not to distort the ranking, visible enough to show which keyword
the ledger is missing. Matching runs on the Japanese original as well as the
English translation: the ledger's keywords are English and MiniMax may render
GPIF as "Government Pension Investment Fund".
"""
import hashlib, json, os
from pathlib import Path

THESES_PATH = Path(os.path.expanduser("~/.openclaw/workspace/thesis_ledger/theses.json"))
TEXT_FIELDS = ("title", "title_en", "extracted_text", "translated_text")

def load_theses(path=THESES_PATH):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError("theses.json unreadable at %s: %s" % (path, type(e).__name__)) from e
    theses = raw.get("theses") if isinstance(raw, dict) else raw
    if not isinstance(theses, list) or not theses:
        raise RuntimeError("theses.json: expected a non-empty list under 'theses'")
    for t in theses:
        if not t.get("id") or not isinstance(t.get("keywords"), list):
            raise RuntimeError("theses.json: every thesis needs id + keywords (bad: %r)" % t.get("id"))
    return theses

def all_theses(rubric, ledger):
    out = [{"id": t["id"], "title": t.get("title", t["id"]),
            "keywords": list(t["keywords"]), "pseudo": False} for t in ledger]
    for p in rubric.get("pseudo_theses", []):
        out.append({"id": p["id"], "title": p.get("title", p["id"]),
                    "keywords": list(p["keywords"]), "pseudo": True})
    return out

def theses_hash(theses):
    ids = sorted(t["id"] for t in theses)
    return hashlib.sha1("\n".join(ids).encode("utf-8")).hexdigest()

def _haystack(article):
    return " ".join((article.get(f) or "") for f in TEXT_FIELDS).lower()

def candidates(article, theses):
    hay = _haystack(article)
    hits = []
    for t in theses:
        if any(k.lower() in hay for k in t["keywords"] if k):
            hits.append(t["id"])
    return hits

def clamp_strength(strength, matched):
    try:
        s = int(strength)
    except (TypeError, ValueError):
        s = 0
    s = max(0, min(3, s))
    return s if matched else min(s, 1)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 test_thesis_match.py`
Expected: 7 `✓`, `All tests passed.`

- [ ] **Step 5: Commit**

```bash
git add thesis_match.py test_thesis_match.py
git commit -m "scoring: keyword linkage to theses.json, JP+EN, unmatched clamped to 1"
```

---

### Task 3: The scorer

**Files:**
- Create: `japan_news_scraper/score_articles.py`
- Test: `japan_news_scraper/test_score_articles.py`
- Reference: `japan_news_scraper/generate_html.py:35` (`dedup_articles_by_japanese_shingles(articles, threshold=0.55) -> (list, removed)`), `minimax_translate.py:6` (`get_endpoint_and_key() -> (endpoint, key)`)

**Interfaces:**
- Consumes: Task 1 (`load_rubric, AXES, combine, best_axis`), Task 2 (`load_theses, all_theses, theses_hash, candidates, clamp_strength`).
- Produces:
  - `RAW_DIR: Path`, `DB_PATH: Path` (= `~/.openclaw/workspace/apps/carry-dash/server/carry.db`), `BATCH = 10`
  - `run_date_jst(now=None) -> str`
  - `load_articles(raw_dir=RAW_DIR) -> list[dict]`  (translated only; each gets `_path`)
  - `dedup(articles) -> list[dict]`
  - `build_prompt(batch, cand_by_url, theses_by_id, rubric) -> str`
  - `parse_scores(text, expected_urls) -> dict[str, dict]`  raises `ValueError`
  - `call_model(prompt) -> str`
  - `score_batch(batch, cand_by_url, theses_by_id, rubric, call=call_model) -> list[dict]`  (items; retry once, then singles)
  - `make_row(article, item, rubric, matched, run_date, thash) -> dict`  (a `news_scores` row)
  - `connect(db_path=DB_PATH) -> sqlite3.Connection`  (WAL, timeout 30, table ensured)
  - `already_scored(conn, urls, rubric_version) -> set[str]`
  - `store(conn, rows) -> int`
  - `main(argv=None) -> int`  flags `--dry-run`, `--limit N`, `--db PATH`, `--raw PATH`

- [ ] **Step 1: Write the failing tests**

`japan_news_scraper/test_score_articles.py`:
```python
"""
test_score_articles.py — the scoring step, with the model faked.

    cd japan_news_scraper && python3 test_score_articles.py

Covers the plumbing the spec's failure table depends on: run_date rather
than published_at, the retry-then-split path, idempotence per rubric
version, and the refusal to report zero articles as a quiet day.
"""
import json, os, sqlite3, sys, tempfile, datetime as dt
from pathlib import Path
import score_articles as sa
from scoring_rubric import load_rubric, AXES
from thesis_match import all_theses

R = load_rubric()
TH = all_theses(R, [{"id": "jgb", "title": "JGB", "keywords": ["gpif", "jgb"]}])
TH_BY_ID = {t["id"]: t for t in TH}
JST = dt.timezone(dt.timedelta(hours=9))
sa.RETRY_WAIT = 0          # score_batch reads the module global at call time; no 5 s naps in tests

def art(i, **kw):
    d = {"url": "https://x/%d" % i, "source": "Nikkei Economy", "title": "T%d" % i,
         "title_en": "Title %d" % i, "extracted_text": "本文%d" % i,
         "translated_text": "Body %d" % i, "published_at": "2026-09-30T06:00:00+09:00"}
    d.update(kw)
    return d

def item(url, **kw):
    it = {"url": url, "axes": {a: 0 for a in AXES}, "thesis": {"id": None, "strength": 0},
          "event": "Event " + url[-1], "reason": "r"}
    it.update(kw)
    return it

def fake_call(items_by_url, missing=(), extra=()):
    def _c(prompt):
        urls = [l.split("URL: ", 1)[1].strip() for l in prompt.splitlines() if l.startswith("URL: ")]
        out = [items_by_url[u] for u in urls if u not in missing]
        out += [item(u) for u in extra]
        return json.dumps({"articles": out})
    return _c

def tmpdb():
    return sa.connect(Path(tempfile.mkdtemp()) / "t.db")


def t_run_date_is_jst_run_day_not_published_at():
    now = dt.datetime(2026, 9, 28, 20, 5, tzinfo=dt.timezone.utc)   # 05:05 JST on the 29th
    assert sa.run_date_jst(now) == "2026-09-29"
    a = art(1, published_at="2026-09-30T06:00:00+09:00")               # Nikkei 朝刊 tomorrow
    row = sa.make_row(a, item(a["url"]), R, matched=False, run_date="2026-09-29", thash="h")
    assert row["run_date"] == "2026-09-29" and row["published_at"] == a["published_at"]
    print("  ✓ run_date is the run's JST day even when published_at says tomorrow")


def t_title_only_article_is_scored():
    a = art(1, translated_text="")
    row = sa.make_row(a, item(a["url"], axes={**{x: 0 for x in AXES}, "rates": 7}), R, False, "2026-09-29", "h")
    assert row["score"] == 7.0 and row["title_en"] == "Title 1"
    print("  ✓ an article with only a translated title is still scored")


def t_parse_rejects_missing_url_and_ignores_extras():
    ok = json.dumps({"articles": [item("https://x/1"), item("https://x/9")]})
    got = sa.parse_scores(ok, ["https://x/1"])
    assert set(got) == {"https://x/1"}
    try:
        sa.parse_scores(json.dumps({"articles": [item("https://x/1")]}), ["https://x/1", "https://x/2"])
    except ValueError:
        print("  ✓ an omitted URL is a parse error; an extra URL is ignored")
        return
    raise AssertionError("missing url must raise")


def t_axis_values_are_clamped_or_rejected():
    it = item("https://x/1", axes={**{a: 0 for a in AXES}, "rates": 14, "macro": -3})
    got = sa.parse_scores(json.dumps({"articles": [it]}), ["https://x/1"])
    assert got["https://x/1"]["axes"]["rates"] == 10 and got["https://x/1"]["axes"]["macro"] == 0
    bad = item("https://x/1", axes={**{a: 0 for a in AXES}, "rates": "high"})
    try:
        sa.parse_scores(json.dumps({"articles": [bad]}), ["https://x/1"])
    except ValueError:
        print("  ✓ out-of-range axes clamp; non-numeric axes are a parse error")
        return
    raise AssertionError("non-numeric axis must raise")


def t_missing_event_falls_back_to_title():
    a = art(1)
    row = sa.make_row(a, item(a["url"], event=""), R, False, "2026-09-29", "h")
    assert row["event"] == "Title 1"
    print("  ✓ an empty event label falls back to the title so collapse still works")


def t_unmatched_thesis_is_clamped_and_flagged():
    a = art(1)                                            # no 'gpif' anywhere
    it = item(a["url"], thesis={"id": "jgb", "strength": 3}, axes={**{x: 0 for x in AXES}, "macro": 8})
    row = sa.make_row(a, it, R, matched=False, run_date="2026-09-29", thash="h")
    assert row["thesis_strength"] == 1 and row["thesis_matched"] == 0 and row["score"] == 8.5
    b = art(2, translated_text="GPIF buys jgb")
    row2 = sa.make_row(b, item(b["url"], thesis={"id": "jgb", "strength": 3}, axes={**{x: 0 for x in AXES}, "macro": 8}),
                       R, matched=True, run_date="2026-09-29", thash="h")
    assert row2["thesis_strength"] == 3 and row2["thesis_matched"] == 1 and row2["score"] == 10.5
    print("  ✓ a model-proposed link without a keyword is worth +0.5 and marked as such")


def t_batch_retries_then_splits_into_singles():
    batch = [art(1), art(2), art(3)]
    items = {a["url"]: item(a["url"]) for a in batch}
    calls = {"n": 0}
    def flaky(prompt):
        calls["n"] += 1
        if calls["n"] <= 2:                       # whole-batch attempts fail
            return "not json"
        return fake_call(items)(prompt)            # singles succeed
    got = sa.score_batch(batch, {}, TH_BY_ID, R, call=flaky)
    assert len(got) == 3 and calls["n"] == 5, (len(got), calls["n"])
    print("  ✓ a malformed batch is retried once, then split into singles (2 + 3 calls)")


def t_one_bad_single_costs_one_article():
    batch = [art(1), art(2)]
    items = {a["url"]: item(a["url"]) for a in batch}
    def flaky(prompt):
        if "URL: https://x/2" in prompt and "URL: https://x/1" not in prompt:
            return "garbage"
        if "URL: https://x/1" in prompt and "URL: https://x/2" in prompt:
            return "garbage"
        return fake_call(items)(prompt)
    got = sa.score_batch(batch, {}, TH_BY_ID, R, call=flaky)
    assert [g["url"] for g in got] == ["https://x/1"]
    print("  ✓ a single that still fails is dropped and named; the rest survive")


def t_store_is_idempotent_per_rubric_version():
    conn = tmpdb()
    a = art(1)
    row = sa.make_row(a, item(a["url"]), R, False, "2026-09-29", "h")
    assert sa.store(conn, [row]) == 1
    assert sa.already_scored(conn, [a["url"]], R["version"]) == {a["url"]}
    assert sa.already_scored(conn, [a["url"]], "other-version") == set()
    sa.store(conn, [row])
    n = conn.execute("SELECT COUNT(*) FROM news_scores").fetchone()[0]
    assert n == 1
    print("  ✓ already_scored keys on rubric_version; store does not duplicate")


def t_connect_uses_wal():
    conn = tmpdb()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    print("  ✓ the scorer opens carry.db in WAL mode")


def t_main_refuses_zero_scored(tmp=None):
    raw = Path(tempfile.mkdtemp())
    (raw / "Nikkei_Economy").mkdir()
    (raw / "Nikkei_Economy" / "a.json").write_text(json.dumps(art(1, translated_text="", title_en="")), encoding="utf-8")
    db = Path(tempfile.mkdtemp()) / "t.db"
    rc = sa.main(["--raw", str(raw), "--db", str(db), "--dry-run"])
    assert rc == 1, rc
    print("  ✓ zero scorable articles exits non-zero — never a quiet day")


if __name__ == "__main__":
    print("Running score-articles tests...")
    failed = False
    for fn in (t_run_date_is_jst_run_day_not_published_at, t_title_only_article_is_scored,
               t_parse_rejects_missing_url_and_ignores_extras, t_axis_values_are_clamped_or_rejected,
               t_missing_event_falls_back_to_title, t_unmatched_thesis_is_clamped_and_flagged,
               t_batch_retries_then_splits_into_singles, t_one_bad_single_costs_one_article,
               t_store_is_idempotent_per_rubric_version, t_connect_uses_wal, t_main_refuses_zero_scored):
        try:
            fn()
        except Exception as e:
            print("  ✗ %s: %s: %s" % (fn.__name__, type(e).__name__, e))
            failed = True
    print("FAILED" if failed else "All tests passed.")
    sys.exit(1 if failed else 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 test_score_articles.py`
Expected: `ModuleNotFoundError: No module named 'score_articles'`

- [ ] **Step 3: Implement `score_articles.py`**

```python
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

from scoring_rubric import load_rubric, AXES, combine, best_axis, sort_key
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
    ]
    for a in batch:
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
        else:
            lines.append("CANDIDATE THESES: none")
        lines.append("")
    return "\n".join(lines)

# ---- model ------------------------------------------------------------------

def call_model(prompt):
    endpoint, key = get_endpoint_and_key()
    payload = {"model": MODEL, "max_tokens": 4096, "temperature": 0.2,
               "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "x-api-key": key,
                                          "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=90) as resp:
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

def score_batch(batch, cand_by_url, theses_by_id, rubric, call=call_model):
    urls = [a["url"] for a in batch]
    prompt = build_prompt(batch, cand_by_url, theses_by_id, rubric)
    for attempt in (1, 2):
        try:
            return list(parse_scores(call(prompt), urls).values())
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

# ---- rows -------------------------------------------------------------------

def make_row(article, item, rubric, matched, run_date, thash):
    axes = item["axes"]
    tid = item["thesis"].get("id") or None
    strength = clamp_strength(item["thesis"].get("strength", 0), matched) if tid else 0
    if not tid:
        strength = 0
    row = {"url": article["url"], "run_date": run_date,
           "published_at": article.get("published_at") or article.get("date"),
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
    sql = "INSERT OR REPLACE INTO news_scores (%s) VALUES (%s)" % (",".join(COLS), ",".join("?" * len(COLS)))
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
    args = ap.parse_args(argv)

    rubric = load_rubric()
    ledger = load_theses()                                   # fail loud
    theses = all_theses(rubric, ledger)
    theses_by_id = {t["id"]: t for t in theses}
    thash = theses_hash(theses)
    run_date = run_date_jst()

    arts = dedup(load_articles(Path(args.raw)))
    if args.limit:
        arts = arts[:args.limit]
    conn = connect(Path(args.db))
    done = set() if args.dry_run else already_scored(conn, [a["url"] for a in arts], rubric["version"])
    todo = [a for a in arts if a["url"] not in done]
    log("articles: %d translated · %d already scored (rubric %s) · %d to score"
        % (len(arts), len(done), rubric["version"], len(todo)))

    cand_by_url = {a["url"]: candidates(a, theses) for a in todo}
    rows = []
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        for it in score_batch(batch, cand_by_url, theses_by_id, rubric):
            a = next(x for x in batch if x["url"] == it["url"])
            matched = bool(it["thesis"].get("id")) and it["thesis"]["id"] in cand_by_url.get(a["url"], [])
            rows.append(make_row(a, it, rubric, matched, run_date, thash))
        log("scored %d/%d" % (min(i + BATCH, len(todo)), len(todo)))

    if not rows and not done:
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 test_score_articles.py`
Expected: 11 `✓`, `All tests passed.`

- [ ] **Step 5: Dry-run against the real archive (first real output)**

Run: `python3 score_articles.py --dry-run --limit 30`
Expected: log lines `dedup: …`, `articles: …`, `scored 10/30 … 30/30`, then a ranked top-20 with scores, sources and thesis ids. Read it. If MiniMax returns malformed JSON on every batch, the singles path will show `GIVE UP` lines — that is the signal to look at the prompt before continuing.

- [ ] **Step 6: Commit**

```bash
git add score_articles.py test_score_articles.py
git commit -m "scoring: the pipeline step — dedup, batch to MiniMax, news_scores in carry.db (WAL)

run_date not published_at; retry once then split to singles; idempotent
per rubric_version; exit 1 on zero scored."
```

---

### Task 4: Golden set

**Files:**
- Create: `japan_news_scraper/golden/golden_set.json`
- Test: `japan_news_scraper/test_scoring_golden.py`

**Interfaces:**
- Consumes: `score_articles.score_batch, build_prompt, call_model`, `scoring_rubric.load_rubric`, `thesis_match.*`.
- Produces: the fixture file; no code interfaces.

- [ ] **Step 1: Propose the fixture from a real run**

Run:
```bash
python3 - <<'PY'
import json, score_articles as sa
from scoring_rubric import load_rubric, sort_key
from thesis_match import load_theses, all_theses, candidates
R=load_rubric(); TH=all_theses(R, load_theses()); BY={t["id"]:t for t in TH}
arts=sa.dedup(sa.load_articles())
# pick a spread: top, middle, bottom by a first pass
cand={a["url"]:candidates(a,TH) for a in arts[:60]}
rows=[]
for i in range(0,60,10):
    b=arts[i:i+10]
    for it in sa.score_batch(b,cand,BY,R):
        a=next(x for x in b if x["url"]==it["url"])
        rows.append(sa.make_row(a,it,R,it["thesis"]["id"] in cand.get(a["url"],[]),"golden","h"))
rows.sort(key=sort_key)
pick=rows[:5]+rows[len(rows)//2-3:len(rows)//2+2]+rows[-5:]
out=[{"url":r["url"],"source":r["source"],"title_en":r["title_en"],
      "proposed_score":r["score"],"proposed_thesis":r["thesis_id"],
      "corrected_score":r["score"],"corrected_thesis":r["thesis_id"],
      "tolerance":1.5,"note":""} for r in pick]
import os; os.makedirs("golden",exist_ok=True)
json.dump(out,open("golden/golden_set.json","w"),ensure_ascii=False,indent=2)
print(len(out),"proposed; edit corrected_* in golden/golden_set.json")
PY
```
Expected: `15 proposed; edit corrected_* in golden/golden_set.json`. **Hand the file to Thomas**: he changes `corrected_score` / `corrected_thesis` where he disagrees. `tolerance` 1.5 by default.

- [ ] **Step 2: Write the golden test**

`japan_news_scraper/test_scoring_golden.py`:
```python
"""
test_scoring_golden.py — ~15 real articles with corrected scores.

    cd japan_news_scraper && python3 test_scoring_golden.py

Asserts within a tolerance band, not exactly: the point is to catch a
prompt or rubric change that quietly re-ranks everything, not to pin
model noise. Skips (exit 0) when no MiniMax key is reachable — a golden
test that fails for want of a network is noise.
"""
import json, sys
from pathlib import Path
import score_articles as sa
from scoring_rubric import load_rubric
from thesis_match import load_theses, all_theses, candidates

GOLD = Path(__file__).resolve().parent / "golden" / "golden_set.json"

def main():
    try:
        sa.get_endpoint_and_key()
    except Exception:
        print("  ~ no MiniMax key; golden test skipped")
        return 0
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    by_url = {a["url"]: a for a in sa.load_articles()}
    R = load_rubric(); TH = all_theses(R, load_theses()); BY = {t["id"]: t for t in TH}
    arts = [by_url[g["url"]] for g in gold if g["url"] in by_url]
    if len(arts) < len(gold) // 2:
        print("  ~ fewer than half the golden articles are still in the 3-day archive; skipped")
        return 0
    cand = {a["url"]: candidates(a, TH) for a in arts}
    rows = {}
    for i in range(0, len(arts), 10):
        b = arts[i:i + 10]
        for it in sa.score_batch(b, cand, BY, R):
            a = next(x for x in b if x["url"] == it["url"])
            rows[a["url"]] = sa.make_row(a, it, R, it["thesis"]["id"] in cand.get(a["url"], []), "golden", "h")
    bad = []
    for g in gold:
        r = rows.get(g["url"])
        if not r:
            continue
        if abs(r["score"] - g["corrected_score"]) > g.get("tolerance", 1.5):
            bad.append("%s: got %.1f, expected %.1f ±%.1f" % (g["title_en"][:50], r["score"], g["corrected_score"], g.get("tolerance", 1.5)))
        if g.get("corrected_thesis") and r["thesis_id"] != g["corrected_thesis"]:
            bad.append("%s: thesis %s, expected %s" % (g["title_en"][:50], r["thesis_id"], g["corrected_thesis"]))
    for b in bad:
        print("  ✗ " + b)
    print("  ✓ %d of %d golden articles within band" % (len(gold) - len(bad), len(gold)))
    print("FAILED" if bad else "All tests passed.")
    return 1 if bad else 0

if __name__ == "__main__":
    print("Running golden-set test...")
    sys.exit(main())
```

- [ ] **Step 3: Run it**

Run: `python3 test_scoring_golden.py`
Expected: `✓ N of 15 golden articles within band`, `All tests passed.` (Immediately after proposing, corrected == proposed, so it passes by construction; its value starts when Thomas edits it and whenever the prompt changes.)

- [ ] **Step 4: Commit**

```bash
git add golden/golden_set.json test_scoring_golden.py
git commit -m "scoring: golden set — proposed by the model, corrected by hand, asserted within a band"
```

---

### Task 5: Ranked top-50 HTML

**Files:**
- Create: `japan_news_scraper/generate_ranked_html.py`
- Test: `japan_news_scraper/test_generate_ranked_html.py`
- Modify: `japan_news_scraper/docs/specs/2026-09-28-news-relevance-scoring-design.md` §5 (amendment: top 50)

**Interfaces:**
- Consumes: `score_articles.connect`, `scoring_rubric.load_rubric, sort_key, same_event, AXES`.
- Produces:
  - `fetch_rows(conn, run_date) -> list[dict]`
  - `render(rows, run_date, rubric) -> str`  raises `ValueError` on zero rows
  - `OUT_DIR = HERE/"data"/"reports"`, `out_path(run_date) -> Path`
  - `main(argv=None) -> int`  (`--date`, `--db`) writes `data/reports/daily_ranked_<date>.html` atomically

- [ ] **Step 1: Write the failing tests**

`japan_news_scraper/test_generate_ranked_html.py`:
```python
"""
test_generate_ranked_html.py — the top-50 document.

    cd japan_news_scraper && python3 test_generate_ranked_html.py
"""
import sys, tempfile
from pathlib import Path
import score_articles as sa
import generate_ranked_html as g
from scoring_rubric import load_rubric, AXES

R = load_rubric()

def mkrow(i, score, event=None, source="Nikkei Economy", run_date="2026-09-29"):
    r = {"url": "https://x/%d" % i, "run_date": run_date, "published_at": "2026-09-29T06:00:00+09:00",
         "source": source, "title_en": "Title %d" % i, "event": event or ("Event %d" % i),
         "thesis_id": None, "thesis_strength": 0, "thesis_matched": 0, "score": score,
         "reason": "because", "rubric_version": R["version"], "theses_hash": "h", "scored_at_utc": "2026-09-28T20:30:00Z"}
    for a in AXES:
        r["ax_" + a] = 0.0
    r["ax_rates"] = min(10.0, score)
    return r

def db_with(rows):
    conn = sa.connect(Path(tempfile.mkdtemp()) / "t.db")
    sa.store(conn, rows)
    return conn


def t_zero_rows_raises():
    try:
        g.render([], "2026-09-29", R)
    except ValueError:
        print("  ✓ zero rows raises; never an empty document")
        return
    raise AssertionError("expected ValueError")


def t_renders_top_50_with_sources():
    rows = [mkrow(i, 12.0 - i * 0.1, source="Src %d" % (i % 7)) for i in range(80)]
    html = g.render(rows, "2026-09-29", R)
    assert html.count('class="article"') == 50, html.count('class="article"')
    assert "Title 0" in html and "Title 49" in html and "Title 50" not in html
    assert "Src 3" in html
    print("  ✓ exactly 50 articles, ranked, each with its source")


def t_same_story_marker_on_adjacent_rows():
    rows = [mkrow(1, 9.0, "BoJ signals October hike", "Nikkei Economy"),
            mkrow(2, 8.5, "BoJ signals an October hike", "NHK Economy"),
            mkrow(3, 7.0, "Nippon Steel iron ore talks stall", "Nikkei Business")]
    html = g.render(rows, "2026-09-29", R)
    assert html.count("same story") == 1, html.count("same story")
    assert html.index("Title 1") < html.index("Title 2") < html.index("Title 3")
    print("  ✓ the second outlet on an event sits under the first with a same-story marker")


def t_quiet_day_banner_when_none_above_threshold():
    rows = [mkrow(i, 3.0) for i in range(5)]
    html = g.render(rows, "2026-09-29", R)
    assert "quiet day" in html.lower() and html.count('class="article"') == 5
    print("  ✓ nothing above threshold renders a quiet-day banner, still lists what there is")


def t_fetch_rows_spans_the_whole_run_date():
    conn = db_with([mkrow(1, 9.0), mkrow(2, 8.0), mkrow(3, 7.0, run_date="2026-09-28")])
    rows = g.fetch_rows(conn, "2026-09-29")
    assert [r["url"] for r in rows] == ["https://x/1", "https://x/2"]
    print("  ✓ fetch_rows returns every row for the run date, in rank order, and no other day")


def t_main_writes_atomically():
    conn = db_with([mkrow(1, 9.0)])
    db = conn.execute("PRAGMA database_list").fetchone()[2]
    out = Path(tempfile.mkdtemp())
    g.OUT_DIR = out
    rc = g.main(["--date", "2026-09-29", "--db", db])
    assert rc == 0 and (out / "daily_ranked_2026-09-29.html").exists()
    assert not (out / "daily_ranked_2026-09-29.html.tmp").exists()
    print("  ✓ main writes data/reports/daily_ranked_<date>.html via tmp + replace")


if __name__ == "__main__":
    print("Running ranked-html tests...")
    failed = False
    for fn in (t_zero_rows_raises, t_renders_top_50_with_sources, t_same_story_marker_on_adjacent_rows,
               t_quiet_day_banner_when_none_above_threshold, t_fetch_rows_spans_the_whole_run_date,
               t_main_writes_atomically):
        try:
            fn()
        except Exception as e:
            print("  ✗ %s: %s: %s" % (fn.__name__, type(e).__name__, e))
            failed = True
    print("FAILED" if failed else "All tests passed.")
    sys.exit(1 if failed else 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 test_generate_ranked_html.py`
Expected: `ModuleNotFoundError: No module named 'generate_ranked_html'`

- [ ] **Step 3: Implement `generate_ranked_html.py`**

```python
#!/usr/bin/env python3
"""
generate_ranked_html.py — the day's top 50 articles by relevance score.

    python3 generate_ranked_html.py [--date YYYY-MM-DD] [--db PATH]

Fixed size (rubric.top_n_html), like the Telegram top-5 and for the same
reason: a document of constant length is one a person keeps reading. Every
row carries its source. Articles sharing an event sit adjacent, the second
and later ones marked "same story". Zero rows raises — an empty ranked
digest is exactly the failure this whole project exists to stop.
"""
import argparse, html as H, os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from scoring_rubric import load_rubric, sort_key, same_event, AXES
from score_articles import connect, DB_PATH, COLS, run_date_jst

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "data" / "reports"
JST = timezone(timedelta(hours=9))

def out_path(run_date):
    return OUT_DIR / ("daily_ranked_%s.html" % run_date)

def fetch_rows(conn, run_date):
    cur = conn.execute("SELECT %s FROM news_scores WHERE run_date=?" % ",".join(COLS), (run_date,))
    rows = [dict(zip(COLS, r)) for r in cur.fetchall()]
    return sorted(rows, key=sort_key)

def _axes_chips(r):
    parts = [(a, float(r.get("ax_" + a) or 0)) for a in AXES]
    parts = [p for p in parts if p[1] >= 3]
    parts.sort(key=lambda p: -p[1])
    return " ".join('<span class="chip">%s %d</span>' % (a, v) for a, v in parts[:3])

def _thesis(r):
    if not r.get("thesis_id"):
        return ""
    mark = "" if r.get("thesis_matched") else ' <span class="unmatched" title="model-proposed, no ledger keyword">?</span>'
    return '<span class="thesis">→ %s · %d%s</span>' % (H.escape(r["thesis_id"]), int(r.get("thesis_strength") or 0), mark)

def render(rows, run_date, rubric):
    if not rows:
        raise ValueError("no scored articles for %s — refusing to render an empty ranked digest" % run_date)
    rows = sorted(rows, key=sort_key)
    top = rows[: int(rubric.get("top_n_html", 50))]
    thr = float(rubric["publish_threshold"])
    above = sum(1 for r in rows if float(r["score"]) >= thr)
    out = []
    out.append("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>")
    out.append("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    out.append("<title>Ranked Japan News — %s</title>" % run_date)
    out.append("<style>"
               "*{box-sizing:border-box;margin:0;padding:0}"
               "body{font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;background:#f4f6f8;color:#1a1f24;padding:20px 16px 60px}"
               "h1{font-size:20px;margin-bottom:4px}.meta{color:#5b6570;font-size:12px;margin-bottom:16px}"
               ".banner{background:#fff6e0;border:1px solid #f0c36d;border-radius:8px;padding:10px 14px;margin-bottom:16px;font-size:13px}"
               ".article{background:#fff;border-radius:8px;padding:12px 14px;margin-bottom:8px;border:1px solid #e4e8ec}"
               ".article.same{margin-left:22px;border-left:3px solid #c9d2db}"
               ".row{display:flex;gap:10px;align-items:baseline}"
               ".score{font-weight:700;font-size:16px;min-width:44px;color:#0b5cad}"
               ".title{font-weight:600}.title a{color:inherit;text-decoration:none}.title a:hover{text-decoration:underline}"
               ".src{color:#5b6570;font-size:12px;margin-top:2px}"
               ".chip{display:inline-block;font-size:11px;background:#eef2f6;border-radius:999px;padding:1px 8px;margin-right:4px;color:#334}"
               ".thesis{font-size:11px;color:#7a3e00;background:#fff3e0;border-radius:999px;padding:1px 8px}"
               ".unmatched{color:#b45309;font-weight:700}"
               ".same-story{font-size:11px;color:#7a8794;font-style:italic}"
               ".reason{font-size:12px;color:#3d4852;margin-top:4px}"
               "</style></head><body>")
    out.append("<h1>📰 Ranked Japan News — %s JST</h1>" % run_date)
    out.append("<div class='meta'>top %d of %d scored · %d above %.1f · rubric %s · sources shown per article</div>"
               % (len(top), len(rows), above, thr, H.escape(rubric["version"])))
    if above == 0:
        out.append("<div class='banner'>Quiet day: nothing scored above %.1f. Listed anyway, lowest bar first.</div>" % thr)
    prev_event = None
    for r in top:
        same = prev_event is not None and same_event(prev_event, r.get("event") or "", rubric.get("event_jaccard", 0.6))
        cls = "article same" if same else "article"
        out.append("<div class='%s'>" % cls)
        out.append("<div class='row'><div class='score'>%.1f</div><div class='title'><a href='%s'>%s</a></div></div>"
                   % (float(r["score"]), H.escape(r["url"]), H.escape(r["title_en"])))
        pub = (r.get("published_at") or "")[:16].replace("T", " ")
        out.append("<div class='src'>%s%s%s</div>"
                   % (H.escape(r["source"]), " · " + H.escape(pub) if pub else "",
                      " · <span class='same-story'>same story</span>" if same else ""))
        chips = _axes_chips(r); th = _thesis(r)
        if chips or th:
            out.append("<div style='margin-top:6px'>%s %s</div>" % (chips, th))
        if r.get("reason"):
            out.append("<div class='reason'>%s</div>" % H.escape(r["reason"]))
        out.append("</div>")
        if not same:
            prev_event = r.get("event") or ""
    out.append("<div class='meta' style='margin-top:20px'>Generated %s · not openclaw</div>"
               % datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"))
    out.append("</body></html>")
    return "\n".join(out)

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=run_date_jst())
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args(argv)
    rubric = load_rubric()
    conn = connect(Path(args.db))
    rows = fetch_rows(conn, args.date)
    try:
        html = render(rows, args.date, rubric)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = out_path(args.date); tmp = str(p) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(html)
    os.replace(tmp, p)
    print("wrote %s (%d rows, %d bytes)" % (p, min(len(rows), rubric.get("top_n_html", 50)), len(html)))
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 test_generate_ranked_html.py`
Expected: 6 `✓`, `All tests passed.`

- [ ] **Step 5: Amend spec §5 for the top-50 document**

In `docs/specs/2026-09-28-news-relevance-scoring-design.md`, replace the sentence under §5 that begins `**Threshold.**` … `It governs the ranked digest, the tab's default filter, and the Telegram top-5.` with:

```
**Top 50.** The ranked HTML is the day's top 50 articles by score
(`rubric.json:top_n_html`), fixed size for the same reason the Telegram
message is: a document of constant length is one a person keeps reading.
Every row shows its source; a second outlet on the same event sits directly
beneath the first, marked "same story". Sent to Telegram as a document
alongside the two existing digests.

**Threshold.** `rubric.json:publish_threshold`, starting at **6.0** — the level
at which an article is either a mid-strength read on an axis that matters or a
weak one with a thesis link. It governs the Telegram top-5 and the tab's
default filter, and is shown in the HTML header as "N above 6.0". Expect to
tune this after a week of real output; it is one number in one file for
exactly that reason.
```

- [ ] **Step 6: Commit**

```bash
git add generate_ranked_html.py test_generate_ranked_html.py docs/specs/2026-09-28-news-relevance-scoring-design.md
git commit -m "ranked digest: top-50 HTML with sources and same-story markers; spec §5 amended"
```

---

### Task 6: Telegram publisher + pipeline wiring

**Files:**
- Create: `japan_news_scraper/publish_ranked.py`
- Test: `japan_news_scraper/test_publish_ranked.py`
- Modify: `japan_news_scraper/run.sh` (after line 53 `run_strict "translate_minimax.py"`, and after line ~69 `run_strict "send_digests.sh"`)
- Modify: `japan_news_scraper/CLAUDE.md` (pipeline steps list)

**Interfaces:**
- Consumes: `generate_ranked_html.fetch_rows, out_path`, `score_articles.connect, DB_PATH, run_date_jst`, `scoring_rubric.load_rubric, collapse_events`.
- Produces:
  - `top5_message(rows, run_date, rubric) -> str`
  - `send_document(path, caption, token, chat) -> bool`, `send_message(text, token, chat) -> bool`
  - `main(argv=None) -> int`  (`--date`, `--db`, `--no-telegram`) — exit 1 when there are no rows (never sends "0 scored")

- [ ] **Step 1: Write the failing tests**

`japan_news_scraper/test_publish_ranked.py`:
```python
"""
test_publish_ranked.py — the Telegram top-5 message.

    cd japan_news_scraper && python3 test_publish_ranked.py
"""
import sys
import publish_ranked as p
from scoring_rubric import load_rubric, AXES

R = load_rubric()

def mkrow(i, score, event, source="Nikkei Economy", thesis=None):
    r = {"url": "https://x/%d" % i, "source": source, "title_en": "Title %d" % i, "event": event,
         "thesis_id": thesis, "thesis_strength": 3 if thesis else 0, "thesis_matched": 1 if thesis else 0,
         "score": score, "reason": "why"}
    for a in AXES:
        r["ax_" + a] = 0.0
    r["ax_rates"] = min(10.0, score)
    return r


def t_top5_collapses_events_and_respects_threshold():
    rows = [mkrow(1, 9.0, "BoJ signals October hike"),
            mkrow(2, 8.5, "BoJ signals an October hike", "NHK Economy"),
            mkrow(3, 8.0, "GPIF shifts into super-long JGBs", thesis="japan-fiscal-jgb-pension"),
            mkrow(4, 7.0, "Oracle raises $18B"),
            mkrow(5, 6.5, "Nippon Steel iron ore"),
            mkrow(6, 6.2, "Copper tariff"),
            mkrow(7, 6.1, "Yen intervention talk"),
            mkrow(8, 3.0, "Local festival")]
    m = p.top5_message(rows, "2026-09-29", R)
    assert "Title 1" in m and "Title 2" not in m, "the second outlet on the same event must collapse"
    assert "also: NHK Economy" in m
    assert "Title 7" not in m, "six events above threshold, only five shown"
    assert "Title 8" not in m
    assert "japan-fiscal-jgb-pension" in m
    assert "5 of 6 above 6.0" in m, m
    print("  ✓ top-5 shows five distinct events above threshold, collapsed, with thesis tags")


def t_short_day_reports_its_count():
    rows = [mkrow(1, 8.0, "A"), mkrow(2, 7.0, "B"), mkrow(3, 4.0, "C")]
    m = p.top5_message(rows, "2026-09-29", R)
    assert "2 above 6.0 today" in m and "Title 3" not in m, m
    print("  ✓ a short day says '2 above 6.0 today' rather than padding")


def t_quiet_day_message():
    rows = [mkrow(1, 3.0, "A")]
    m = p.top5_message(rows, "2026-09-29", R)
    assert "0 above 6.0 today" in m and "Title 1" not in m
    print("  ✓ nothing above threshold is stated, not padded")


def t_no_rows_never_sends():
    sent = []
    p.send_message = lambda text, token, chat: sent.append(text) or True
    p.send_document = lambda path, caption, token, chat: sent.append(path) or True
    p.fetch_rows = lambda conn, d: []
    rc = p.main(["--date", "2026-09-29", "--db", ":memory:"])
    assert rc == 1 and sent == []
    print("  ✓ zero rows exits 1 and sends nothing")


if __name__ == "__main__":
    print("Running publish-ranked tests...")
    failed = False
    for fn in (t_top5_collapses_events_and_respects_threshold, t_short_day_reports_its_count,
               t_quiet_day_message, t_no_rows_never_sends):
        try:
            fn()
        except Exception as e:
            print("  ✗ %s: %s: %s" % (fn.__name__, type(e).__name__, e))
            failed = True
    print("FAILED" if failed else "All tests passed.")
    sys.exit(1 if failed else 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 test_publish_ranked.py`
Expected: `ModuleNotFoundError: No module named 'publish_ranked'`

- [ ] **Step 3: Implement `publish_ranked.py`**

```python
#!/usr/bin/env python3
"""
publish_ranked.py — send the ranked digest to Telegram.

    python3 publish_ranked.py [--date YYYY-MM-DD] [--db PATH] [--no-telegram]

Two sends: the top-50 HTML as a document (like the two existing digests),
then one message with the day's top 5 EVENTS above threshold — collapsed,
so six outlets on one BoJ decision are one line with "also: …". When fewer
than five clear the bar the message says so ("3 above 6.0 today") rather
than padding with a 3.5 traffic story. Zero rows exits 1 and sends nothing.
"""
import argparse, json, sys, urllib.request
from pathlib import Path

from scoring_rubric import load_rubric, collapse_events
from score_articles import connect, DB_PATH, run_date_jst
from generate_ranked_html import fetch_rows, out_path

OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"
CHAT_ID = "8004116253"
NOT_OPENCLAW = "\n\nnot openclaw"

def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def token():
    return json.loads(OPENCLAW_CONFIG.read_text())["channels"]["telegram"]["botToken"]

def send_message(text, token, chat):
    body = json.dumps({"chat_id": chat, "text": text, "parse_mode": "HTML",
                       "disable_web_page_preview": True}).encode()
    req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token,
                                 data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return bool(json.load(r).get("ok"))

def send_document(path, caption, token, chat):
    boundary = "----carrydash%s" % abs(hash(str(path)))
    data = Path(path).read_bytes()
    parts = []
    for k, v in (("chat_id", chat), ("caption", caption)):
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, k, v)).encode())
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"document\"; filename=\"%s\"\r\n"
                  "Content-Type: text/html\r\n\r\n" % (boundary, Path(path).name)).encode() + data + b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode())
    req = urllib.request.Request("https://api.telegram.org/bot%s/sendDocument" % token, data=b"".join(parts),
                                 headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    with urllib.request.urlopen(req, timeout=60) as r:
        return bool(json.load(r).get("ok"))

def top5_message(rows, run_date, rubric):
    thr = float(rubric["publish_threshold"]); n = int(rubric.get("top_n_telegram", 5))
    groups = [g for g in collapse_events(rows, rubric.get("event_jaccard", 0.6)) if float(g["lead"]["score"]) >= thr]
    shown = groups[:n]
    lines = ["📰 <b>Ranked Japan News — %s JST</b>" % run_date]
    if not groups:
        lines.append("0 above %.1f today — a quiet day." % thr)
    else:
        lines.append("%d of %d above %.1f today" % (len(shown), len(groups), thr) if len(groups) > n
                     else "%d above %.1f today" % (len(groups), thr))
    lines.append("")
    for i, g in enumerate(shown, 1):
        r = g["lead"]
        lines.append("%d. <b>%.1f</b> <a href=\"%s\">%s</a>" % (i, float(r["score"]), esc(r["url"]), esc(r["title_en"])))
        tail = [esc(r["source"])]
        if r.get("thesis_id"):
            tail.append("→ %s%s" % (esc(r["thesis_id"]), "" if r.get("thesis_matched") else "?"))
        if g["also"]:
            tail.append("also: " + ", ".join(sorted({esc(a["source"]) for a in g["also"]})))
        lines.append("   " + " · ".join(tail))
    lines.append("")
    lines.append("Full top 50 attached." if shown else "")
    return "\n".join(l for l in lines if l is not None).rstrip() + NOT_OPENCLAW

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=run_date_jst())
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--no-telegram", action="store_true", help="print, do not send")
    args = ap.parse_args(argv)
    rubric = load_rubric()
    conn = connect(Path(args.db))
    rows = fetch_rows(conn, args.date)
    if not rows:
        print("no scored rows for %s — nothing to publish" % args.date, file=sys.stderr)
        return 1
    msg = top5_message(rows, args.date, rubric)
    html = out_path(args.date)
    if args.no_telegram:
        print(msg); print("(would send %s)" % html)
        return 0
    tok = token()
    ok_doc = html.exists() and send_document(html, "📰 Ranked Japan News — %s JST (top 50)" % args.date + NOT_OPENCLAW, tok, CHAT_ID)
    ok_msg = send_message(msg, tok, CHAT_ID)
    print("telegram: document %s · message %s" % ("OK" if ok_doc else "FAIL/missing", "OK" if ok_msg else "FAIL"))
    return 0 if (ok_doc and ok_msg) else 1

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 test_publish_ranked.py`
Expected: 4 `✓`, `All tests passed.`

- [ ] **Step 5: Wire into `run.sh`**

Edit `japan_news_scraper/run.sh`. After the line
`run_strict  "translate_minimax.py"    python3 translate_minimax.py`
add:
```bash
# 3b. Relevance scoring → carry.db news_scores. Non-fatal: the two existing
#     digests must ship even if scoring fails. Exits 1 on ZERO scored.
run_scraper "score_articles.py"       python3 score_articles.py
run_scraper "generate_ranked_html.py" python3 generate_ranked_html.py --date "$DATE_JST"
```
After the line
`run_strict  "send_digests.sh" /home/blablom/bin/send_digests.sh "$DATE_JST"`
add:
```bash
# 6b. Ranked top-50 document + top-5 events message. Non-fatal.
run_scraper "publish_ranked.py"       python3 publish_ranked.py --date "$DATE_JST"
```

- [ ] **Step 6: Dry-run the three steps by hand against the live archive**

Run:
```bash
cd ~/.openclaw/workspace/japan_news_scraper
python3 score_articles.py                       # real write to carry.db
python3 generate_ranked_html.py                 # writes data/reports/daily_ranked_<today>.html
python3 publish_ranked.py --no-telegram         # prints the message, sends nothing
```
Expected: `stored N rows`, `wrote … (50 rows, … bytes)`, then the top-5 text. Open the HTML in a browser (`xdg-open` or copy over Tailscale) and read the ranking before Step 7.

- [ ] **Step 7: Send once, for real, then commit**

Run: `python3 publish_ranked.py`
Expected: `telegram: document OK · message OK`, and both arrive in Telegram.

```bash
git add publish_ranked.py test_publish_ranked.py run.sh
git commit -m "ranked digest: Telegram top-50 document + top-5 events message; wired into run.sh (non-fatal)"
```

- [ ] **Step 8: Update `CLAUDE.md` pipeline steps**

In `japan_news_scraper/CLAUDE.md`, in "## Pipeline steps", after item 3 add:
```
3b. `score_articles.py` — relevance scoring (six axes + thesis link) → `news_scores` in `apps/carry-dash/server/carry.db`. Non-fatal. Exits 1 on zero scored. Then `generate_ranked_html.py` → `data/reports/daily_ranked_DATE.html` (top 50). Rubric: `rubric.json`. Spec: `docs/specs/2026-09-28-news-relevance-scoring-design.md`.
```
and after item 6:
```
6b. `publish_ranked.py DATE` — Telegram: the top-50 document + one top-5-events message. Non-fatal.
```
Then:
```bash
git add CLAUDE.md
git commit -m "docs: ranked-digest steps in the pipeline list"
```

---

### Task 7: carry-dash API

**Files:**
- Modify: `apps/carry-dash/server/db.js` (after `getKevRecent`, ~line 371; export list at the bottom)
- Modify: `apps/carry-dash/server/index.js` (after the `/api/kev/recent` route, ~line 946)
- Modify: `apps/carry-dash/client/src/api.js` (after `getKev`, ~line 93)
- Test: `apps/carry-dash/server/test_news_api.js`

**Interfaces:**
- Consumes: the `news_scores` table as created by Task 3's `SCHEMA` (column names exact).
- Produces:
  - `db.newsTableExists() -> bool`
  - `db.getNewsRuns() -> [{run_date, n, above}]` (newest first; `above` counts score ≥ 6.0)
  - `db.getNewsScored({ run_date, min = 0, axis = null, collapse = false, limit = 300 }) -> rows | groups`
  - routes `GET /api/news/runs`, `GET /api/news/scored?run_date=&min=&axis=&collapse=1&limit=`
  - `api.getNewsRuns()`, `api.getNewsScored({run_date, min, axis, collapse})`

- [ ] **Step 1: Write the failing test**

`apps/carry-dash/server/test_news_api.js`:
```js
// test_news_api.js — the read side of news_scores.
//   cd apps/carry-dash/server && node test_news_api.js
// The table is created and written by japan_news_scraper/score_articles.py;
// this side is read-only. Collapse here is exact normalised-event match
// (the Python side, which builds the digest, also tolerates wording drift).
const assert = require("assert");
const Database = require("better-sqlite3");
const path = require("path"), os = require("os"), fs = require("fs");

const tmp = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "news-")), "t.db");
process.env.CARRY_DB_PATH = tmp;                 // db.js honours this for tests
const db = require("./db.js");

const SCHEMA = `CREATE TABLE news_scores (
  url TEXT PRIMARY KEY, run_date TEXT NOT NULL, published_at TEXT, source TEXT NOT NULL, title_en TEXT NOT NULL, event TEXT,
  ax_rates REAL, ax_macro REAL, ax_commodities REAL, ax_credit REAL, ax_politics REAL, ax_corporate REAL,
  thesis_id TEXT, thesis_strength INTEGER, thesis_matched INTEGER, score REAL NOT NULL, reason TEXT,
  rubric_version TEXT NOT NULL, theses_hash TEXT NOT NULL, scored_at_utc TEXT NOT NULL)`;

function seed(rows) {
  const d = new Database(tmp); d.exec(SCHEMA);
  const ins = d.prepare(`INSERT INTO news_scores (url,run_date,source,title_en,event,ax_rates,ax_macro,ax_commodities,ax_credit,ax_politics,ax_corporate,thesis_id,thesis_strength,thesis_matched,score,reason,rubric_version,theses_hash,scored_at_utc)
    VALUES (@url,@run_date,@source,@title_en,@event,@ax_rates,0,0,0,0,0,@thesis_id,0,0,@score,'',"v","h","t")`);
  for (const r of rows) ins.run({ thesis_id: null, ax_rates: r.score, ...r });
  d.close();
}

const tests = {
  "newsTableExists is false before the scorer has ever run"() {
    assert.strictEqual(db.newsTableExists(), false);
  },
  "runs are newest first with counts"() {
    seed([
      { url: "u1", run_date: "2026-09-29", source: "Nikkei", title_en: "A", event: "BoJ hike", score: 9 },
      { url: "u2", run_date: "2026-09-29", source: "NHK",    title_en: "B", event: "boj hike",  score: 8 },
      { url: "u3", run_date: "2026-09-28", source: "Asahi",  title_en: "C", event: "Copper",   score: 3 },
    ]);
    const runs = db.getNewsRuns();
    assert.deepStrictEqual(runs, [{ run_date: "2026-09-29", n: 2, above: 2 }, { run_date: "2026-09-28", n: 1, above: 0 }]);
  },
  "scored rows are ranked and filterable"() {
    const rows = db.getNewsScored({ run_date: "2026-09-29" });
    assert.deepStrictEqual(rows.map(r => r.url), ["u1", "u2"]);
    assert.deepStrictEqual(db.getNewsScored({ run_date: "2026-09-29", min: 8.5 }).map(r => r.url), ["u1"]);
    assert.deepStrictEqual(db.getNewsScored({ run_date: "2026-09-28", axis: "rates" }).map(r => r.url), ["u3"]);
    assert.deepStrictEqual(db.getNewsScored({ run_date: "2026-09-28", axis: "macro" }), []);
  },
  "collapse groups by normalised event"() {
    const g = db.getNewsScored({ run_date: "2026-09-29", collapse: true });
    assert.strictEqual(g.length, 1);
    assert.strictEqual(g[0].lead.url, "u1");
    assert.deepStrictEqual(g[0].also.map(a => a.source), ["NHK"]);
  },
};

let failed = false;
console.log("Running news-api tests...");
for (const [name, fn] of Object.entries(tests)) {
  try { fn(); console.log(`  ✓ ${name}`); }
  catch (e) { console.log(`  ✗ ${name}: ${e.message}`); failed = true; }
}
console.log(failed ? "FAILED" : "All tests passed.");
process.exit(failed ? 1 : 0);
```

- [ ] **Step 2: Check how `db.js` locates the database, then run the test**

Run: `cd ~/.openclaw/workspace/apps/carry-dash/server && sed -n '1,12p' db.js && node test_news_api.js`
Expected: the first lines show `getDb()` and a `DB_PATH` constant. The test fails with `db.newsTableExists is not a function`. **If `db.js` does not read `CARRY_DB_PATH`, add it in Step 3** — `const DB_PATH = process.env.CARRY_DB_PATH || path.join(__dirname, "carry.db");` — keeping the default identical to today.

- [ ] **Step 3: Implement in `db.js`**

After `getKevRecent` add:
```js
// --- news_scores (written by japan_news_scraper/score_articles.py; read-only here) ---

const NEWS_AXES = ["rates", "macro", "commodities", "credit", "politics", "corporate"];

function newsTableExists() {
  return !!getDb().prepare(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='news_scores'"
  ).get();
}

function getNewsRuns() {
  if (!newsTableExists()) return [];
  return getDb().prepare(`
    SELECT run_date, COUNT(*) AS n, SUM(score >= 6.0) AS above
      FROM news_scores GROUP BY run_date ORDER BY run_date DESC`).all()
    .map(r => ({ run_date: r.run_date, n: r.n, above: r.above || 0 }));
}

// Exact normalised-event match. The Python side that builds the digest also
// tolerates wording drift (token-Jaccard); the tab keeps to the cheap rule and
// shows what was collapsed, so a missed grouping is visible rather than hidden.
function newsEventKey(s) {
  return String(s || "").toLowerCase().replace(/[^\w\s぀-ヿ一-龯]+/g, " ").trim().replace(/\s+/g, " ");
}

function getNewsScored({ run_date, min = 0, axis = null, collapse = false, limit = 300 } = {}) {
  if (!newsTableExists() || !run_date) return [];
  const where = ["run_date = ?", "score >= ?"];
  const args = [run_date, Number(min) || 0];
  if (axis && NEWS_AXES.includes(axis)) { where.push(`ax_${axis} >= 3`); }
  const rows = getDb().prepare(`
    SELECT * FROM news_scores WHERE ${where.join(" AND ")}
     ORDER BY score DESC, (ax_rates+ax_macro+ax_commodities+ax_credit+ax_politics+ax_corporate) DESC
     LIMIT ?`).all(...args, Math.min(Number(limit) || 300, 1000));
  if (!collapse) return rows;
  const groups = [], byKey = new Map();
  for (const r of rows) {
    const k = newsEventKey(r.event || r.title_en);
    if (byKey.has(k)) byKey.get(k).also.push(r);
    else { const g = { lead: r, also: [] }; byKey.set(k, g); groups.push(g); }
  }
  return groups;
}
```
and add `newsTableExists, getNewsRuns, getNewsScored,` to `module.exports`.

- [ ] **Step 4: Run to verify it passes**

Run: `node test_news_api.js`
Expected: 4 `✓`, `All tests passed.`

- [ ] **Step 5: Routes in `index.js`** (after `/api/kev/recent`):

```js
app.get("/api/news/runs", (req, res) => {
  try { res.json({ runs: db.getNewsRuns() }); }
  catch (e) { res.status(500).json({ error: e.message }); }
});
app.get("/api/news/scored", (req, res) => {
  try {
    const runs = db.getNewsRuns();
    const run_date = req.query.run_date || (runs[0] && runs[0].run_date) || null;
    res.json({
      run_date,
      min: Number(req.query.min) || 0,
      axis: req.query.axis || null,
      collapsed: req.query.collapse === "1",
      rows: db.getNewsScored({ run_date, min: req.query.min, axis: req.query.axis,
                               collapse: req.query.collapse === "1", limit: req.query.limit }),
    });
  } catch (e) { res.status(500).json({ error: e.message }); }
});
```

- [ ] **Step 6: Client methods in `api.js`** (after `getKev`):

```js
  getNewsRuns: () => get(`/news/runs`),
  getNewsScored: ({ run_date, min = 0, axis = "", collapse = false } = {}) =>
    get(`/news/scored?run_date=${encodeURIComponent(run_date || "")}&min=${min}` +
        `${axis ? `&axis=${axis}` : ""}${collapse ? "&collapse=1" : ""}`),
```

- [ ] **Step 7: Restart and verify the routes**

Run:
```bash
sudo systemctl restart carry-dash && sleep 6
curl -s localhost:3456/api/news/runs | head -c 300; echo
curl -s "localhost:3456/api/news/scored?collapse=1" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['run_date'], len(d['rows']), 'groups'); print(d['rows'][0]['lead']['title_en'] if d['rows'] else '(none)')"
```
Expected: a runs list containing today's date, and the top group's title.

- [ ] **Step 8: Commit**

```bash
cd ~/.openclaw/workspace/apps/carry-dash
git add server/db.js server/index.js server/test_news_api.js client/src/api.js
git commit -m "news: read-only API over news_scores — runs + ranked/collapsed rows"
```

---

### Task 8: News tab

**Files:**
- Create: `apps/carry-dash/client/src/News.jsx`
- Modify: `apps/carry-dash/client/src/Nav.jsx:22` (add entry after `kev`)
- Modify: `apps/carry-dash/client/src/App.jsx:9` (import) and `:132` (render)

**Interfaces:**
- Consumes: `api.getNewsRuns()`, `api.getNewsScored(...)` from Task 7.

- [ ] **Step 1: Create `News.jsx`**

```jsx
// News.jsx — the ranked Japan-news feed (news_scores, written by the
// japan_news_scraper pipeline; read-only here). Ranked list with score,
// axes, thesis link and source; collapse toggle groups outlets on one event.
import { useState, useEffect, useCallback } from "react";
import { api } from "./api.js";

// House tokens (match Kev.jsx / Canary.jsx).
const INK = "#f8fafc", INK_2 = "#cbd5e1", MUTED = "#64748b", DIM = "#475569";
const SURFACE = "#111827", BORDER = "#1e293b", ACCENT = "#f59e0b", WARNING = "#eab308", CRITICAL = "#ef4444";
const cardS = { background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: 8, padding: 14 };
const lS = { fontSize: 9, color: MUTED, letterSpacing: 1, textTransform: "uppercase", marginBottom: 3 };
const AXES = ["rates", "macro", "commodities", "credit", "politics", "corporate"];

function Stat({ label, value, sub, color = INK }) {
  return (
    <div style={{ ...cardS, flex: "1 1 150px", minWidth: 140 }}>
      <div style={lS}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color, lineHeight: 1.1 }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: MUTED, marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

const scoreColor = (s) => (s >= 9 ? ACCENT : s >= 6 ? INK : MUTED);

function Chips({ r }) {
  const parts = AXES.map(a => [a, Number(r[`ax_${a}`] || 0)]).filter(([, v]) => v >= 3).sort((x, y) => y[1] - x[1]).slice(0, 3);
  return (
    <span>
      {parts.map(([a, v]) => (
        <span key={a} style={{ fontSize: 10, background: "#1f2937", color: INK_2, borderRadius: 999, padding: "1px 8px", marginRight: 4 }}>{a} {v}</span>
      ))}
      {r.thesis_id && (
        <span title={r.thesis_matched ? "ledger keyword matched" : "model-proposed — no ledger keyword; worth +0.5 at most"}
              style={{ fontSize: 10, color: r.thesis_matched ? "#fbbf24" : WARNING, background: "rgba(245,158,11,0.12)",
                       border: `1px ${r.thesis_matched ? "solid" : "dashed"} #78350f`, borderRadius: 999, padding: "1px 8px" }}>
          → {r.thesis_id} · {r.thesis_strength}{r.thesis_matched ? "" : " ?"}
        </span>
      )}
    </span>
  );
}

function Row({ r, indent = false }) {
  return (
    <div style={{ ...cardS, padding: "10px 12px", marginBottom: 6, marginLeft: indent ? 22 : 0,
                  borderLeft: indent ? `3px solid ${DIM}` : `1px solid ${BORDER}` }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
        <div style={{ fontWeight: 700, fontSize: 16, minWidth: 44, color: scoreColor(r.score) }}>{Number(r.score).toFixed(1)}</div>
        <a href={r.url} target="_blank" rel="noreferrer" style={{ color: INK, textDecoration: "none", fontWeight: 600 }}>{r.title_en}</a>
      </div>
      <div style={{ fontSize: 11, color: MUTED, marginTop: 2 }}>
        {r.source}{r.published_at ? ` · ${String(r.published_at).slice(0, 16).replace("T", " ")}` : ""}{indent ? " · same story" : ""}
      </div>
      <div style={{ marginTop: 6 }}><Chips r={r} /></div>
      {r.reason && <div style={{ fontSize: 12, color: INK_2, marginTop: 4 }} title={`rubric ${r.rubric_version}`}>{r.reason}</div>}
    </div>
  );
}

export default function News() {
  const [runs, setRuns] = useState([]);
  const [runDate, setRunDate] = useState("");
  const [min, setMin] = useState(6);
  const [axis, setAxis] = useState("");
  const [collapse, setCollapse] = useState(true);
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    api.getNewsRuns().then(d => { setRuns(d.runs || []); if (!runDate && d.runs?.[0]) setRunDate(d.runs[0].run_date); })
      .catch(e => setErr(String(e.message || e)));
  }, []);                                                         // eslint-disable-line

  const load = useCallback(() => {
    if (!runDate) return;
    setErr(null);
    try {
      api.getNewsScored({ run_date: runDate, min, axis, collapse }).then(setData).catch(e => setErr(String(e.message || e)));
    } catch (e) { setErr(`client error: ${e.message || String(e)}`); }
  }, [runDate, min, axis, collapse]);
  useEffect(() => { load(); }, [load]);

  const run = runs.find(r => r.run_date === runDate);
  const rows = data?.rows || [];
  const btn = (on) => ({ background: on ? ACCENT : "transparent", color: on ? "#0a0e17" : INK_2,
                         border: `1px solid ${on ? ACCENT : BORDER}`, borderRadius: 6, padding: "4px 10px", fontSize: 11, cursor: "pointer" });

  return (
    <div style={{ padding: "18px 20px 40px", color: INK_2 }}>
      <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 10, letterSpacing: 4, color: ACCENT, textTransform: "uppercase", marginBottom: 3 }}>Ranked news</div>
        <div style={{ fontSize: 20, fontWeight: 700, color: INK }}>Japan news, by relevance</div>
        <div style={{ fontSize: 10, color: MUTED, marginTop: 3 }}>
          Six coverage axes + a link to the thesis ledger. Scored once a day by <code style={{ color: DIM }}>score_articles.py</code> — read-only here.
          A dashed <span style={{ color: WARNING }}>?</span> tag is a model-proposed link with no ledger keyword.
        </div>
      </div>

      {err && <div style={{ ...cardS, borderColor: CRITICAL, color: CRITICAL, fontSize: 12, marginBottom: 14 }}>Failed to load: {err}</div>}

      {runs.length === 0 && !err && (
        <div style={{ ...cardS, fontSize: 12, marginBottom: 14 }}>No scored runs yet — the pipeline writes <code>news_scores</code> at 05:00 JST.</div>
      )}

      {run && (
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 14 }}>
          <Stat label="Run" value={run.run_date} sub="JST digest day" />
          <Stat label="Scored" value={run.n} sub="after dedup" />
          <Stat label="Above 6.0" value={run.above} color={run.above > 0 ? INK : WARNING} sub={run.above === 0 ? "quiet day" : "publish threshold"} />
          <Stat label="Shown" value={rows.length} sub={collapse ? "events (collapsed)" : "articles"} />
        </div>
      )}

      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10, flexWrap: "wrap" }}>
        <select value={runDate} onChange={e => setRunDate(e.target.value)} style={{ ...btn(false), padding: "4px 8px" }}>
          {runs.map(r => <option key={r.run_date} value={r.run_date}>{r.run_date} ({r.n})</option>)}
        </select>
        <button style={btn(!axis)} onClick={() => setAxis("")}>all</button>
        {AXES.map(a => <button key={a} style={btn(axis === a)} onClick={() => setAxis(axis === a ? "" : a)}>{a}</button>)}
        <span style={{ fontSize: 11, color: MUTED, marginLeft: 6 }}>min</span>
        <input type="number" step="0.5" min="0" max="12.5" value={min} onChange={e => setMin(Number(e.target.value))}
               style={{ ...btn(false), width: 64, padding: "3px 6px" }} />
        <button style={btn(collapse)} onClick={() => setCollapse(!collapse)}>{collapse ? "collapsed" : "all articles"}</button>
      </div>

      {rows.map((x, i) => collapse
        ? (<div key={x.lead.url}><Row r={x.lead} />{x.also.map(a => <Row key={a.url} r={a} indent />)}</div>)
        : <Row key={x.url} r={x} />)}
    </div>
  );
}
```

- [ ] **Step 2: Register the tab**

`Nav.jsx` line 22 — after `{ id: "kev", label: "KEV Watch" },` add:
```js
    { id: "news",        label: "News" },
```
`App.jsx` — after `import Kev from "./Kev.jsx";` add `import News from "./News.jsx";`, and after `{tab === "kev" && <Kev />}` add:
```jsx
      {tab === "news"        && <News />}
```

- [ ] **Step 3: Build and check**

Run: `cd ~/.openclaw/workspace/apps/carry-dash/client && npm run build 2>&1 | tail -3`
Expected: `✓ built in …s`. Then open `https://openclawsandbox.tail1e2644.ts.net/carry`, click **News**: today's run in the picker, a ranked list, the collapse toggle changes "Shown" from events to articles. Hard-refresh if the tab is missing (nginx index.html caching, known).

- [ ] **Step 4: Commit**

```bash
cd ~/.openclaw/workspace/apps/carry-dash
git add client/src/News.jsx client/src/Nav.jsx client/src/App.jsx
git commit -m "news: ranked-feed tab — run picker, axis/min filters, event collapse, model-proposed links marked"
```

---

### Task 9: Docs and hand-off

**Files:**
- Modify: `apps/carry-dash/CLAUDE.md` (a "## Ranked news (News tab)" section before "## Current state")
- Modify: `japan_news_scraper/CLAUDE.md` ("Paths cheatsheet" entries)

- [ ] **Step 1: carry-dash CLAUDE.md**

Insert before `## Current state`:
```
## Ranked news (News tab)

Read-only view over `news_scores`, which `japan_news_scraper/score_articles.py`
writes once a day at 05:00 JST (WAL; the scorer owns the table). Spec:
`japan_news_scraper/docs/specs/2026-09-28-news-relevance-scoring-design.md`.
Six coverage axes + a link to `thesis_ledger/theses.json`; `score = best_axis +
bonus`, no cap; a dashed `?` on a thesis tag means the model proposed the link
with no ledger keyword (worth +0.5 at most). Routes `GET /api/news/runs`,
`GET /api/news/scored?run_date=&min=&axis=&collapse=1`. Collapse here is exact
normalised-event match; the Python digest side also tolerates wording drift.
`server/test_news_api.js` covers the helpers against a temp DB
(`CARRY_DB_PATH`). Tune the rubric in `japan_news_scraper/rubric.json`, never here.
```

- [ ] **Step 2: japan_news_scraper CLAUDE.md** — add to "Paths cheatsheet":
```
- Relevance scoring: `score_articles.py` (rubric `rubric.json`, maths `scoring_rubric.py`, ledger link `thesis_match.py`) → `apps/carry-dash/server/carry.db:news_scores`
- Ranked outputs: `generate_ranked_html.py` → `data/reports/daily_ranked_DATE.html`; `publish_ranked.py` → Telegram
- Golden set: `golden/golden_set.json` (`test_scoring_golden.py`)
```

- [ ] **Step 3: Run every test file once more, then commit both repos**

```bash
cd ~/.openclaw/workspace/japan_news_scraper && for t in test_scoring_rubric.py test_thesis_match.py test_score_articles.py test_generate_ranked_html.py test_publish_ranked.py test_scoring_golden.py test_translate_parallel.py; do printf "%-32s " $t; python3 $t | tail -1; done
cd ~/.openclaw/workspace/apps/carry-dash/server && node test_news_api.js | tail -1 && node test_stir_current.js | tail -1
```
Expected: every line `All tests passed.` (golden may print `skipped` if the archive rolled).
```bash
cd ~/.openclaw/workspace/japan_news_scraper && git add CLAUDE.md && git commit -m "docs: ranked-news paths" && git push
cd ~/.openclaw/workspace/apps/carry-dash && git add CLAUDE.md && git commit -m "docs: News tab" && git push
```

- [ ] **Step 4: Watch the first scheduled run**

Tomorrow 05:00 JST. Check:
```bash
journalctl --user -u japan-news-pipeline.service --since "today 04:55 JST" --no-pager | grep -E "score_articles|generate_ranked|publish_ranked|stored|wrote|telegram"
```
Expected: `stored N rows`, `wrote … (50 rows …)`, `telegram: document OK · message OK`, and three documents plus one message in Telegram.
