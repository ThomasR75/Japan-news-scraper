# Ranked Japan news — relevance scoring against a live thesis book

**Status:** approved · **Date:** 2026-09-28 · **Revised:** 2026-09-29 after
reassessment (see *Revision notes* at the end)

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

**Discovered while scoping this:** the Nikkei digest had been publishing
**zero articles** since at least 2026-09-21 — byte-identical 2,850-byte empty
shells delivered to Telegram every morning. The scraper's cookies died in
August, `generate_html.py` rendered an empty document, and nothing complained
because "returned no articles" is not a step failure. That is a requirement,
not an anecdote: see §6. (Fixed 2026-09-28; the first non-empty Nikkei digest
in weeks went out 2026-09-29 with 94 articles.)

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
                                        └→ Telegram: top 5 above threshold, one message
carry-dash:  GET /api/news/scored  →  News tab
```

`score_articles.py` lives in `japan_news_scraper/`, beside the translate step
whose output it consumes. It writes to `apps/carry-dash/server/carry.db`.
`_lib/tile_prices_updater` already writes into carry-dash from the workspace
side, so the cross-project reach is established; this adds a DB table rather
than files. The scorer opens the database in **WAL mode with a busy timeout**,
as `ibkr_stir_rates.py` does — the Node process holds `carry.db` open, and a
`database is locked` at 05:00 JST would otherwise fail the step.

### 1a. Deduplication, before scoring

Measured on the live archive on 2026-09-29: **80 of 507** translated articles
(16%) were verbatim duplicates by the digest's own Japanese-shingle dedup —
mostly Nikkei cross-posting one article into two sections, plus some Asahi and
NHK. The scorer runs `generate_html.dedup_articles_by_japanese_shingles` **first**,
so those never reach the model. Free, and it removes the most visible way a
top-5 fills up with the same story.

Shingles only catch near-verbatim text. Six outlets covering the same BoJ
decision in their own words are six distinct articles, all scoring 9. That is
handled after scoring, by **event collapse** (§3, §5): the model emits a short
`event` label per article, and the digest and top-5 show one entry per event
with the other outlets listed beneath it. Batches do not see each other, so the
collapse cannot happen inside the prompt.

## 2. The rubric

Two components. The split exists because each alone fails in a way the other
covers.

### 2a. Coverage — six axes, 0-10, model-assigned

| axis | what it covers |
|---|---|
| `rates` | central bank policy, STIR, sovereign yields, JGBs |
| `macro` | growth, inflation, FX, capital flows, fiscal |
| `commodities` | the 11 tracked commodities (wheat, sugar, coffee, cocoa, natgas, cattle, copper, oil, soy, iron, aluminium), plus energy and shipping |
| `credit` | issuance, spreads, financing structure — **hyperscaler/AI capex funding lands here** |
| `politics` | only insofar as it moves policy or fiscal outcomes |
| `corporate` | single names, M&A, earnings |

Six axes rather than the four originally suggested: `commodities` and `credit`
were added because both map to standing exposure (the 11-commodity consolidator;
the AI-capex financing theme) and would otherwise be diluted into `macro`.

### 2b. Thesis link — 0-3

Keyword pre-match from `theses.json[].keywords` produces *candidates*; the
model then rates the strength of the strongest one:

| strength | meaning | bonus |
|---|---|---|
| 0 | no link | 0 |
| 1 weak | same sector, no read-through to the position | +0.5 |
| 2 related | moves a driver of the thesis | +1.5 |
| 3 direct | moves the thesis itself | +2.5 |

**Pre-matching runs against both the English translation and the Japanese
original** (`title` + `extracted_text`). The ledger's keywords are English
(`gpif`, `mof japan`), and MiniMax may render GPIF as "Government Pension
Investment Fund" — matching only the translation would make the link impossible
by construction on the days it matters most.

The bound is deliberate: a strength-2 or strength-3 link **must** trace to a
keyword a human wrote in the ledger. The model cannot promote an article on a
connection it merely finds plausible. It **may** propose a link with no
keyword match, capped at **strength 1** (+0.5) — too small to distort the
ranking, visible enough in the tab to show which keyword the ledger is missing.

One standing pseudo-thesis supplements the nine in the ledger. It lives in
`rubric.json` (beside the scorer), not in `theses.json`, because it is a
*standing interest* rather than a position with an open date and a
falsification condition:

- **`hyperscaler-financing`** — how AI/datacentre capex is funded: bond raises,
  SPVs, vendor financing, private credit, sale-leasebacks, and the hyperscaler
  names. Links to `tci-holdings` via the MSFT redeployment already tracked
  there. Kept because it is genuinely narrower than the `credit` axis — most
  credit stories will not match it.

A second pseudo-thesis, `commodities-book`, was in the first draft and is
**removed**. It covered exactly what the `commodities` axis covers, so every
commodity story would have collected its axis score plus an automatic direct
+2.5 — iron-ore contract talks at 8.5 against a BoJ hike at 9.0, on every
commodity story, every day. The axis is the coverage; a pseudo-thesis on top
of it is double counting.

`rubric.json` holds the axis list, the bonus table, the pseudo-thesis with its
keywords, the publish threshold, and a `version` string. Everything tunable is
in that one file, and `version` is what `news_scores.rubric_version` records —
so "why did this rank differently in October" is answerable.

### 2c. The combination

```
score = best_axis + link_bonus          (range 0 – 12.5, no cap)
ties break on the sum of all six axes
```

| article | axes | link | score |
|---|---|---|---|
| BoJ eyes Oct hike, core CPI 2.8% | rates 9 | — | 9.0 |
| BoJ hike story that also hits jgb-pension directly | rates 9 | direct | 11.5 |
| GPIF shifts ¥2T into super-long JGBs | macro 8 | jgb-pension, direct | 10.5 |
| Oracle raises $18B for datacentre buildout | credit 8 | hyperscaler, direct | 10.5 |
| Nippon Steel iron ore talks stall | commodities 6 | — | 6.0 |
| Dull earnings at a TCI holding | corporate 4 | tci, direct | 6.5 |

`best_axis` rather than a weighted sum of all six, because a story that is a 9
on rates and 0 on everything else is exactly as important as its rates score —
averaging would punish it for being focused.

**No cap.** The first draft clamped at 10, which made GPIF-direct, Oracle-direct
and a rates-9-direct all read 10.0, resolved by a tie-break nobody looks at. The
scale is ordinal; nothing needs it to end at 10, and the ordering at the top is
the only part anyone reads. The tab shows the raw number.

**The load-bearing property:** a thesis link *promotes*, it does not *gate*. A
BoJ hike scores 9 with no thesis attached. The whole risk of thesis-anchored
ranking is that the most important news is the thing the book does not yet
anticipate; additive bonus over a coverage floor is what prevents that.

## 3. Prompt and batching

One MiniMax call per batch of 10 deduplicated articles, structured JSON out:

```json
{"url": "...",
 "axes": {"rates": 9, "macro": 7, "commodities": 0, "credit": 1, "politics": 3, "corporate": 0},
 "thesis": {"id": "japan-fiscal-jgb-pension", "strength": 3},
 "event": "BoJ signals October hike",
 "reason": "BoJ signalling an October move; direct read-through to the long end"}
