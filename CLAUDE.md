# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Entry point

`/home/blablom/bin/run_japan_news_pipeline.sh` — runs the full pipeline.

Scheduled by systemd user timer `japan-news-pipeline.timer` at 05:00 JST daily. Manage with:

```bash
systemctl --user list-timers japan-news-pipeline.timer
systemctl --user status japan-news-pipeline.service
journalctl --user -u japan-news-pipeline.service -n 100
```

Unit files: `~/.config/systemd/user/japan-news-pipeline.{service,timer}`.

## Common commands

Always activate the venv first:

```bash
cd /home/blablom/.openclaw/workspace/japan_news_scraper
source venv/bin/activate
```

Run individual pipeline steps:

```bash
python3 scraper_non_nikkei.py                    # step 1: 18+ non-Nikkei sources
python3 /home/blablom/bin/nikkei_scraper.py      # step 2: 6 Nikkei sections
python3 translate_minimax.py                     # step 3: translate all untranslated articles
python3 generate_html.py --exclude-nikkei        # step 4: non-Nikkei digest
python3 generate_html.py --nikkei-only           # step 5: Nikkei digest
/home/blablom/bin/send_digests.sh DATE           # step 6: Telegram delivery
python3 cleanup_archive.py --days 7              # step 6: delete articles older than 7 days
```

Quick diagnostics:

```bash
# Translation coverage
python3 -c "
import json, glob
files = glob.glob('data/news_archive/raw/*/*.json')
translated = total = 0
for f in files:
    try:
        total += 1
        if json.load(open(f)).get('translated_text'): translated += 1
    except: pass
print(f'{translated}/{total} translated')
"

# Article count in today's digest
grep -c 'class="article"' data/reports/daily_digest_non-nikkei_$(TZ=Asia/Tokyo date +%Y-%m-%d).html
grep -c 'class="article"' digests/nikkei_daily_$(TZ=Asia/Tokyo date +%Y-%m-%d).html
```

## Pipeline steps

1. `scraper_non_nikkei.py` — 18+ non-Nikkei sources (requests / RSS). Sources: 3 Asahi, 3 Mainichi, 3 Sankei, 3 FNN, 3 NHK (all via requests/RSS), plus 5 Yomiuri. Retries once on failure, then continues.
2. `/home/blablom/bin/nikkei_scraper.py` — 6 Nikkei sections (requests + paid-subscription cookies). Retries once, then continues.
3. `translate_minimax.py` — MiniMax translates anything with `extracted_text` missing `translated_text`, or `title` missing `title_en`. Imports `minimax_translate.py`. Failure aborts the pipeline.
4. `python3 generate_html.py --exclude-nikkei` → `data/reports/daily_digest_non-nikkei_DATE.html`
5. `python3 generate_html.py --nikkei-only` → `data/reports/daily_digest_nikkei_DATE.html`, then copied to `digests/nikkei_daily_DATE.html`.
6. `/home/blablom/bin/send_digests.sh DATE` — Telegram delivery to chat `8004116253`.
7. `cleanup_archive.py --days 7` — deletes article JSON files older than 7 days from `data/news_archive/raw/`, removes empty source directories.

Logs: `logs/pipeline_YYYY-MM-DD.log`.

## JSON article shape

Each scraped article is a JSON file under `data/news_archive/raw/<section>/YYYYMMDD_HHMMSS_<hash>.json`. Fields:

- Before translation: `source`, `url`, `title`, `extracted_text`, `date`, `published_at`, `scraped_at`, `scraper`.
- After translation: adds `title_en`, `translated_text`, `translated_at`, `translated_by`.

Nikkei and non-Nikkei share the same field schema (this is an important invariant — don't split them).

## Key implementation details

**Non-Nikkei scraper** (`scraper_non_nikkei.py`) uses three modes internally:
- `REQUESTS_SOURCES` — tuples of `(name, url, link_selector, title_selector, time_selector, body_selector)` for static HTML
- `RSS_SOURCES` — tuples of `(name, feed_url)` for NHK
- `YOMIURI_SOURCES` — tuples of `(name, url)` for Yomiuri (its own logic)
- `LIGHTPANDA_SOURCES` — currently empty (disabled 2026-04-28); was for JS-heavy SPAs; do not escalate back to Lightpanda unless requests fails

**Deduplication** in `generate_html.py` uses Japanese character 8-gram shingles (Hiragana U+3040–U+309F, Katakana U+30A0–U+30FF, Kanji U+4E00–U+9FAF), Jaccard ≥ 0.55. Typically reduces ~900–1000 raw articles to ~550–650.

**Nikkei cookies**: stored at `~/.config/openclaw/nikkei_cookies.json` (JSON array in Cookie-Editor format with `name` and `value` keys). If Nikkei digest is empty, cookies have likely expired — refresh by exporting from a logged-in browser session.

## Canonical docs

- `PIPELINE_CHECKLIST.md` — full pre-send checks, quality thresholds. **Source of truth** — wins over anything else.
- `NIKKEI_AUTOMATION.md` — Nikkei-specific current approach.
- `~/.openclaw/workspace/memory/ref-nikkei-scraper.md` and `ref-non-nikkei-scraper.md` — agent-readable reference notes (include source status table).

## Common failure modes

| Problem | Cause | Fix |
|---|---|---|
| `Files needing translation: 0` but digest empty | Old `EXCLUDE` filter in `translate_minimax.py` | Remove the exclude filter |
| Digest is stale | Pipeline ran before scrapes finished | Re-run `run_japan_news_pipeline.sh` manually |
| Telegram send fails with `MISSING` | One digest file wasn't produced | Check `generate_html.py` step in log |
| Nikkei digest empty | Cookies expired | Refresh `~/.config/openclaw/nikkei_cookies.json` |

## Paths cheatsheet

- Non-Nikkei scraper: `scraper_non_nikkei.py` (this dir)
- Nikkei scraper: `/home/blablom/bin/nikkei_scraper.py`
- Translator: `translate_minimax.py` (uses `minimax_translate.py`)
- Digest builder: `generate_html.py` (this dir)
- Telegram sender: `/home/blablom/bin/send_digests.sh`
- Pipeline entry: `/home/blablom/bin/run_japan_news_pipeline.sh`
- Raw articles: `data/news_archive/raw/<section>/`
- Output digests: `data/reports/daily_digest_{nikkei,non-nikkei}_DATE.html` + `digests/nikkei_daily_DATE.html`
- Logs: `logs/pipeline_DATE.log`
- Nikkei cookies: `~/.config/openclaw/nikkei_cookies.json`

## Deprecated — do not revive

These patterns exist in git history, `.bak` files, and old session logs:

- `deep_translator` / `GoogleTranslator` (replaced by MiniMax)
- `data/nikkei.db` SQLite (replaced by JSON files)
- Xvfb `:99` Chrome for Nikkei (replaced by `requests` + cookies)
- Windows Chrome CDP relay for Nikkei (replaced by `requests` + cookies)
- `generate_html_digest.py` (replaced by `generate_html.py` with `--nikkei-only`)
- "5 chunks in parallel subagents" Nikkei orchestration (replaced by single requests scraper)
- `run_nikkei.sh`, `run_pipeline.py`, `run_digest.sh`, `run_telegram.sh` (quarantined as `.bak`)
- OpenClaw cron jobs `daily-japan-news-html` and `nikkei-daily-local` (disabled; systemd timer replaces them)
