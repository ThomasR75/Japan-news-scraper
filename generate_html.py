#!/usr/bin/env python3
"""
Generate HTML digest from scraped Japanese news articles.
Replaces generate_pdf.py — outputs clean HTML with TOC linking to each source.

Usage:
  python3 generate_html.py                 # all sources
  python3 generate_html.py --exclude-nikkei # non-Nikkei only
"""
import json
import os
import re
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

JP = timezone(timedelta(hours=9))

# ─── Japanese Shingle Deduplication ───────────────────────────────────────

def get_japanese_shingles(text, k=8):
    """Get character k-grams from Japanese text for similarity matching."""
    jp_chars = re.findall(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9faf]+', text)
    shingles = set()
    for chunk in jp_chars:
        for i in range(len(chunk) - k + 1):
            shingles.add(chunk[i:i+k])
    return frozenset(shingles)

def jaccard_overlap(a, b):
    if not a or not b:
        return 0
    return len(a & b) / len(a | b)

def dedup_articles_by_japanese_shingles(articles, threshold=0.55):
    """
    Remove duplicate articles based on Japanese text similarity using character shingles.
    Uses extracted_text (Japanese original) for shingling, falls back to translated_text.
    Returns (deduped_list, removed_count).
    """
    articles_deduped = []
    article_shingles = {}
    removed = 0

    for a in articles:
        # Use extracted_text (Japanese) for shingles; English translated_text won't
        # produce any Japanese character shingles so empty set = no dedup signal.
        txt = a.get('extracted_text', '') or a.get('body', '') or a.get('translated_text', '')
        sh = get_japanese_shingles(txt)
        is_dup = False
        for existing in articles_deduped:
            ex_sh = article_shingles[id(existing)]
            if jaccard_overlap(sh, ex_sh) > threshold:
                is_dup = True
                break
        if not is_dup:
            articles_deduped.append(a)
            article_shingles[id(a)] = sh
        else:
            removed += 1

    return articles_deduped, removed

def clean_text_for_html(text):
    """Clean text for safe HTML display — escape < > & " only, preserve all other characters including Japanese."""
    import html
    # Split into lines, escape each, rejoin
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        # Strip HTML tags
        line = re.sub(r'<[^>]+>', '', line)
        # Escape HTML entities
        line = html.escape(line)
        # Remove control chars and leading/trailing whitespace
        line = re.sub(r'[\x00-\x1f\x7f]', '', line).strip()
        if line:
            cleaned.append(line)
    return '\n'.join(cleaned)

def get_article_date(published_date_str):
    """Parse and format article date."""
    if not published_date_str:
        return ""
    for fmt in ['%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d']:
        try:
            dt = datetime.strptime(published_date_str[:19], fmt)
            return dt.strftime('%Y-%m-%d')
        except:
            pass
    return published_date_str[:10] if len(published_date_str) >= 10 else published_date_str

