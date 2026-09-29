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
            rows[a["url"]] = sa.make_row(a, it, R, bool(it["thesis"]["id"]) and it["thesis"]["id"] in cand.get(a["url"], []), "golden", "h")
    # What the ranking needs is TIERS that hold, not points that repeat.
    # Measured 2026-09-29 over four re-runs of the same 15 articles (MiniMax-M2.5,
    # temperature 0.2 and 0): the top and the zeros were stable in every run;
    # mid-tier articles moved up to 3 points on their axis score, and the +0.5
    # weak-link bonus flipped freely. So: a top must stay a top, a zero must
    # stay a zero, a mid stays within the measured band on its AXIS score
    # (bonus excluded), and only related/direct links (strength >= 2) are
    # pinned. A prompt change that collapses the tiers still fails here.
    bonus = R["bonus"]
    TOP, ZERO, MID_TOL = 7.5, 0.5, 3.0
    bad = []
    for g in gold:
        r = rows.get(g["url"])
        if not r:
            continue
        exp_axis = g["corrected_score"] - bonus[str(int(g.get("corrected_strength", 0)))]
        got_axis = r["score"] - bonus[str(int(r["thesis_strength"]))]
        t = g["title_en"][:50]
        if g["corrected_score"] >= TOP and r["score"] < 5.0:
            bad.append("%s: a top (%.1f) fell to %.1f" % (t, g["corrected_score"], r["score"]))
        elif g["corrected_score"] <= ZERO and r["score"] > 3.0:
            bad.append("%s: a zero (%.1f) rose to %.1f" % (t, g["corrected_score"], r["score"]))
        elif ZERO < g["corrected_score"] < TOP and abs(got_axis - exp_axis) > MID_TOL:
            bad.append("%s: axis %.1f vs expected %.1f (±%.1f)" % (t, got_axis, exp_axis, MID_TOL))
        # Only DIRECT links are pinned. Measured on M3, 2026-09-29: two
        # strength-2 ("related") links appeared when the articles were proposed
        # and vanished on both re-runs, identically — a related link is a
        # judgement the model makes in the context of its batch-mates. A direct
        # link held. +1.5 does not move a tier, so this is not hiding drift.
        if g.get("corrected_thesis") and int(g.get("corrected_strength", 0)) >= 3 \
                and r["thesis_id"] != g["corrected_thesis"]:
            bad.append("%s: thesis %s, expected %s" % (t, r["thesis_id"], g["corrected_thesis"]))
    for b in bad:
        print("  ✗ " + b)
    print("  ✓ %d of %d golden articles within band" % (len(gold) - len(bad), len(gold)))
    print("FAILED" if bad else "All tests passed.")
    return 1 if bad else 0

if __name__ == "__main__":
    print("Running golden-set test...")
    sys.exit(main())
