# ref — Nikkei cookie refresh

Referenced by `NIKKEI_AUTOMATION.md`. Rebuilt 2026-06-25 (the original was lost
in the workspace rm). Follow this when the **Nikkei digest comes out empty**.

## Symptom

`logs/pipeline_<date>.log` shows, for every Nikkei section:

```
[Nikkei Markets] fetching https://www.nikkei.com/markets/
  AUTH CHALLENGE PAGE (2740 bytes) — nikkei_cookies.json likely needs refresh
=== TOTAL: saved=0 skipped=0 ===
...
Nikkei only: 130 → 0 articles
```

→ `digests/nikkei_daily_<date>.html` is an empty ~2.8 KB shell. The non-Nikkei
digest is unaffected. **Cause: the paid-subscription session cookies expired.**
Nikkei sessions last only a few weeks; expect to do this roughly monthly.

## Cookie file

- **Path:** `~/.config/openclaw/nikkei_cookies.json`
- **Format:** a browser cookie export — a JSON *list* of objects with
  `name`, `value`, `domain`, `expirationDate`, `path`, … (the shape produced by
  the **Cookie-Editor** / **EditThisCookie** Chrome extensions).
- **Cookies that actually gate access:** `RNikkeiAuth`, `NID-AUTHENTICATED`,
  `NID-Melissa-SSO`, `__Secure-NID-SMK`, `NID-Serial-Cookie`. The scraper drops
  any cookie whose `expirationDate` is in the past, so once `RNikkeiAuth` /
  `NID-Melissa-SSO` lapse, the request goes out unauthenticated.
  *2026-07-18: sessions issued since ~July 2026 no longer carry
  `NID-Melissa-SSO` at all — a fresh `RNikkeiAuth` (+ the `__Secure-NID-*`
  set) is sufficient; full paid articles verified 3/3. The validator now
  treats the live fetch as authoritative and only warns on the static list.
  A partial export can be merged over the old file (update by cookie name,
  keep old entries for names the new export lacks).*

## Refresh procedure

1. In a normal browser, log into **https://www.nikkei.com** with the paid Nikkei
   ID account (`Thomasr75@gmail.com`). Confirm you can open a full article.
2. With a cookie-export extension (Cookie-Editor → *Export* → *JSON*), export the
   cookies for the `nikkei.com` domain.
3. Overwrite `~/.config/openclaw/nikkei_cookies.json` with that JSON (keep it a
   plain list of cookie objects — don't wrap it).

## Validate before re-running

```bash
python3 ~/.openclaw/workspace/nikkei_scraper/validate_nikkei_cookies.py
```

Expect `RESULT: PASS` (auth cookies fresh + the live `/markets/` fetch returns a
full page, not the ~2.7 KB challenge). If it says FAIL, the export didn't capture
a live session — repeat step 1–3 (make sure you were actually logged in).

## Re-run just the Nikkei half (don't wait for the 05:00 JST timer)

```bash
python3 ~/.openclaw/workspace/nikkei_scraper/nikkei_scraper.py        # scrape 6 sections
cd ~/.openclaw/workspace/japan_news_scraper
python3 translate_minimax.py                                          # translate new JP articles
python3 generate_html.py --nikkei-only                               # rebuild the Nikkei digest
D=$(TZ=Asia/Tokyo date +%F)
cp "data/reports/daily_digest_nikkei_${D}.html" "digests/nikkei_daily_${D}.html"
```

Then check `digests/nikkei_daily_${D}.html` is no longer the empty shell. To also
resend to Telegram: `/home/blablom/bin/send_digests.sh "$D"`. Or just let the next
scheduled `japan-news-pipeline.timer` run (20:00 UTC) pick up the fresh cookies.

## Don't reintroduce (per NIKKEI_AUTOMATION.md)

Browser-relay via Windows Chrome/CDP, Xvfb `:99` login automation, the
Google-Translate-URL trick, `data/nikkei.db`, `deep_translator`. The current path
is plain `requests` + these cookies, nothing else.
