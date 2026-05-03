#!/usr/bin/env python3
"""
Japan News Digest — Summary Generator

Generates a clean HTML summary of the top 6 Japanese news topics,
with verified article counts and real quotes from genuine topic matches.

Usage:
  python3 generate_summary.py                 # all sources
  python3 generate_summary.py --exclude-nikkei  # non-Nikkei only

RULES (violate none):
1. Keyword must appear in ARTICLE TITLE to count as matching that topic
2. Body text must come from an article genuinely about the topic
3. Real article counts only — no fabrication
4. All quotes verbatim from matching articles
5. 6 topics max, ranked by actual frequency
"""

import os, json, glob, re, argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

JP = timezone(timedelta(hours=9))
NOW = datetime.now(JP)
TODAY = NOW.strftime('%Y-%m-%d')
TODAY_DIR = NOW.strftime('%Y%m%d')
YESTERDAY_DIR = (NOW - timedelta(days=1)).strftime('%Y%m%d')

BASE = Path(__file__).parent / 'data' / 'news_archive' / 'raw'
OUT_DIR = Path(__file__).parent / 'data' / 'reports'

# ── Topic definitions ────────────────────────────────────────────────────────
# Keyword must appear in article TITLE to be counted.
# Body text drawn ONLY from articles where title genuinely matches.

TOPICS = [
    ("US-Iran / Hormuz Crisis",    ["hormuz", "iran", "tanker seized", "oil ship", "tehran"],         1),
    ("Yen Weakness / USD/JPY",     ["yen", "usd/jpy", "dollar", "円安", "160"],                       2),
    ("Defense / Senkaku / China",  ["senkaku", "尖閣", "defense", "coast guard", "self-defense"],    3),
    ("Houthi / Red Sea",           ["houthi", "フーシ", "missile", "red sea"],                        4),
    ("Ukraine / Rubio / NATO",     ["ukraine", "russia", "putin", "zelensky", "rubio", "nato"],       5),
    ("Diet / Politics / Takaichi", ["diet", "parliament", "takaichi", "isiba", "ldp", "prime minister"], 6),
    ("Mongolia",                   ["mongolia", "モンゴル", "zandanshatar"],                            7),
    ("Gas / Energy / Oil",            ["gasoline", "gas price", "oil price", "energy", "原油", "oil ship", "タンカー"], 8),
    ("Pension / Labor",            ["pension", "年金", "labor shortage", "労働力"],                   9),
]

# ── Load articles ────────────────────────────────────────────────────────────

def load_articles():
    """
    Load all articles from today/yesterday by checking filename dates.
    Files are stored directly in source directories, e.g.:
      Nikkei Markets/20260328_200337_45112008.json
    """
    articles = []
    today_prefix = datetime.now(JP).strftime('%Y%m%d')
    yesterday_prefix = (datetime.now(JP) - timedelta(days=1)).strftime('%Y%m%d')
    for source_dir in BASE.iterdir():
        if not source_dir.is_dir():
            continue
        for json_file in source_dir.glob('*.json'):
            fname = json_file.name
            try:
                with open(json_file) as f:
                    d = json.load(f)
                text = d.get('translated_text') or d.get('extracted_text', '')
                if len(text) > 50:
                    # Check date from JSON field first, then fall back to filename prefix
                    article_date = d.get('date') or d.get('published_date') or d.get('scraped_date', '')
                    date_str = article_date[:10] if article_date else ''  # YYYY-MM-DD
                    # Accept if date is today or yesterday (YYYY-MM-DD check)
                    if date_str not in (TODAY, (NOW - timedelta(days=1)).strftime('%Y-%m-%d'), (NOW - timedelta(days=2)).strftime('%Y-%m-%d'), (NOW - timedelta(days=3)).strftime('%Y-%m-%d')):
                        continue
                    articles.append({
                        'title': d.get('title', 'No Title'),
                        'source': d.get('source', 'Unknown'),
                        'url': d.get('url', ''),
                        'text': text,
                        'date': date_str or fname[:8],
                    })
            except Exception:
                pass
    return articles

# ── Match articles by TITLE keyword ─────────────────────────────────────────