def generate_html_report(articles_by_source, output_path, date_str):
    """
    Generate HTML report from articles grouped by source.
    
    Args:
        articles_by_source: dict of {source_name: [article_dicts]}
        output_path: path to write HTML file
        date_str: date string for the report title
    """
    sources = list(articles_by_source.keys())
    
    html = []
    html.append("<!DOCTYPE html>")
    html.append("<html lang=\"en\">")
    html.append("<head>")
    html.append("  <meta charset=\"UTF-8\">")
    html.append("  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">")
    html.append(f"  <title>Japan News Digest — {date_str}</title>")
    html.append("  <style>")
    html.append("    * { box-sizing: border-box; margin: 0; padding: 0; }")
    html.append("    body { font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; ")
    html.append("           background: #f8f9fa; color: #1a1a1a; padding: 20px; line-height: 1.6; }")
    html.append("    .container { max-width: 900px; margin: 0 auto; }")
    html.append("    h1 { font-size: 24px; font-weight: 700; margin-bottom: 8px; color: #0d2137; }")
    html.append("    .subtitle { color: #666; font-size: 13px; margin-bottom: 24px; }")
    html.append("    .toc { background: white; border-radius: 8px; padding: 20px 24px; ")
    html.append("            margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }")
    html.append("    .toc h2 { font-size: 16px; font-weight: 600; margin-bottom: 12px; color: #333; }")
    html.append("    .toc ol { padding-left: 20px; }")
    html.append("    .toc li { margin-bottom: 6px; }")
    html.append("    .toc a { color: #1a6fd4; text-decoration: none; font-size: 14px; }")
    html.append("    .toc a:hover { text-decoration: underline; }")
    html.append("    .source-block { background: white; border-radius: 8px; margin-bottom: 20px; ")
    html.append("                    box-shadow: 0 1px 3px rgba(0,0,0,0.08); overflow: hidden; }")
    html.append("    .source-header { background: #f0f4f8; padding: 14px 20px; ")
    html.append("                    border-bottom: 1px solid #e4e8ec; }")
    html.append("    .source-header h2 { font-size: 15px; font-weight: 600; color: #1a3a5c; }")
    html.append("    .article { padding: 16px 20px; border-bottom: 1px solid #f0f0f0; }")
    html.append("    .article:last-child { border-bottom: none; }")
    html.append("    .article-title { font-size: 15px; font-weight: 600; color: #0d2137; ")
    html.append("                     margin-bottom: 4px; line-height: 1.4; }")
    html.append("    .article-title a { color: inherit; text-decoration: none; }")
    html.append("    .article-title a:hover { color: #1a6fd4; text-decoration: underline; }")
    html.append("    .article-meta { font-size: 12px; color: #888; margin-bottom: 8px; }")
    html.append("    .article-link { font-size: 12px; margin-bottom: 8px; }")
    html.append("    .article-link a { color: #1a6fd4; text-decoration: none; }")
    html.append("    .article-link a:hover { text-decoration: underline; }")
    html.append("    .article-summary { font-size: 14px; color: #333; line-height: 1.65; }")
    html.append("    .section-label { font-size: 11px; font-weight: 600; color: #888; ")
    html.append("                    text-transform: uppercase; letter-spacing: 0.5px; ")
    html.append("                    margin-bottom: 4px; }")
    html.append("    .source-footer { padding: 10px 20px; border-top: 1px solid #e4e8ec; }")
    html.append("    .source-footer a { font-size: 13px; color: #1a6fd4; text-decoration: none; }")
    html.append("    .source-footer a:hover { text-decoration: underline; }")
    html.append("    @media (max-width: 600px) { body { padding: 12px; } }")
    html.append("  </style>")
    html.append("</head>")
    html.append("<body>")
    html.append("  <div class=\"container\">")
    
    # Title
    html.append(f"    <h1>📰 Japan News Digest</h1>")
    html.append(f"    <p class=\"subtitle\">{date_str} JST — Generated {datetime.now(JP).strftime('%Y-%m-%d %H:%M')}</p>")
    
    # Table of contents — sorted alphabetically to match source blocks
    html.append("    <div class=\"toc\">")
    html.append("      <h2>📋 Table of Contents</h2>")
    html.append("      <ol>")
    for src in sorted(sources):
        anchor = src.lower().replace(' ', '-').replace('"', '')
        html.append(f'        <li><a href="#{anchor}">{src}</a> ({len(articles_by_source[src])} articles)</li>')
    html.append("      </ol>")
    html.append("    </div>")
    
    # Articles by source — sorted alphabetically
    for src in sorted(sources):
        articles = articles_by_source[src]
        anchor = src.lower().replace(' ', '-').replace('"', '')
        html.append(f'    <div class="source-block" id="{anchor}">')
        html.append(f'      <div class="source-header"><h2>{src}</h2></div>')
        
        for art in sorted(articles, key=lambda a: (a.get('title_en') or a.get('title') or '').lower()):
            title = clean_text_for_html(art.get('title_en') or art.get('title') or 'No Title')
            url = art.get('url', '')
            date = get_article_date(art.get('published_at') or art.get('date', ''))
            # Show full translated article content (no truncation)
            full_text = art.get('translated_text', '') or art.get('extracted_text', '') or art.get('body', '')
            summary = clean_text_for_html(full_text)
            
            html.append('      <div class="article">')
            html.append(f'        <div class="article-title"><a href="{url}" target="_blank">{title}</a></div>')
            meta_parts = []
            if date:
                meta_parts.append(date)
            meta_parts.append(f'<a href="{url}" target="_blank">🔗 Source</a>')
            if meta_parts:
                html.append(f'        <div class="article-meta">{" · ".join(meta_parts)}</div>')
            if summary:
                html.append(f'        <div class="article-summary">{summary}</div>')
            html.append('      </div>')

        html.append('      <div class="source-footer"><a href="#top">↑ Back to top</a></div>')
        html.append('    </div>')
    
    html.append("  </div>")
    html.append("</body>")
    html.append("</html>")
    
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(html))
    print(f"HTML report generated: {output_path}")

