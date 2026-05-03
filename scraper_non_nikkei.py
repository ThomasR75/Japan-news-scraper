#!/usr/bin/env python3
"""
Non-Nikkei scraper — Lightpanda-only browser policy.
Nikkei is handled separately by its own Chrome cron.

BROWSER POLICY (per ref-non-nikkei-scraper.md):
  1. RSS feeds where available (structured, polite, stable)
  2. requests + BeautifulSoup for static HTML
  3. Lightpanda CLI for JS-heavy SPAs only
  NO Chrome fallback. NO mcporter. NO Windows node.

Order of preference: RSS → requests → Lightpanda.
Don't escalate unless the lower-friction path actually fails.

If Lightpanda fails to extract article body (<500 chars), log the URL and skip.
Do not escalate to Chrome.
"""
import sys
import os
import json
import time
import re
from urllib.parse import urljoin
import hashlib
import subprocess
import requests
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup

JP = timezone(timedelta(hours=9))
BASE = '/home/blablom/.openclaw/workspace/japan_news_scraper/data/news_archive/raw'
LIGHTPANDA_BIN = '/home/blablom/.local/bin/lightpanda'

# ── Sources: requests path (static HTML, fast) ────────────────────────────────
REQUESTS_SOURCES = [
    # Asahi (3)
    ("Asahi Politics",       "https://www.asahi.com/politics/",       "a[href*='/articles/']", "h1, h1[class*='title']", "time", "article p, .article-body p"),
    ("Asahi Business",      "https://www.asahi.com/business/",        "a[href*='/articles/']", "h1, h1[class*='title']", "time", "article p, .article-body p"),
    ("Asahi International", "https://www.asahi.com/international/",    "a[href*='/articles/']", "h1, h1[class*='title']", "time", "article p, .article-body p"),
    # Mainichi (3)
    ("Mainichi Politics",     "https://mainichi.jp/seiji/",            "a[href*='/articles/']", "h1, h1[class*='title']", "time", "article p, .article-body p"),
    ("Mainichi International","https://mainichi.jp/world/",             "a[href*='/articles/']", "h1, h1[class*='title']", "time", "article p, .article-body p"),
    ("Mainichi Economy",      "https://mainichi.jp/economy/",           "a[href*='/articles/']", "h1, h1[class*='title']", "time", "article p, .article-body p"),
    # Sankei (3) — all confirmed accessible via requests
    ("Sankei Politics",      "https://www.sankei.com/politics/",       "a[href*='/article/202']","h1", "time", "article p, .article-body p"),
    ("Sankei Economy",       "https://www.sankei.com/economy/",        "a[href*='/article/202']","h1", "time", "article p, .article-body p"),
    ("Sankei International", "https://www.sankei.com/world/",          "a[href*='/article/202']","h1", "time", "article p, .article-body p"),
    # FNN (3) — uses RSS listing + requests for article body
    ("FNN Politics",         "https://www.fnn.jp/subcategory/politics", "a[href*='/articles/']",  "h1", "time", "article p"),
    ("FNN Economy",          "https://www.fnn.jp/subcategory/economy",  "a[href*='/articles/']",  "h1", "time", "article p"),
    ("FNN International",    "https://www.fnn.jp/subcategory/world",    "a[href*='/articles/']",  "h1", "time", "article p"),
]

# ── Sources: RSS path (NHK) ────────────────────────────────────────────────────
RSS_SOURCES = [
    # NHK (3) — all via RSS feeds; cat1=politics, cat3=international, cat4=economy
    ("NHK Politics",       "https://www.nhk.or.jp/rss/news/cat1.xml"),
    ("NHK International", "https://www.nhk.or.jp/rss/news/cat3.xml"),
    ("NHK Economy",       "https://www.nhk.or.jp/rss/news/cat4.xml"),
]

# ── Sources: Lightpanda path (JS-heavy SPA) ─────────────────────────────────
LIGHTPANDA_SOURCES = []  # disabled 2026-04-28: replaced by requests-based YOMIURI_SOURCES

YOMIURI_SOURCES = [
    ("Yomiuri Politics",      "https://www.yomiuri.co.jp/politics/"),
    ("Yomiuri Economy",       "https://www.yomiuri.co.jp/economy/"),
    ("Yomiuri International", "https://www.yomiuri.co.jp/world/"),
    ("Yomiuri Society",       "https://www.yomiuri.co.jp/national/"),
    ("Yomiuri Local",         "https://www.yomiuri.co.jp/local/"),
]

SESSION_URLS_SET = set()
ARCHIVE_DIR = Path(BASE)


def _load_existing_urls():
    """Load URLs seen in the last 48h to avoid duplicate writes."""
    global SESSION_URLS_SET
    cutoff = datetime.now(JP) - timedelta(hours=48)
    for f in ARCHIVE_DIR.glob('*/*.json'):
        try:
            with open(f) as fh:
                d = json.load(fh)
            scraped = datetime.fromisoformat(d.get('scraped_at', '2000-01-01'))
            if scraped > cutoff:
                SESSION_URLS_SET.add(d.get('url', ''))
        except Exception:
            pass