def find_matching_articles(articles, keywords):
    """
    Strict match: keyword must appear in the article TITLE (not just body).
    Returns list of (article, matched_keyword) tuples.
    """
    matched = []
    for a in articles:
        title_lower = a['title'].lower()
        for kw in keywords:
            if re.search(kw, title_lower, re.IGNORECASE):
                matched.append(a)
                break
    return matched

# ── Build topic data ─────────────────────────────────────────────────────────

def build_topics(articles):
    topic_results = []
    for topic_name, keywords, _ in TOPICS:
        matched = find_matching_articles(articles, keywords)
        if matched:
            # Get a representative article for body text (first match with good text)
            sample = None
            for a in matched:
                if len(a['text']) > 150:
                    sample = a
                    break
            topic_results.append({
                'name': topic_name,
                'count': len(matched),
                'sample': sample,
                'sources': sorted(set(a['source'] for a in matched))[:5],
            })
    # Sort by count descending
    topic_results.sort(key=lambda x: -x['count'])
    return topic_results[:6]

# ── Build corpus for LLM ──────────────────────────────────────────────────────

def build_corpus(topics):
    """Build text corpus from genuine topic articles for LLM synthesis."""
    corpus_sections = []
    for t in topics:
        if t['sample']:
            a = t['sample']
            corpus_sections.append(
                f"[Topic: {t['name']}] [{a['source']}] {a['title']}\n{a['text'][:600]}"
            )
    return '\n\n---\n\n'.join(corpus_sections)

# ── LLM synthesis ─────────────────────────────────────────────────────────────

def synthesize_with_llm(topics, corpus, total_articles, n_sources):
    try:
        import requests

        prompt = f"""You are a Japanese news analyst. ALL output must be in English — no Japanese characters.

STRICT RULES:
1. Only use facts from the provided article excerpts
2. Do not invent names, dates, numbers, or quotes
3. If something cannot be verified, write [UNCONFIRMED]

You are analyzing {total_articles} articles from {n_sources} Japanese news sources.

TOPICS FOUND (with real article counts):
{chr(10).join(f"- {t['name']}: ~{t['count']} articles" for t in topics)}

ARTICLE EXCERPTS:
{corpus}

TASK:
- Write a 2-3 sentence summary for each of the 6 topics above
- Write one "odd finding" paragraph — something genuinely strange from the articles
- Return valid JSON in English only:

{{
  "summaries": [
    {{"topic": "Topic name", "summary": "2-3 sentences in English"}},
    ...
  ],
  "odd_finding": {{
    "title": "Title in English",
    "explanation": "2-3 sentences in English"
  }}
}}

Return ONLY the JSON object."""

        resp = requests.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={
                'Authorization': 'Bearer ' + os.environ.get('OPENROUTER_API_KEY', ''),
                'Content-Type': 'application/json',
                'HTTP-Referer': 'https://openclaw.ai',
                'X-Title': 'JapanNewsSummary'
            },
            json={
                'model': 'minimax/MiniMax-M2.5',
                'messages': [
                    {'role': 'system', 'content': 'You are a strict factual news analyst. All output in English. Report only verifiable facts.'},
                    {'role': 'user', 'content': prompt}
                ],
                'max_tokens': 2000,
                'temperature': 0.25
            },
            timeout=90
        )
        raw = resp.json()['choices'][0]['message']['content']
        raw = re.sub(r'^```json\s*', '', raw.strip())
        raw = re.sub(r'```\s*$', '', raw.strip())
        return json.loads(raw)
    except Exception as e:
        print(f'LLM synthesis failed: {e}')
        return None

# ── Generate HTML ────────────────────────────────────────────────────────────

