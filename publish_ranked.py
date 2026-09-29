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

def top5_message(rows, run_date, rubric, note=""):
    thr = float(rubric["publish_threshold"]); n = int(rubric.get("top_n_telegram", 5))
    groups = [g for g in collapse_events(rows, rubric.get("event_jaccard", 0.6)) if float(g["lead"]["score"]) >= thr]
    shown = groups[:n]
    lines = ["📰 <b>Ranked Japan News — %s JST</b>%s" % (run_date, (" · " + esc(note)) if note else "")]
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
    if shown:
        lines.append("")
        lines.append("Full top 50 attached.")
    return "\n".join(lines).rstrip() + NOT_OPENCLAW

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=run_date_jst())
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--no-telegram", action="store_true", help="print, do not send")
    ap.add_argument("--note", default="", help="short label on the message and caption, e.g. 'corrected' for a re-send")
    args = ap.parse_args(argv)
    rubric = load_rubric()
    conn = connect(Path(args.db))
    rows = fetch_rows(conn, args.date, rubric.get("max_age_days"))
    if not rows:
        print("no scored rows for %s — nothing to publish" % args.date, file=sys.stderr)
        return 1
    msg = top5_message(rows, args.date, rubric, note=args.note)
    html = out_path(args.date)
    if args.no_telegram:
        print(msg); print("(would send %s)" % html)
        return 0
    tok = token()
    caption = "📰 Ranked Japan News — %s JST (top 50)%s" % (args.date, (" · " + args.note) if args.note else "")
    ok_doc = html.exists() and send_document(html, caption + NOT_OPENCLAW, tok, CHAT_ID)
    ok_msg = send_message(msg, tok, CHAT_ID)
    print("telegram: document %s · message %s" % ("OK" if ok_doc else "FAIL/missing", "OK" if ok_msg else "FAIL"))
    return 0 if (ok_doc and ok_msg) else 1

if __name__ == "__main__":
    sys.exit(main())