```

- **Input is `title_en` + the first 1,200 characters of `translated_text`.** A
  lede carries the news; the tail carries background. ~24k input tokens/day.
- **`event`** is a short normalised label for the underlying story — "BoJ
  signals October hike", not the headline. Articles from different outlets
  about the same event should produce the same label; that is what the
  presentation layer collapses on. Exact-match after lowercasing, then a
  token-Jaccard ≥ 0.6 fallback, so small wording drift still groups.
- **Temperature 0.2**, matching the translate step.
- A batch that fails to parse is retried once, then split into singles, so one
  malformed article cannot lose the other nine.
- Articles are scored **once**. Re-scoring happens only when `rubric_version`
  changes, so a rubric edit re-ranks history coherently instead of leaving two
  scales mixed in one table.
- **The model is not load-bearing.** Scoring nuance — "does this move the
  thesis itself?" — is a judgement call, and MiniMax-M2.5 is chosen for cost
  and because the translate step already trusts it. If the golden set (§7)
  shows it is not up to the judgement, Anthropic and Gemini are both configured
  on this box and still cost cents a day. The prompt and output schema do not
  change; only the endpoint does.

## 4. Storage

```sql
CREATE TABLE news_scores (
  url            TEXT PRIMARY KEY,
  run_date       TEXT NOT NULL,   -- JST date of the pipeline run that first
                                  -- scored it: the digest this article is IN
  published_at   TEXT,            -- outlet's own stamp, display only (see below)
  source         TEXT NOT NULL,
  title_en       TEXT NOT NULL,
  event          TEXT,            -- model's normalised story label, for collapse
  ax_rates       REAL, ax_macro     REAL, ax_commodities REAL,
  ax_credit      REAL, ax_politics  REAL, ax_corporate   REAL,
  thesis_id      TEXT,
  thesis_strength INTEGER,
  thesis_matched INTEGER,         -- 1 if a ledger keyword matched, 0 if model-proposed
  score          REAL NOT NULL,
  reason         TEXT,
  rubric_version TEXT NOT NULL,
  theses_hash    TEXT NOT NULL,   -- sha1 of the sorted live thesis ids at scoring time
  scored_at_utc  TEXT NOT NULL
);
CREATE INDEX idx_news_scores_run ON news_scores(run_date, score DESC);
```

**`run_date`, not `published_at`.** The first draft grouped by the outlet's
published date. Measured on 2026-09-29: **17 articles carried a `published_at`
after today — all Nikkei.** Nikkei stamps evening pieces with the next
morning's paper date (the 朝刊 convention), so grouping by it files tonight's
BoJ story under tomorrow, and late-night articles fall between two digests.
The existing digest has the same flaw; alphabetical-by-source hid it. An
article belongs to the digest of the run that first scored it, and
`published_at` is shown, never grouped on.

**`theses_hash`** records which theses were live when the score was assigned.
Without it, "why didn't this link to X" is unanswerable for any day before X
was filed.

`reason` is stored because a ranking nobody can interrogate is a ranking nobody
will trust. When the tab shows an article at 9.1, the model's own sentence for
why is one hover away.

## 5. API and UI

- `GET /api/news/scored?run_date=&min=&axis=&collapse=1&limit=` — ranked rows
  for a run, with optional axis filter and minimum score. `collapse=1` returns
  one row per `event` with the other outlets' sources and URLs nested under it;
  the default returns every article, because the tab should let you see what
  was collapsed.
- `GET /api/news/runs` — which run dates have scores, for the tab's date picker.
- `client/src/News.jsx`, registered in `App.jsx` beside the existing tabs.
  Ranked list, score badge, axis chips, thesis tag (visually distinct when
  `thesis_matched = 0`, so a model-proposed link never looks like a ledger
  hit), source and time, "also: Asahi, NHK" beneath a collapsed event; filter
  row for axis and minimum score; date navigation.
- **Telegram: the day's top 5 events above threshold**, as a single message at
  the end of the run. Top-5 rather than everything above the cutoff, because a
  fixed-size message is one a person keeps reading each morning. When fewer
  than five clear the threshold the message says so — "3 above 6.0 today" —
  rather than padding with a 3.5 traffic story. A short list is information.

**Threshold.** `rubric.json:publish_threshold`, starting at **6.0** — the level
at which an article is either a mid-strength read on an axis that matters or a
weak one with a thesis link. It governs the ranked digest, the tab's default
filter, and the Telegram top-5. Expect to tune this after a week of real
output; it is one number in one file for exactly that reason.

## 6. Failure modes

| Failure | Handling |
|---|---|
| **Zero articles scored** | Refuse to publish, exit non-zero. This is the empty-shell digest, the exact failure that ran unnoticed for weeks. |
| Zero articles *above threshold* | Publish a "quiet day" digest and a "0 above 6.0 today" Telegram. That is information, not breakage, and conflating the two is how the first case hides. |
| MiniMax unavailable | Step fails non-fatally; the two existing digests ship as they do today. |
| A batch returns malformed JSON | Retry once, then split into singles; only the offending article is lost, and it is named in the log. |
| `theses.json` unreadable | Fail loud. Scoring on coverage alone while silently dropping half the rubric would produce a plausible-looking ranking that is wrong. |
| `carry.db` locked | WAL + busy timeout (30 s), same as the STIR sidecar. If still locked, fail loud — never skip the write and publish anyway. |
| Dedup removes everything | Impossible unless the input was one article; treated as the zero-scored case. |
| Unit fails | `OnFailure=` already guards every user unit (`failure-alert.conf`). |

## 7. Testing

`test_scoring_rubric.py`:

- the arithmetic: `best_axis + bonus`, no cap, tie-break on axis sum
- an article with no thesis link still ranks on coverage alone
- a direct link on a low-coverage article promotes it but does not top the list
- a model-proposed link with no keyword match is clamped to strength 1
- keyword pre-matching against the **real** `theses.json`, in both languages,
  so a ledger edit that breaks the contract fails here rather than silently
  stopping all linkage
- `commodities` axis alone: an iron-ore story with no ledger link scores its
  axis and nothing more (the double-count regression)
- event collapse: three outlets with the same `event` become one entry with two
  "also" sources; two different events stay separate; wording drift within the
  Jaccard band still groups
- `run_date` is the run's date regardless of a next-day `published_at`
- zero scored articles raises; zero *above threshold* does not; a short top-5
  reports its count

`test_scoring_golden.py`:

- ~15 real articles as a regression fixture. **Claude proposes the scores;
  Thomas corrects them** — twenty minutes of review, not an afternoon of
  labelling. Scores are asserted within a tolerance band, not exactly: the
  point is to catch a prompt change that quietly re-ranks everything, not to
  pin model noise.

## 8. Risks

| Risk | Handling |
|---|---|
| The rubric encodes today's interests and silently rots | `rubric_version` per row; `theses_hash` per row; the golden set is re-reviewed when either changes |
| Model scores drift between runs on identical input | Temperature 0.2, scores stored once, golden set asserts bands |
| Thesis linkage dominates and narrows the feed | Bonus is additive over a coverage floor; unmatched links capped at +0.5; the tab shows unfiltered rank |
| One event fills the top-5 | Shingle dedup before scoring; event collapse after it |
| Ranking is trusted before it has earned it | Both digests run in parallel; nothing is replaced in this spec |
| Cross-project DB write couples two repos | Established reach (`tile_prices_updater`); WAL + timeout; the table is written only by the scorer and read-only everywhere else |
| MiniMax cannot make the judgement | Provider is one endpoint string; Anthropic/Gemini are configured and cost cents |

## 9. Order of work

1. **Rubric + scorer + dedup + table + tests.** The risky part, and it is
   independently verifiable: run it over the existing 3-day archive and read
   the output. Golden set is assembled here — proposed scores, corrected.
2. **Ranked digest + event collapse + Telegram top 5.** Alongside the existing
   digests.
3. **carry-dash News tab.**

Step 1 is worth landing and living with for a few days before 2 and 3. If the
ranking is wrong, that is cheapest to discover before anything publishes it.

---

## Revision notes — 2026-09-29

Reassessed after the first draft. Four behaviour changes and six additions,
all approved:

| # | change | why |
|---|---|---|
| 1 | **Dropped `commodities-book`** | Duplicated the `commodities` axis; every commodity story got +2.5 for free — iron ore at 8.5 vs a BoJ hike at 9.0 |
| 2 | **Dedup before scoring + event collapse after** | 16% verbatim duplicates measured; and six outlets on one BoJ decision would have been five top-5 slots |
| 3 | **`run_date`, not `published_at`** | 17 Nikkei articles dated *tomorrow* (朝刊 convention); late pieces fell between digests |
| 4 | **No cap at 10** | Cap collapsed the top of the ranking, the only part anyone reads |
| 5 | Pre-match JP + EN; unmatched links capped at strength 1 | English keywords vs translation phrasing was a false-negative built into the design |
| 6 | Telegram top-5 respects threshold, reports count when short | "Ignores threshold" contradicted the quiet-day logic; it would pad with noise |
| 7 | Golden set: Claude proposes, Thomas corrects | Fifteen hand-labels is an afternoon; fifteen corrections is twenty minutes |
| 8 | WAL + busy timeout on `carry.db` | Node holds the DB open; 05:00 JST would hit `database is locked` |
| 9 | `theses_hash` per row | "Why no link to X" must be answerable for days before X existed |
| 10 | Model explicitly not load-bearing | Judgement scoring may exceed MiniMax; swapping the endpoint is a one-liner |