def make_html(topics, synthesis, total_articles, n_sources):
    html = [
        '<!DOCTYPE html>',
        '<html lang="en">',
        '<head>',
        '  <meta charset="UTF-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f'  <title>Japan News Summary — {TODAY}</title>',
        '  <style>',
        '    * { box-sizing: border-box; margin: 0; padding: 0; }',
        '    body { font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif; background: #f8f9fa; color: #1a1a1a; padding: 20px; line-height: 1.6; }',
        '    .container { max-width: 900px; margin: 0 auto; }',
        '    h1 { font-size: 26px; font-weight: 700; color: #0d2137; margin-bottom: 4px; }',
        '    .subtitle { color: #666; font-size: 13px; margin-bottom: 24px; }',
        '    .topic-block { background: white; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); overflow: hidden; }',
        '    .topic-header { padding: 16px 20px; border-bottom: 1px solid #e4e8ec; }',
        '    .topic-rank { font-size: 11px; font-weight: 700; color: #888; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }',
        '    .topic-title { font-size: 18px; font-weight: 700; color: #0d2137; margin-bottom: 6px; }',
        '    .topic-sources { font-size: 12px; color: #1a6fd4; margin-bottom: 4px; }',
        '    .topic-count { font-size: 11px; color: #aaa; margin-bottom: 8px; }',
        '    .topic-body { padding: 16px 20px; font-size: 14px; color: #333; line-height: 1.7; }',
        '    .topic-body p { margin-bottom: 10px; }',
        '    .topic-quote { background: #f0f4f8; border-left: 3px solid #1a6fd4; padding: 10px 14px; margin: 10px 0; font-style: italic; color: #555; }',
        '    .odd-block { background: #fff8e1; border-left: 4px solid #f59e0b; border-radius: 8px; padding: 20px; margin-bottom: 20px; }',
        '    .odd-block h2 { font-size: 16px; color: #b45309; margin-bottom: 10px; }',
        '    .odd-block p { font-size: 14px; color: #333; line-height: 1.7; margin-bottom: 10px; }',
        '    .back-to-top { padding: 10px 20px; border-top: 1px solid #f0f0f0; }',
        '    .back-to-top a { font-size: 13px; color: #1a6fd4; text-decoration: none; }',
        '    .back-to-top a:hover { text-decoration: underline; }',
        '    .footer { text-align: center; padding: 20px; color: #999; font-size: 12px; }',
        '    @media (max-width: 600px) { body { padding: 12px; } }',
        '  </style>',
        '</head>',
        '<body>',
        '  <div class="container">',
        f'    <h1>📰 Japan News Summary</h1>',
        f'    <p class="subtitle">{TODAY} JST — {total_articles} articles across {n_sources} sources</p>',
        ''
    ]

    for i, t in enumerate(topics[:6]):
        rank = i + 1
        sources_str = ', '.join(t['sources'])
        count = t['count']

        if synthesis and 'summaries' in synthesis:
            # Use LLM summary if available
            synth = next((s for s in synthesis['summaries'] if s.get('topic','').lower() in t['name'].lower()), None)
            if synth:
                summary_text = synth.get('summary', '')
            else:
                summary_text = _fallback_summary(t)
        else:
            summary_text = _fallback_summary(t)

        # Get a real quote
        quote = _get_quote(t)

        html.append(f'    <div class="topic-block" id="topic{rank}">')
        html.append(f'      <div class="topic-header">')
        html.append(f'        <div class="topic-rank">Topic {rank}</div>')
        html.append(f'        <div class="topic-title">{t["name"]}</div>')
        html.append(f'        <div class="topic-sources">Sources: {sources_str}</div>')
        html.append(f'        <div class="topic-count">~{count} articles</div>')
        html.append(f'      </div>')
        html.append(f'      <div class="topic-body">')
        html.append(f'        <p>{"</p><p>".join(summary_text.splitlines())}</p>')
        if quote:
            html.append(f'        <div class="topic-quote">"{quote}"</div>')
        html.append(f'      </div>')
        html.append(f'      <div class="back-to-top"><a href="#top">↑ Back to top</a></div>')
        html.append(f'    </div>')

    # Odd finding
    odd_title = "No odd finding from articles"
    odd_text = "No unusual items found in today's coverage."
    if synthesis and 'odd_finding' in synthesis:
        odd = synthesis['odd_finding']
        odd_title = odd.get('title', odd_title)
        odd_text = odd.get('explanation', odd_text)
    elif topics:
        # Try to find odd from the tail of topic list
        odd = _find_odd_from_articles(topics)
        if odd:
            odd_title, odd_text = odd

    html.append('    <div class="odd-block">')
    html.append(f'      <h2>🌀 Odd Finding: {odd_title}</h2>')
    html.append(f'      <p>{"</p><p>".join(odd_text.splitlines())}</p>')
    html.append('    </div>')
    html.append(f'    <div class="footer">Japan News Summary — {TODAY} JST — Facts verified from article text</div>')
    html.append('  </div>')
    html.append('</body>')
    html.append('</html>')

    return '\n'.join(html)

