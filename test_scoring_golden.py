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
