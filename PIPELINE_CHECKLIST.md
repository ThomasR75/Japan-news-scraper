# Japan News Digest — Pipeline & Checklist

**Run this before every Telegram send. Nothing goes out unless every item passes.**

---

## The Golden Rules

Two files delivered to Telegram every morning:
1. **Non-Nikkei digest** — `data/reports/daily_digest_non-nikkei_YYYY-MM-DD.html`
2. **Nikkei digest** — `digests/nikkei_daily_YYYY-MM-DD.html` (copied from `data/reports/daily_digest_nikkei_YYYY-MM-DD.html`)

**What "good" looks like:**
- Every article body in English (translated from Japanese)
- No article older than 24 hours
- Zero duplicate articles (Japanese shingle dedup, k=8, Jaccard ≥ 0.55)
- TOC and source blocks sorted A→Z
- Working jump links

---

## Pipeline (current)

**Entry point:** `/home/blablom/bin/run_japan_news_pipeline.sh`

**Scheduled by:** systemd user timer `japan-news-pipeline.timer` at 05:00 JST daily.

**Steps (in order):**
1. `python3 scraper_non_nikkei.py` — 18 non-Nikkei sources (requests / RSS / Lightpanda)
2. `python3 /home/blablom/bin/nikkei_scraper.py` — 6 Nikkei sections (requests + cookies)
3. `python3 /home/blablom/bin/translate_minimax.py` — MiniMax translates everything missing `translated_text` or `title_en`
4. `python3 generate_html.py --exclude-nikkei` — non-Nikkei digest
5. `python3 generate_html.py --nikkei-only` — Nikkei digest, then copied to `digests/nikkei_daily_DATE.html`
6. `/home/blablom/bin/send_digests.sh DATE` — sends both files to Telegram chat 8004116253

Scraper failures retry once and then continue (the digest still goes out with whatever was successfully scraped). Translator and digest steps are not retried — if they fail the pipeline aborts.

**Logs:** `~/.openclaw/workspace/japan_news_scraper/logs/pipeline_YYYY-MM-DD.log`

**Manual run:**
```bash
/home/blablom/bin/run_japan_news_pipeline.sh
```

**Check timer:**
```bash
systemctl --user list-timers japan-news-pipeline.timer
systemctl --user status japan-news-pipeline.service
journalctl --user -u japan-news-pipeline.service -n 100
```

---

## Pre-Send Checklist

### Freshness (24h)
- [ ] Today's date directories exist under `data/news_archive/raw/`
- [ ] Articles in HTML have date within 24h of run
- [ ] Log shows `Latest article date: YYYYMMDD` matching today

### Translation
- [ ] Untranslated articles < 5% of total
- [ ] If higher, MiniMax probably hit errors — check log for `GIVE UP` lines

### Deduplication
- [ ] Japanese shingle dedup ran (look for `Articles after Japanese shingles dedup` in log)

### HTML structure
- [ ] TOC sorted A→Z (case-insensitive)
- [ ] Source block IDs match TOC hrefs
- [ ] Articles within each source sorted A→Z
- [ ] Back-to-top links work

### Telegram
- [ ] Both files exist before `send_digests.sh` runs
- [ ] File size 500KB–3MB normal
- [ ] Caption format: `📰 Japan News Digest — DATE JST (non-Nikkei)` and `📰 Nikkei Daily — DATE JST`

---

## Source list (all currently in scope)

**Non-Nikkei** (18 targets — see `ref-non-nikkei-scraper.md` for live status):
Asahi (Politics, Business, International) · Mainichi (Politics, International, Economy) · Sankei (Politics, Economy, International) · FNN (Politics, Economy, International) · NHK (Politics, International, Economy) · Yomiuri (Politics, Economy, International — currently deferred)

**Nikkei** (6+ sections — see `ref-nikkei-scraper.md`):
Nikkei_Business · Nikkei_Economy · Nikkei_Finance · Nikkei_International · Nikkei_Markets · Nikkei_Politics

---

## Common failure modes

| Problem | Cause | Fix |
|---|---|---|
| Pipeline reports `Files needing translation: 0` but no Nikkei in digest | Old `EXCLUDE = ('Nikkei',)` line in translator | Make sure `translate_minimax.py` doesn't filter Nikkei |
| Today's digest stale | Pipeline ran before scrapes finished, or before translation | Re-run `run_japan_news_pipeline.sh` manually |
| Telegram send fails with "MISSING" | One of the digest files wasn't produced | Check log for the `generate_html.py` step that failed |
| Nikkei digest empty | `nikkei_scraper.py` cookies expired | Refresh cookies (see `ref-nikkei-scraper.md`) |

---

## Quick verification commands

```bash
# Untranslated count (skips malformed JSON)
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

# How many articles ended up in each digest?
grep -c 'class="article"' data/reports/daily_digest_non-nikkei_$(TZ=Asia/Tokyo date +%Y-%m-%d).html
grep -c 'class="article"' digests/nikkei_daily_$(TZ=Asia/Tokyo date +%Y-%m-%d).html
```

---

## Deprecated (do not use)

These scripts are quarantined as `.bak` and must not be reintroduced:
- `run_pipeline.py.bak` — used GoogleTranslator, would overwrite MiniMax output
- `run_digest.sh.bak` — referenced nonexistent `generate_html_digest.py`
- `run_nikkei.sh.bak` — old SQLite/Xvfb agent-driven path; references deleted scripts
- `run_telegram.sh.bak` — hardcoded April 23 date

Any references to `deep_translator`, `GoogleTranslator`, `nikkei.db`, `Xvfb :99`, or `generate_html_digest.py` in this project are obsolete.
