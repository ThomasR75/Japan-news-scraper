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

def is_fresh(published_at, run_date, max_age_days):
    """Is an article recent enough for the run's digest?

    Judged against the RUN date, not today: an article may be up to
    max_age_days behind it, and anything ahead of it is fine — Nikkei stamps
    evening pieces with the next morning's paper date. An unknown date is kept
    (the scorer falls back to scraped_at before asking). The first live digest
    carried 23 top-50 articles published before the window: the first run had
    scored the whole 3-day archive, which also holds republished pieces dated
    months back.
    """
    if not published_at:
        return True
    day = str(published_at)[:10]
    if len(day) != 10:
        return True
    import datetime as _dt
    try:
        floor = (_dt.date.fromisoformat(run_date) - _dt.timedelta(days=int(max_age_days))).isoformat()
    except ValueError:
        return True
    return day >= floor

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
