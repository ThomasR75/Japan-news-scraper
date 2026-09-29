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