def is_nikkei_source(source_name):
    return 'nikkei' in source_name.lower()

def load_articles_for_date(date_str, max_age_hours=None):
    """
    Load all scraped articles from data/news_archive/raw/ that match the date.
    Returns dict of {source_name: [article_dicts]}.
    If max_age_hours is set, skip Nikkei articles older than that.
    """
    base = '/home/blablom/.openclaw/workspace/japan_news_scraper/data/news_archive/raw'
    articles_by_source = {}
    now = datetime.now(JP)
    if max_age_hours:
        cutoff = now.replace(tzinfo=None) - timedelta(hours=max_age_hours)
    
    if not os.path.exists(base):
        print(f"Archive directory {base} not found!")
        return articles_by_source
    
    # Filter by date string (e.g. "20260323")
    for source_dir in os.listdir(base):
        src_path = os.path.join(base, source_dir)
        if not os.path.isdir(src_path):
            continue
        for fname in os.listdir(src_path):
            if not fname.endswith('.json'):
                continue
            fpath = os.path.join(src_path, fname)
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    art = json.load(f)
                src = art.get('source', source_dir)

                # Age filter for Nikkei articles
                if max_age_hours and is_nikkei_source(src):
                    ts = art.get('fetch_timestamp', '') or art.get('translated_at', '')
                    if ts:
                        try:
                            art_dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                            if art_dt.tzinfo:
                                art_dt = art_dt.astimezone(JP).replace(tzinfo=None)
                            if art_dt < cutoff:
                                continue  # skip old article
                        except:
                            pass

                if src not in articles_by_source:
                    articles_by_source[src] = []
                articles_by_source[src].append(art)
            except Exception as e:
                print(f"Error loading {fpath}: {e}")
    
    print(f"Loaded {sum(len(v) for v in articles_by_source.values())} articles across {len(articles_by_source)} sources")
    return articles_by_source

