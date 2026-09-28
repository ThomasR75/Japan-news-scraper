# Ranked Japan news — relevance scoring against a live thesis book

**Status:** draft for review · **Date:** 2026-09-28

## Problem

The pipeline scrapes ~140 Japanese articles a day across six outlets, translates
them with MiniMax-M2.5, and publishes two HTML digests **grouped by source and
sorted alphabetically**. Alphabetical by source is not an editorial judgement —
it is the absence of one. A BoJ policy signal and a regional traffic story sit
in the same block, and finding the former means reading past the latter.

Two things make ranking tractable now:

1. Every article is already translated and stored as JSON with `title_en` and
   `translated_text`, so a scorer needs no new fetching.
2. `thesis_ledger/theses.json` is a machine-readable record of nine live
   positions, each with a `keywords` list. That is a far sharper relevance
   signal than a generic topic taxonomy: "macro" scores a Fed-speak piece and a
   GPIF duration-shift piece identically, but the ledger knows only the second
   one touches an open position.

**Discovered while scoping this:** the Nikkei digest has been publishing
**zero articles** since at least 2026-09-21 — byte-identical 2,850-byte empty
shells delivered to Telegram every morning. The scraper's cookies died in
August, `generate_html.py` rendered an empty document, and nothing complained
because "returned no articles" is not a step failure. That is a requirement,
not an anecdote: see §6.

## Non-goals

- **No change to scraping or translation.** The scorer reads the archive the
  digests already read. If it fails, the existing digests ship unchanged.
- **No replacement of the current digests.** Both run in parallel until the
  ranking has earned trust. Comparing what the ranking promoted against what
  was read anyway is the only honest way to evaluate it.
- **No new service.** Scoring is a pipeline step; the UI is a tab in
  carry-dash, which already provides the schedule, database, API, React client
  and Tailscale access a standalone app would have to reinvent.
- **No automated thesis creation.** The scorer reads `theses.json`; it never
  writes to it. Filing a thesis stays a deliberate human act.

## 1. Shape

```
run.sh 05:00 JST
  scrape → translate_minimax → [NEW] score_articles.py → generate_html ×2
                                        │
                                        ├→ carry.db: news_scores
                                        ├→ data/reports/daily_ranked_<date>.html
                                        └→ Telegram: top 5, one message
carry-dash:  GET /api/news/scored  →  News tab
```

`score_articles.py` lives in `japan_news_scraper/`, beside the translate step
whose output it consumes. It writes to `apps/carry-dash/server/carry.db`,
which is the same cross-project reach `tile_prices_updater` already has.

## 2. The rubric

Two components. The split exists because each alone fails in a way the other
covers.

### 2a. Coverage — six axes, 0-10, model-assigned

| axis | what it covers |
|---|---|
| `rates` | central bank policy, STIR, sovereign yields, JGBs |
| `macro` | growth, inflation, FX, capital flows, fiscal |
| `commodities` | the 11 tracked commodities, plus energy and shipping |
| `credit` | issuance, spreads, financing structure — **hyperscaler/AI capex funding lands here** |
| `politics` | only insofar as it moves policy or fiscal outcomes |
| `corporate` | single names, M&A, earnings |

Six axes rather than the four originally suggested: `commodities` and `credit`
were added because both map to standing exposure (the 11-commodity consolidator;
the AI-capex financing theme) and would otherwise be diluted into `macro`.

### 2b. Thesis link — 0-3

Deterministic keyword pre-match from `theses.json[].keywords` produces
*candidates*; the model then rates the strength of the strongest one:

| strength | meaning | bonus |
|---|---|---|
| 0 | no link | 0 |
| 1 weak | same sector, no read-through to the position | +0.5 |
| 2 related | moves a driver of the thesis | +1.5 |
| 3 direct | moves the thesis itself | +2.5 |

Keyword pre-matching is not just an optimisation. It bounds what the model is
asked to consider, so a link must be traceable to a keyword a human wrote in
the ledger — the model cannot invent a connection to a thesis it merely finds
plausible.

Two standing pseudo-theses supplement the nine in the ledger. They live in
`rubric.json` (beside the scorer), not in `theses.json`, because they are
*standing interests* rather than positions with an open date and a
falsification condition:

- **`hyperscaler-financing`** — how AI/datacentre capex is funded: bond raises,
  SPVs, vendor financing, private credit, sale-leasebacks, and the hyperscaler
  names. Links to `tci-holdings` via the MSFT redeployment already tracked there.
- **`commodities-book`** — wheat, sugar, coffee, cocoa, natgas, cattle, copper,
  oil, soy, iron, aluminium.

`rubric.json` holds the axis list, the bonus table, the two pseudo-theses with
their keywords, the publish threshold, and a `version` string. Everything
tunable is in that one file, and `version` is what `news_scores.rubric_version`
records — so "why did this rank differently in October" is answerable.

### 2c. The combination

```
score = min(10, best_axis + link_bonus)
ties break on the sum of all six axes
```

| article | axes | link | score |
|---|---|---|---|
| BoJ eyes Oct hike, core CPI 2.8% | rates 9 | — | 9.0 |
| GPIF shifts ¥2T into super-long JGBs | macro 8 | jgb-pension, direct | 10.0 |
| Oracle raises $18B for datacentre buildout | credit 8 | hyperscaler, direct | 10.0 |
| Nippon Steel iron ore talks stall | commodities 6 | — | 6.0 |
| Dull earnings at a TCI holding | corporate 4 | tci, direct | 6.5 |

`best_axis` rather than a weighted sum of all six, because a story that is a 9
on rates and 0 on everything else is exactly as important as its rates score —
averaging would punish it for being focused.

