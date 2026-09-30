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
    assert html.count('class="article"') + html.count('class="article same"') == 50, html.count("class='article")
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
    assert "quiet day" in html.lower()
    assert html.count('class="article"') + html.count('class="article same"') == 5
    print("  ✓ nothing above threshold renders a quiet-day banner, still lists what there is")


def t_fetch_rows_spans_the_whole_run_date():
    conn = db_with([mkrow(1, 9.0), mkrow(2, 8.0), mkrow(3, 7.0, run_date="2026-09-28")])
    rows = g.fetch_rows(conn, "2026-09-29")
    assert [r["url"] for r in rows] == ["https://x/1", "https://x/2"]
    print("  ✓ fetch_rows returns every row for the run date, in rank order, and no other day")


def t_stale_rows_are_excluded_from_the_document_and_the_feed():
    """User finding on the first live digest: 23 of the top 50 were published
    before the 27th. fetch_rows is the one read path for the HTML AND the
    Telegram top-5, so the freshness window is applied there."""
    rows = [mkrow(1, 9.0), mkrow(2, 8.0), mkrow(3, 12.0)]
    rows[2]["published_at"] = "2026-06-24T10:00:00+09:00"          # highest score, stale
    conn = db_with(rows)
    got = g.fetch_rows(conn, "2026-09-29", max_age_days=2)
    assert [r["url"] for r in got] == ["https://x/1", "https://x/2"], [r["url"] for r in got]
    html = g.render(got, "2026-09-29", R)
    assert "Title 3" not in html
    print("  ✓ a stale row never reaches the document, however high it scored")


def t_full_text_unfolds_under_the_title():
    # Thomas, 2026-09-30: "instead of going to the website jump down the html
    # for the translated article". The title is a <summary>; the translation
    # sits collapsed under it, paragraphs kept, escaped, source link kept.
    rows = [mkrow(1, 9.0), mkrow(2, 8.0)]
    bodies = {"https://x/1": "First para with <b>angle</b> & ampersand.\n\nSecond para.\nStill second."}
    html = g.render(rows, "2026-09-29", R, bodies=bodies)
    assert "<details" in html and "<summary" in html, "article 1 must be a details/summary block"
    assert "First para with &lt;b&gt;angle&lt;/b&gt; &amp; ampersand." in html, "body escaped"
    assert html.count("<p>") == 2 and "Second para.<br>Still second." in html, "blank line = paragraph, newline = line break"
    assert "class='source' href='https://x/1'" in html, "source link kept at the end of the text"
    i1, i2 = html.index("Title 1"), html.index("Title 2")
    assert i1 < html.index("First para") < i2, "the text sits under its own title"
    print("  ✓ the translation unfolds under the title; escaped, paragraphs kept, source link kept")


def t_missing_text_falls_back_to_the_link():
    rows = [mkrow(1, 9.0)]
    html = g.render(rows, "2026-09-29", R, bodies={})
    assert "<details" not in html
    assert "<a href='https://x/1'>Title 1</a>" in html, "title links to the site as before"
    assert "text not available" in html
    html2 = g.render(rows, "2026-09-29", R)                     # no bodies at all: today's behaviour
    assert "<details" not in html2 and "<a href='https://x/1'>Title 1</a>" in html2
    print("  ✓ no stored translation: the title links out, and says so")


def t_translated_bodies_reads_the_raw_archive_by_url():
    root = Path(tempfile.mkdtemp())
    (root / "Nikkei_Economy").mkdir()
    (root / "Nikkei_Economy" / "a.json").write_text(
        '{"url": "https://x/1", "translated_text": "Hello.", "extracted_text": "\u3053\u3093"}', encoding="utf-8")
    (root / "Nikkei_Economy" / "b.json").write_text('{"url": "https://x/2", "extracted_text": "untranslated"}', encoding="utf-8")
    (root / "Nikkei_Economy" / "junk.json").write_text("{not json", encoding="utf-8")
    got = g.translated_bodies({"https://x/1", "https://x/2", "https://x/3"}, root=root)
    assert got == {"https://x/1": "Hello."}, got
    print("  ✓ translated_bodies maps url → translated_text from the raw archive; untranslated and junk skipped")


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
               t_full_text_unfolds_under_the_title, t_missing_text_falls_back_to_the_link,
               t_translated_bodies_reads_the_raw_archive_by_url,
               t_quiet_day_banner_when_none_above_threshold, t_fetch_rows_spans_the_whole_run_date,
               t_stale_rows_are_excluded_from_the_document_and_the_feed, t_main_writes_atomically):
        try:
            fn()
        except Exception as e:
            print("  ✗ %s: %s: %s" % (fn.__name__, type(e).__name__, e))
            failed = True
    print("FAILED" if failed else "All tests passed.")
    sys.exit(1 if failed else 0)