if __name__ == '__main__':
    from datetime import datetime, timezone, timedelta
    import glob
    import shutil
    JP = timezone(timedelta(hours=9))

    # ── Command-line flags ───────────────────────────────────────────────
    parser = argparse.ArgumentParser(description='Generate Japan news digest HTML')
    parser.add_argument('--exclude-nikkei', action='store_true',
                        help='Exclude all Nikkei sections from the digest')
    parser.add_argument('--nikkei-only', action='store_true',
                        help='Include ONLY Nikkei sections in the digest')
    parser.add_argument('--max-age-hours', type=int, default=24,
                        help='Skip Nikkei articles older than this many hours (default: 24)')
    args = parser.parse_args()

    # Nikkei source name substrings to filter out
    NIKKEI_KEYWORDS = ('nikkei', 'Nikkei')

    # Find the most recent date with scraped articles
    base = '/home/blablom/.openclaw/workspace/japan_news_scraper/data/news_archive/raw'
    all_files = []
    if os.path.exists(base):
        for src in os.listdir(base):
            src_path = os.path.join(base, src)
            if os.path.isdir(src_path):
                for f in os.listdir(src_path):
                    if f.endswith('.json'):
                        all_files.append(os.path.join(src_path, f))
    
    if not all_files:
        print("No scraped articles found!")
        exit(1)
    
    # Get most recent date by scanning all filenames (from JST now)
    JP = timezone(timedelta(hours=9))
    jst_now = datetime.now(JP)
    jst_date_str = jst_now.strftime('%Y%m%d')
    latest_date = jst_date_str  # default to today JST
    yesterday_date = (jst_now - timedelta(days=1)).strftime('%Y%m%d')

    for fpath in all_files:
        fname = os.path.basename(fpath)
        date_part = fname.split('_')[0]
        # Only consider valid YYYYMMDD date strings (8 digits)
        if len(date_part) == 8 and date_part.isdigit():
            if date_part in (jst_date_str, yesterday_date) and date_part > latest_date:
                latest_date = date_part

    # Fallback: scan all files for the true latest
    for fpath in all_files:
        fname = os.path.basename(fpath)
        date_part = fname.split('_')[0]
        if len(date_part) == 8 and date_part.isdigit() and date_part > latest_date:
            latest_date = date_part

    date_str = latest_date
    print(f"Latest article date (from filenames): {date_str}")
    
    # Load articles matching date_str (last 24h for non-Nikkei, max_age_hours for Nikkei)
    articles_by_source = load_articles_for_date(date_str, max_age_hours=args.max_age_hours)

    # ── Additional 24h time filter for non-Nikkei articles ─────────────────
    # Skip articles with fetch_timestamp older than 24h (from JST now)
    cutoff_hours = 24
    cutoff = jst_now.replace(tzinfo=None) - timedelta(hours=cutoff_hours)
    before_count = sum(len(v) for v in articles_by_source.values())
    filtered_by_source = {}
    for src, arts in articles_by_source.items():
        if any(nk.lower() in src.lower() for nk in NIKKEI_KEYWORDS):
            # Don't time-filter Nikkei (handled by max_age_hours param above)
            filtered_by_source[src] = arts
            continue
        filtered = []
        for art in arts:
            ts_str = art.get('scraped_at', '') or art.get('fetch_timestamp', '') or art.get('translated_at', '')
            if ts_str:
                try:
                    ts_dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    if ts_dt.tzinfo:
                        ts_dt = ts_dt.astimezone(JP).replace(tzinfo=None)
                    if ts_dt >= cutoff:
                        filtered.append(art)
                except:
                    # No parseable timestamp — include it (trust filename date)
                    filtered.append(art)
            else:
                # No timestamp — include it (trust filename date is recent enough)
                filtered.append(art)
        if filtered:
            filtered_by_source[src] = filtered
    articles_by_source = filtered_by_source
    after_count = sum(len(v) for v in articles_by_source.values())
    print(f"Time filter (24h JST): {before_count} → {after_count} articles")
    if after_count == 0:
        print("WARNING: No articles in last 24h — check if scrape ran")

    # ── Exclude Nikkei if requested ──────────────────────────────────────
    if args.exclude_nikkei:
        before = sum(len(v) for v in articles_by_source.values())
        articles_by_source = {
            src: arts
            for src, arts in articles_by_source.items()
            if not any(nk.lower() in src.lower() for nk in NIKKEI_KEYWORDS)
        }
        after = sum(len(v) for v in articles_by_source.values())
        print(f'Excluded Nikkei: {before} → {after} articles ({before - after} removed)')

    # ── Include only Nikkei if requested ─────────────────────────────────
    if args.nikkei_only:
        before = sum(len(v) for v in articles_by_source.values())
        articles_by_source = {
            src: arts
            for src, arts in articles_by_source.items()
            if any(nk.lower() in src.lower() for nk in NIKKEI_KEYWORDS)
        }
        after = sum(len(v) for v in articles_by_source.values())
        print(f'Nikkei only: {before} → {after} articles')

    # ─── Japanese shingles deduplication ───────────────────────────────────
    # Flatten, dedup across all sources, regroup by source
    flat = [art for arts in articles_by_source.values() for art in arts]
    print(f"Articles before Japanese shingles dedup: {len(flat)}")
    flat_deduped, removed = dedup_articles_by_japanese_shingles(flat, threshold=0.55)
    print(f"Articles after  Japanese shingles dedup: {len(flat_deduped)} (removed {removed} duplicates)")

    # Regroup deduped articles by source
    deduped_by_source = {}
    for a in flat_deduped:
        src = a.get('source', 'Unknown')
        if src not in deduped_by_source:
            deduped_by_source[src] = []
        deduped_by_source[src].append(a)
    articles_by_source = deduped_by_source

    # Generate HTML
    date_formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    if args.exclude_nikkei:
        suffix = "_non-nikkei"
    elif args.nikkei_only:
        suffix = "_nikkei"
    else:
        suffix = ""
    # Primary save locations
    output_path = f"/home/blablom/.openclaw/workspace/japan_news_scraper/data/reports/daily_digest{suffix}_{date_formatted}.html"
    today_path = f"/home/blablom/.openclaw/workspace/japan_news_scraper/data/reports/daily_digest_today{suffix}.html"
    generate_html_report(articles_by_source, output_path, date_formatted)
    generate_html_report(articles_by_source, today_path, date_formatted)  # always overwrite today symlink

    # Also save to /mnt/botsaves/ for long-term storage
    BOTSaves_DIR = Path("/mnt/botsaves/japan_news")
    BOTSaves_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(output_path, BOTSaves_DIR / f"daily_digest{suffix}_{date_formatted}.html")
    shutil.copy2(today_path, BOTSaves_DIR / f"daily_digest_today{suffix}.html")
    print(f"Also saved to {BOTSaves_DIR}")