**The load-bearing property:** a thesis link *promotes*, it does not *gate*. A
BoJ hike scores 9 with no thesis attached. The whole risk of thesis-anchored
ranking is that the most important news is the thing the book does not yet
anticipate; additive bonus over a coverage floor is what prevents that.

## 3. Prompt and batching

One MiniMax call per batch of 10 articles, structured JSON out:

```json
{"url": "...", "axes": {"rates": 9, "macro": 7, ...},
 "thesis": {"id": "japan-fiscal-jgb-pension", "strength": 3},
 "reason": "BoJ signalling an October move; direct read-through to the long end"}
```

- **Input is `title_en` + the first 1,200 characters of `translated_text`.** A
  lede carries the news; the tail carries background. ~24k input tokens/day.
- **Temperature 0.2**, matching the translate step.
- A batch that fails to parse is retried once, then split into singles, so one
  malformed article cannot lose the other nine.
- Articles are scored **once**. Re-scoring happens only when `rubric_version`
  changes, so a rubric edit re-ranks history coherently instead of leaving two
  scales mixed in one table.

## 4. Storage

```sql
CREATE TABLE news_scores (
  url            TEXT PRIMARY KEY,
  date_jst       TEXT NOT NULL,   -- published_at in JST, falling back to
                                  -- scraped_at; the digest day, not UTC
  source         TEXT NOT NULL,
  title_en       TEXT NOT NULL,
  ax_rates       REAL, ax_macro     REAL, ax_commodities REAL,
  ax_credit      REAL, ax_politics  REAL, ax_corporate   REAL,
  thesis_id      TEXT,
  thesis_strength INTEGER,
  score          REAL NOT NULL,
  reason         TEXT,
  rubric_version TEXT NOT NULL,
  scored_at_utc  TEXT NOT NULL
);
CREATE INDEX idx_news_scores_date ON news_scores(date_jst, score DESC);
```

`reason` is stored because a ranking nobody can interrogate is a ranking nobody
will trust. When the tab shows an article at 9.1, the model's own sentence for
why is one hover away.

## 5. API and UI

- `GET /api/news/scored?date=&min=&axis=&limit=` — ranked rows for a date, with
  optional axis filter and minimum score.
- `GET /api/news/dates` — which dates have scores, for the tab's date picker.
- `client/src/News.jsx`, registered in `App.jsx` beside the existing tabs.
  Ranked list, score badge, axis chips, thesis tag, source and time; filter
  row for axis and minimum score; date navigation.
- Telegram: the day's **top 5 by score**, as a single message at the end of the
  run. Top-5 rather than everything above a cutoff, because a fixed-size
  message is one a person keeps reading each morning.

**Threshold.** `rubric.json:publish_threshold`, starting at **6.0** — the level
at which an article is either a mid-strength read on an axis that matters or a
weak one with a thesis link. It governs what the ranked digest includes and
what the tab defaults to; the Telegram top-5 ignores it, since five items is
already the limit. Expect to tune this after a week of real output; it is one
number in one file for exactly that reason.

## 6. Failure modes

| Failure | Handling |
|---|---|
| **Zero articles scored** | Refuse to publish, exit non-zero. This is the empty-shell digest, the exact failure that ran unnoticed for weeks. |
| Zero articles *above threshold* | Publish a "quiet day" digest. That is information, not breakage, and conflating the two is how the first case hides. |
| MiniMax unavailable | Step fails non-fatally; the two existing digests ship as they do today. |
| A batch returns malformed JSON | Retry once, then split into singles; only the offending article is lost, and it is named in the log. |
| `theses.json` unreadable | Fail loud. Scoring on coverage alone while silently dropping half the rubric would produce a plausible-looking ranking that is wrong. |
| Unit fails | `OnFailure=` already guards every user unit (`failure-alert.conf`). |

## 7. Testing

`test_scoring_rubric.py`:

- the arithmetic: `best_axis + bonus`, the cap at 10, tie-break on axis sum
- an article with no thesis link still ranks on coverage alone
- a direct link on a low-coverage article promotes it but does not top the list
- keyword pre-matching against the **real** `theses.json`, so a ledger edit that
  breaks the contract fails here rather than silently stopping all linkage
- zero scored articles raises; zero *above threshold* does not

`test_scoring_golden.py`:

- ~15 hand-scored real articles as a regression fixture. Scores are asserted
  within a tolerance band, not exactly — the point is to catch a prompt change
  that quietly re-ranks everything, not to pin model noise.

## 8. Risks

| Risk | Handling |
|---|---|
| The rubric encodes today's interests and silently rots | `rubric_version` is stored per row; the golden set is re-reviewed when it changes |
| Model scores drift between runs on identical input | Temperature 0.2, scores stored once, golden set asserts bands |
| Thesis linkage dominates and narrows the feed | Bonus is additive over a coverage floor; the tab shows unfiltered rank |
| Ranking is trusted before it has earned it | Both digests run in parallel; nothing is replaced in this spec |
| Cross-project DB write couples two repos | `_lib/tile_prices_updater` already writes into carry-dash from the workspace side, so the reach is established; this adds a DB table rather than files. The table is written only by the scorer and read-only everywhere else |

## 9. Order of work

1. **Rubric + scorer + table + tests.** The risky part, and it is independently
   verifiable: run it over the existing 3-day archive and read the output.
2. **Ranked digest + Telegram top 5.** Alongside the existing digests.
3. **carry-dash News tab.**

Step 1 is worth landing and living with for a few days before 2 and 3. If the
ranking is wrong, that is cheapest to discover before anything publishes it.