def _save_article(name, url, title, body_text, date_str=''):
    """Write article JSON. Single write path for all methods."""
    if not body_text or len(body_text) < 100:
        return False
    url_hash = hashlib.md5(url.encode()).hexdigest()[:16]
    source_dir = ARCHIVE_DIR / name.replace(' ', '_')
    source_dir.mkdir(parents=True, exist_ok=True)
    filepath = source_dir / f"{url_hash}.json"
    data = {
        "url": url,
        "title": title,
        "extracted_text": body_text.strip(),
        "source": name,
        "date": date_str or datetime.now(JP).strftime('%Y-%m-%d'),
        "language": "ja",
        "scraped_at": datetime.now(JP).isoformat(),
        "scraper": "requests"
    }
    with open(filepath, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    return True


# ── REQUESTS path ────────────────────────────────────────────────────────────────

def _fetch_requests_listing(name, url, link_sel, title_sel, date_sel, text_sel, max_pages=2):
    """Get article links from a listing page via requests + BeautifulSoup."""
    links = []
    page_url = url
    for page in range(max_pages):
        try:
            resp = requests.get(page_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, 'html.parser')
            for a in soup.select(link_sel):
                href = a.get('href', '')
                if href.startswith('http'):
                    links.append(href)
                elif href.startswith('/'):
                    links.append(urljoin(resp.url, href))
            next_btn = soup.select_one('a[rel="next"], .next, a:contains("次へ")')
            if not next_btn or page >= max_pages - 1:
                break
            next_href = next_btn.get('href', '')
            if not next_href:
                break
            page_url = urljoin(url, next_href)
        except Exception as e:
            print(f"    Error fetching listing: {e}")
            break
    return list(dict.fromkeys(links))[:15]


def _scrape_requests_source(name, url, link_sel, title_sel, date_sel, text_sel, max_articles=10):
    """Scrape articles from a static HTML source via requests."""
    links = _fetch_requests_listing(name, url, link_sel, title_sel, date_sel, text_sel)
    if not links:
        try:
            resp = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            for m in re.findall(r'href="([^"]+/articles?/[^"]+)"', resp.text):
                links.append(m if m.startswith('http') else urljoin(resp.url, m))
            links = list(dict.fromkeys(links))[:15]
        except Exception:
            pass

    print(f"    Found {len(links)} article links")
    saved = 0
    for article_url in links[:max_articles]:
        if article_url in SESSION_URLS_SET:
            continue
        try:
            resp = requests.get(article_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, 'html.parser')
            title = ' '.join(s.get_text(strip=True) for s in soup.select(title_sel))
            date_str = ''
            for t in soup.select(date_sel):
                dt = t.get('datetime') or t.get_text(strip=True)
                if dt:
                    date_str = dt[:10] if len(dt) > 10 else dt
                    break
            body = ' '.join(p.get_text(strip=True) for p in soup.select(text_sel)
                           if len(p.get_text(strip=True)) > 50 and p.find_parent(['script', 'style']) is None)
            if _save_article(name, article_url, title, body, date_str):
                SESSION_URLS_SET.add(article_url)
                saved += 1
                print(f"    + {title[:40]}...")
        except Exception as e:
            print(f"    Error fetching article {article_url}: {e}")
    return saved


# ── RSS path (NHK) ────────────────────────────────────────────────────────────

def _scrape_rss_source(name, rss_url, max_articles=10):
    """Fetch RSS feed, then fetch each article URL via requests."""
    try:
        resp = requests.get(rss_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        if resp.status_code != 200:
            print(f"    RSS returned {resp.status_code}")
            return 0
        root = ET.fromstring(resp.content)
        channel = root.find('channel')
        items = channel.findall('item') if channel is not None else []
    except Exception as e:
        print(f"    RSS parse error: {e}")
        return 0

    print(f"    RSS feed: {len(items)} items")
    saved = 0
    for item in items[:max_articles]:
        link_el = item.find('link')
        link = link_el.text if link_el is not None and link_el.text else ''
        if not link or link in SESSION_URLS_SET:
            continue

        title_el = item.find('title')
        title = title_el.text.strip() if title_el is not None and title_el.text else ''
        date_el = item.find('pubDate')
        date_str = ''
        if date_el is not None and date_el.text:
            try:
                dt = datetime.strptime(date_el.text[:len('Mon, 27 Apr 2026 17:31:10 +0900')], '%a, %d %b %Y %H:%M:%S %z')
                date_str = dt.strftime('%Y-%m-%d')
            except Exception:
                pass

        # Fetch article body via requests
        try:
            article_resp = requests.get(link, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            if article_resp.status_code != 200:
                continue
            soup = BeautifulSoup(article_resp.text, 'html.parser')
            # NHK article body selector
            body = ' '.join(p.get_text(strip=True) for p in soup.select('article p, .NewsBody p, .article-body p'))
            if _save_article(name, link, title, body, date_str):
                SESSION_URLS_SET.add(link)
                saved += 1
                print(f"    + {title[:40]}...")
        except Exception as e:
            print(f"    Article fetch error {link}: {e}")

    return saved


# ── LIGHTPANDA path ──────────────────────────────────────────────────────────

def _lightpanda_fetch(url):
    """Fetch a URL via Lightpanda CLI. Returns markdown text or None."""
    cmd = [LIGHTPANDA_BIN, 'fetch', '--dump', 'markdown', '--wait-ms', '8000', url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        if result.returncode == 0:
            return result.stdout
    except Exception as e:
        print(f"    Lightpanda error for {url}: {e}")
    return None


def _extract_links_from_markdown(md_text):
    """Extract date-based article URLs from Lightpanda markdown dump."""
    links = []
    for line in md_text.split('\n'):
        for u in re.findall(r'https?://[^\s\)<>\'"\]#]+', line):
            if any(seg in u for seg in ('/article/202', '/news/202', '/yomidr/')) and len(u) > 30:
                links.append(u)
    return list(dict.fromkeys(links))[:15]


def _scrape_lightpanda_source(name, url, max_articles=10):
    """Scrape a JS-heavy SPA source via Lightpanda. No Chrome fallback."""
    page_md = _lightpanda_fetch(url)
    if not page_md:
        print(f"    Error: Lightpanda failed to fetch listing page")
        return 0

    links = _extract_links_from_markdown(page_md)
    print(f"    Found {len(links)} article links via Lightpanda")

    if not links:
        for line in page_md.split('\n'):
            for u in re.findall(r'https?://[^\s\)<>\'"\]#]+', line):
                if '202' in u and len(u) > 40 and u not in links:
                    links.append(u)
        links = links[:15]

    saved = 0
    for article_url in links[:max_articles]:
        if article_url in SESSION_URLS_SET:
            continue

        article_md = _lightpanda_fetch(article_url)
        if not article_md:
            print(f"    Skipping (Lightpanda failed): {article_url}")
            continue

        title = ""
        body_lines = []
        in_body = False
        for line in article_md.split('\n'):
            line = line.strip()
            if not line:
                continue
            if not title and len(line) > 10:
                title = line[:100]
                in_body = True
                continue
            if in_body:
                body_lines.append(line)
            if len('\n'.join(body_lines)) > 600:
                break

        body_text = '\n'.join(body_lines)
        if len(body_text) < 500:
            print(f"    SKIP (body={len(body_text)} chars < 500, Lightpanda SPA limitation): {article_url}")
            continue

        url_hash = hashlib.md5(article_url.encode()).hexdigest()[:16]
        source_dir = ARCHIVE_DIR / name.replace(' ', '_')
        source_dir.mkdir(parents=True, exist_ok=True)
        filepath = source_dir / f"{url_hash}.json"
        data = {
            "url": article_url,
            "title": title,
            "extracted_text": body_text.strip(),
            "source": name,
            "date": datetime.now(JP).strftime('%Y-%m-%d'),
            "language": "ja",
            "scraped_at": datetime.now(JP).isoformat(),
            "scraper": "lightpanda-direct"
        }
        with open(filepath, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        SESSION_URLS_SET.add(article_url)
        saved += 1
        print(f"    + {title[:40]}...")
        time.sleep(1)

    return saved


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("NON-NIKKEI SCRAPER — Lightpanda-only policy")
    print("  requests path: Asahi, Mainichi, Sankei, FNN (12 sources)")
    print("  RSS path:      NHK cat1/cat3/cat4 (3 sources)")
    print("  Lightpanda:    Yomiuri (3 sources)")
    print("  NO Chrome. NO mcporter. NO Windows node.")
    print("=" * 60)

    _load_existing_urls()

    total_saved = 0

    print("\n### REQUESTS PATH (static HTML + FNN with RSS listing) ###")
    for name, url, link_sel, title_sel, date_sel, text_sel in REQUESTS_SOURCES:
        print(f"\n--- {name} ---")
        saved = _scrape_requests_source(name, url, link_sel, title_sel, date_sel, text_sel)
        print(f"  → {saved} saved")
        total_saved += saved

    print("\n### RSS PATH (NHK) ###")
    for name, rss_url in RSS_SOURCES:
        print(f"\n--- {name} ---")
        saved = _scrape_rss_source(name, rss_url)
        print(f"  → {saved} saved")
        total_saved += saved

    print("\n### LIGHTPANDA PATH (JS-heavy SPA) ###")
    for name, url in LIGHTPANDA_SOURCES:
        print(f"\n--- {name} ---")
        saved = _scrape_lightpanda_source(name, url)
        print(f"  → {saved} saved")
        total_saved += saved

    print("\n### YOMIURI PATH (requests + BeautifulSoup) ###")
    import sys
    sys.path.insert(0, '/home/blablom/bin')
    from yomiuri_scraper import scrape_yomiuri_section
    for name, url in YOMIURI_SOURCES:
        print(f"\n--- {name} ---")
        saved, skipped = scrape_yomiuri_section(name, url, ARCHIVE_DIR, SESSION_URLS_SET)
        print(f"  → {saved} saved, {skipped} skipped")
        total_saved += saved

    print(f"\n{'='*60}")
    print(f"Done. Total saved: {total_saved}")

if __name__ == '__main__':
    main()
