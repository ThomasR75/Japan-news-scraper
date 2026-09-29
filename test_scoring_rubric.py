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
