# Nikkei — Automation

The Nikkei pipeline is no longer agent-driven. This document supersedes any earlier description.

## How Nikkei is scraped now

**Script:** `/home/blablom/bin/nikkei_scraper.py`
**Method:** plain `requests` + paid-subscription cookies. No browser, no Chrome, no Xvfb, no Windows node.
**Output:** JSON files in `data/news_archive/raw/Nikkei_<Section>/YYYYMMDD_HHMMSS_<hash>.json`

Sections covered: `Nikkei_Business`, `Nikkei_Economy`, `Nikkei_Finance`, `Nikkei_International`, `Nikkei_Markets`, `Nikkei_Politics`.

## How it fits in the pipeline

Nikkei is **step 2** of the daily pipeline orchestrated by `~/bin/run_japan_news_pipeline.sh`:

```
non-Nikkei scrape → Nikkei scrape → translate (MiniMax) → 2x generate_html.py → send_digests.sh
```

The Nikkei step retries once on failure and otherwise lets the rest of the pipeline continue.

## JSON shape (input to translator)

```json
{
  "source": "Nikkei Economy",
  "url": "https://www.nikkei.com/article/...",
  "title": "<original Japanese title>",
  "extracted_text": "<original Japanese body>",
  "date": "YYYY-MM-DD",
  "published_at": "ISO with +09:00",
  "scraped_at": "ISO with +09:00",
  "scraper": "requests-nikkei"
}
```

After translation (`translate_minimax.py`) the file gains:
```json
{
  "title_en": "...",
  "translated_text": "...",
  "translated_at": "ISO",
  "translated_by": "MiniMax-M2.7"
}
```

## Manual run

```bash
python3 /home/blablom/bin/nikkei_scraper.py
```

## Login / cookies

If the scraper starts returning empty bodies, cookies have expired. Refresh procedure is in `ref-nikkei-scraper.md`.

## What this replaced (do not bring back)

- ❌ Browser-relay through Windows Chrome via CDP
- ❌ Local Xvfb `:99` Chrome with login automation
- ❌ Agent-driven Google-Translate-URL trick
- ❌ SQLite at `data/nikkei.db`
- ❌ `generate_html_digest.py` (deleted; only `__pycache__` lingers)
- ❌ `deep_translator` / GoogleTranslator

If any of those reappear in cron, scripts, or agent prompts, they're wrong.