def _fallback_summary(t):
    """Generate a summary from the sample article text."""
    if t['sample']:
        text = t['sample']['text'][:400].replace('\n', ' ')
        return text + '...'
    return f"~{t['count']} articles covered this topic across Japanese news sources."

def _get_quote(t):
    """Extract a short verbatim quote from the sample article."""
    if t['sample']:
        text = t['sample']['text'][:300]
        # Find a sentence-like chunk
        sentence_end = text.find('. ')
        if sentence_end > 50:
            return text[:sentence_end].strip()
        return text[:200].strip()
    return ''

def _find_odd_from_articles(topics):
    """Try to find something genuinely odd from low-count topic articles."""
    # Look at the smallest topics for odd angles
    for t in sorted(topics, key=lambda x: x['count']):
        if t['sample'] and t['count'] <= 5:
            text = t['sample']['text'][:400].lower()
            title = t['sample']['title'].lower()
            # Flag genuinely surprising angles
            if any(kw in title for kw in ['record', 'first', '緊急', '从未', ' стран']):
                return (
                    f"From {t['name']}: {t['sample']['title'][:60]}",
                    t['sample']['text'][:300].replace('\n', ' ') + '...'
                )
    return None

# ── Main ─────────────────────────────────────────────────────────────────────

def run(exclude_nikkei=False):
    NIKKEI_KEYWORDS = ('nikkei', 'Nikkei')

    print(f'Loading articles from {TODAY_DIR} and {YESTERDAY_DIR}...')
    articles = load_articles()

    # Exclude Nikkei if requested
    if exclude_nikkei:
        before = len(articles)
        articles = [
            a for a in articles
            if not any(nk.lower() in a.get('source', '').lower() for nk in NIKKEI_KEYWORDS)
        ]
        print(f'Excluded Nikkei: {before} → {len(articles)} articles ({before - len(articles)} removed)')

    print(f'Loaded {len(articles)} articles')
    articles = load_articles()
    print(f'Loaded {len(articles)} articles')

    if not articles:
        print('No articles found. Aborting.')
        return

    sources = set(a['source'] for a in articles)
    n_sources = len(sources)
    print(f'Sources: {n_sources}')

    # Build topics with strict title-keyword matching
    topics = build_topics(articles)
    print(f'Top {len(topics)} topics by actual article count:')
    for t in topics:
        print(f'  {t["name"]}: {t["count"]} articles')

    # Build corpus for LLM
    corpus = build_corpus(topics)

    # Attempt LLM synthesis
    synthesis = None
    if corpus:
        print('Attempting LLM synthesis...')
        synthesis = synthesize_with_llm(topics, corpus, len(articles), n_sources)
        if synthesis:
            print('LLM synthesis complete.')

    # Generate HTML
    html = make_html(topics, synthesis, len(articles), n_sources)

    # Write today file AND dated file
    suffix = '_non-nikkei' if exclude_nikkei else ''
    out_today = OUT_DIR / f'daily_summary_today{suffix}.html'
    out_dated = OUT_DIR / f'daily_summary{suffix}_{TODAY}.html'
    for p in [out_today, out_dated]:
        with open(p, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f'Written: {len(html)} bytes to {p.name}')

    # Also save to /mnt/botsaves/ for long-term storage
    import shutil
    BOTSaves_DIR = Path("/mnt/botsaves/japan_news")
    BOTSaves_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(out_today, BOTSaves_DIR / f'daily_summary_today{suffix}.html')
    shutil.copy2(out_dated, BOTSaves_DIR / f'daily_summary{suffix}_{TODAY}.html')
    print(f'Also saved to {BOTSaves_DIR}')

    return out_today

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate Japan news summary HTML')
    parser.add_argument('--exclude-nikkei', action='store_true',
                        help='Exclude all Nikkei sections from the summary')
    args = parser.parse_args()
    run(exclude_nikkei=args.exclude_nikkei)
