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
        out.append('<div class="%s">' % cls)
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
