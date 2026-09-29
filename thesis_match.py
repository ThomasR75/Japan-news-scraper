"""
thesis_match.py — link articles to the live thesis ledger by keyword.

A strength-2/3 link MUST trace to a keyword a human wrote in theses.json. The
model may propose an unmatched link, but clamp_strength caps it at 1 (+0.5) —
small enough not to distort the ranking, visible enough to show which keyword
the ledger is missing. Matching runs on the Japanese original as well as the
English translation: the ledger's keywords are English and MiniMax may render
GPIF as "Government Pension Investment Fund".
"""
import hashlib, json, os, re
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

def _keyword_hits(k, hay):
    """Whole-word match for ASCII keywords, substring for everything else.

    Seen on the first live dry-run (2026-09-29): the ledger keyword 'euro'
    matched 'European' and linked an AI-workforce piece to the ECB thesis.
    Japanese has no word boundaries, so those keywords keep substring matching.
    """
    k = k.lower()
    if k.isascii():
        # Leading boundary always. A trailing boundary only for short keywords:
        # 'euro' must not match 'European', but the ledger writes longer
        # keywords as stems ('e-invoic', 'fsa licen') that must match
        # 'e-invoicing' and 'FSA licensed'. Whole-word on both sides silently
        # killed those (review finding, 2026-09-29).
        tail = r"(?![a-z0-9])" if len(k) <= 4 else ""
        return re.search(r"(?<![a-z0-9])" + re.escape(k) + tail, hay) is not None
    return k in hay

def candidates(article, theses):
    hay = _haystack(article)
    hits = []
    for t in theses:
        if any(_keyword_hits(k, hay) for k in t["keywords"] if k):
            hits.append(t["id"])
    return hits

def clamp_strength(strength, matched):
    try:
        s = int(strength)
    except (TypeError, ValueError):
        s = 0
    s = max(0, min(3, s))
    return s if matched else min(s, 1)
