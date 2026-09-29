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
        # Unknown URLs (the two calibration anchors) get a default item, as a
        # real model would score them too.
        out = [items_by_url.get(u) or item(u) for u in urls if u not in missing]
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


def t_prompt_carries_absolute_anchors():
    """Found on the first golden run: identical articles re-scored minutes
    later moved 7 of 15 out of band (a farm takeover went 3.0 -> 8.0). The
    scale floated because the prompt had no concrete anchors."""
    p = sa.build_prompt([art(1)], {}, TH_BY_ID, R)
    assert "SCALE" in p and "10 =" in p and "0 =" in p, "anchor scale missing"
    assert "score each article on its own" in p.lower()
    for a in sa.CALIBRATION:
        assert "URL: " + a["url"] in p, "calibration article %s not in prompt" % a["url"]
    print("  ✓ the prompt carries an absolute scale and the two calibration articles")


def t_calibration_anchors_are_stripped_from_results():
    batch = [art(1), art(2)]
    items = {a["url"]: item(a["url"]) for a in batch}
    for a in sa.CALIBRATION:
        items[a["url"]] = item(a["url"])
    got = sa.score_batch(batch, {}, TH_BY_ID, R, call=fake_call(items))
    assert sorted(g["url"] for g in got) == ["https://x/1", "https://x/2"], [g["url"] for g in got]
    print("  ✓ calibration articles are scored with the batch and never returned")


def t_scoring_model_comes_from_the_rubric_not_the_translator():
    """The scorer used to inherit the translate step's MODEL pin. Measured
    2026-09-29: M3 re-scores the same articles with ~half M2.5's drift, so the
    two steps need different models, and the choice belongs with the other
    tunables in rubric.json (SCORING_MODEL in the environment overrides it)."""
    assert R.get("model") == "MiniMax-M3", R.get("model")
    p = sa.build_payload("hi", R)
    assert p["model"] == "MiniMax-M3" and p["messages"][0]["content"] == "hi"
    assert p["temperature"] == sa.TEMPERATURE
    os.environ["SCORING_MODEL"] = "MiniMax-Test"
    try:
        assert sa.build_payload("hi", R)["model"] == "MiniMax-Test"
    finally:
        del os.environ["SCORING_MODEL"]
    print("  ✓ the scoring model is rubric.json's, overridable by SCORING_MODEL, independent of translation")


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


def t_rescoring_keeps_the_original_run_date():
    """Review finding: a rubric bump re-scored the whole 3-day archive INTO
    today's run_date (INSERT OR REPLACE), pulling articles out of the digest
    that first carried them. Spec §4: run_date is the run that FIRST scored it."""
    conn = tmpdb()
    a = art(1)
    old = sa.make_row(a, item(a["url"], axes={**{x: 0 for x in AXES}, "rates": 5}), R, False, "2026-09-27", "h")
    sa.store(conn, [old])
    new = sa.make_row(a, item(a["url"], axes={**{x: 0 for x in AXES}, "rates": 8}), R, False, "2026-09-29", "h2")
    new["rubric_version"] = "later"
    sa.store(conn, [new])
    row = conn.execute("SELECT run_date, score, rubric_version FROM news_scores WHERE url=?", (a["url"],)).fetchone()
    assert row == ("2026-09-27", 8.0, "later"), row
    print("  ✓ a re-score updates the score and version but keeps the first run_date")


def t_zero_scored_with_work_to_do_exits_one():
    """Review finding: `if not rows and not done` was dead in steady state —
    with yesterday's articles already scored, an M3 outage that failed every
    batch logged 'stored 0 rows' and exited 0: no digest, no OnFailure."""
    raw = Path(tempfile.mkdtemp()); (raw / "S").mkdir()
    (raw / "S" / "a.json").write_text(json.dumps(art(1)), encoding="utf-8")
    db = Path(tempfile.mkdtemp()) / "t.db"
    real = sa.call_model
    sa.call_model = lambda prompt, rubric=None: "garbage"      # every call fails, no API
    try:
        rc = sa.main(["--raw", str(raw), "--db", str(db)])
    finally:
        sa.call_model = real
    assert rc == 1, rc
    print("  ✓ work to do and nothing scored exits 1, even with older rows in the table")


def t_deadline_stops_further_batches():
    """Review finding: a stalled endpoint could run the scorer for hours,
    past the unit's 3 h ceiling, and take the two existing digests with it."""
    batch_calls = {"n": 0}
    def slow(prompt):
        batch_calls["n"] += 1
        urls = [l.split("URL: ", 1)[1].strip() for l in prompt.splitlines() if l.startswith("URL: ")]
        return json.dumps({"articles": [item(u) for u in urls]})
    arts_ = [art(i) for i in range(25)]                          # 3 batches
    got = sa.score_all(arts_, {}, TH_BY_ID, R, call=slow, deadline=dt.datetime.now(dt.timezone.utc))
    assert batch_calls["n"] == 1 and len(got) == 10, (batch_calls["n"], len(got))
    print("  ✓ an expired deadline stops after the batch in flight; what was scored is kept")


def t_stale_articles_are_never_sent_to_the_model():
    """User finding: the first digest carried articles from June. Stale
    articles are dropped BEFORE scoring — no tokens, no row — and an article
    with no published_at falls back to scraped_at so it can be judged."""
    raw = Path(tempfile.mkdtemp()); (raw / "S").mkdir()
    today = sa.run_date_jst()
    old = art(1, published_at="2026-06-24T10:00:00+09:00")
    fresh = art(2, published_at=today + "T06:00:00+09:00")
    nodate = art(3); del nodate["published_at"]; nodate["scraped_at"] = today + "T05:01:00+09:00"
    for i, a in enumerate((old, fresh, nodate)):
        (raw / "S" / ("%d.json" % i)).write_text(json.dumps(a), encoding="utf-8")
    seen = []
    def fake(prompt, rubric=None):
        urls = [l.split("URL: ", 1)[1].strip() for l in prompt.splitlines() if l.startswith("URL: ")]
        seen.extend(u for u in urls if not u.startswith("calibration://"))
        return json.dumps({"articles": [item(u) for u in urls]})
    db = Path(tempfile.mkdtemp()) / "t.db"
    real = sa.call_model; sa.call_model = fake
    try:
        rc = sa.main(["--raw", str(raw), "--db", str(db)])
    finally:
        sa.call_model = real
    assert rc == 0 and sorted(seen) == ["https://x/2", "https://x/3"], (rc, seen)
    conn = sqlite3.connect(str(db))
    rows = dict(conn.execute("SELECT url, published_at FROM news_scores").fetchall())
    assert "https://x/1" not in rows and rows["https://x/3"] == today + "T05:01:00+09:00", rows
    print("  ✓ a June article is never scored; a dateless one is judged on scraped_at")


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
               t_prompt_carries_absolute_anchors, t_calibration_anchors_are_stripped_from_results,
               t_scoring_model_comes_from_the_rubric_not_the_translator,
               t_store_is_idempotent_per_rubric_version,
               t_rescoring_keeps_the_original_run_date, t_zero_scored_with_work_to_do_exits_one,
               t_deadline_stops_further_batches, t_stale_articles_are_never_sent_to_the_model,
               t_connect_uses_wal, t_main_refuses_zero_scored):
        try:
            fn()
        except Exception as e:
            print("  ✗ %s: %s: %s" % (fn.__name__, type(e).__name__, e))
            failed = True
    print("FAILED" if failed else "All tests passed.")
    sys.exit(1 if failed else 0)
