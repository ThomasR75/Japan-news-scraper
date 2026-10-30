import json, hashlib, time, re
from pathlib import Path
from datetime import datetime, timezone, timedelta
import requests
from bs4 import BeautifulSoup

JP = timezone(timedelta(hours=9))
UA = ('Mozilla/5.0 (X11; Linux x86_64) '
      'AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/120.0.0.0 Safari/537.36')
NOISE = {'記事をシェアする', '完了しました', '西予', '説'}

def scrape_yomiuri_section(name, url, archive_dir, session_seen, max_articles=10):
    """Scrape a Yomiuri section index and its articles.
    Returns (saved, skipped).
    """
    try:
        idx = requests.get(url, headers={'User-Agent': UA}, timeout=15)
        idx.raise_for_status()
    except Exception as e:
        print(f'    Error fetching index: {e}')
        return 0, 0

    section = url.rstrip('/').rsplit('/', 1)[-1]
    # Regional stories sit one level down (/local/kansai/news/<id>/); every
    # other section links its articles directly (/politics/<id>/). The strict
    # form matched nothing on /local/ from day one.
    pattern = re.compile(r'href="(/' + re.escape(section)
                         + r'/(?:[a-z0-9_-]+/news/)?\d{8}-[A-Z][A-Z0-9]+\d+/)"')
    links = []
    for m in pattern.finditer(idx.text):
        full = 'https://www.yomiuri.co.jp' + m.group(1)
        if full not in links:
            links.append(full)
    print(f'    Found {len(links)} article links')

    saved = skipped = 0
    for article_url in links[:max_articles]:
        if article_url in session_seen:
            continue
        try:
            r = requests.get(article_url, headers={'User-Agent': UA}, timeout=15)
            r.raise_for_status()
        except Exception as e:
            print(f'    SKIP fetch error: {e}')
            skipped += 1
            continue

        soup = BeautifulSoup(r.text, 'html.parser')
        h1 = soup.find('h1')
        title = h1.get_text(strip=True) if h1 else ''

        # Extract publication time from JSON-LD
        import re as _re
        pub_match = _re.search(r'"datePublished"\s*:\s*"([^"]+)"', r.text)
        published_at = pub_match.group(1) if pub_match else ''

        body_el = soup.find(class_='article-content')
        if not body_el:
            print(f'    SKIP no article-content: {article_url}')
            skipped += 1
            continue
        paras = []
        for p in body_el.find_all('p'):
            txt = p.get_text(strip=True)
            if not txt or txt in NOISE:
                continue
            paras.append(txt)
        body = '\n'.join(paras)
        if len(body) < 500:
            print(f'    SKIP thin body ({len(body)} chars): {article_url}')
            skipped += 1
            continue

        url_hash = hashlib.md5(article_url.encode()).hexdigest()[:16]
        dir_ = Path(archive_dir) / name.replace(' ', '_')
        dir_.mkdir(parents=True, exist_ok=True)
        fp = dir_ / f'{url_hash}.json'
        data = {
            'url': article_url,
            'title': title,
            'extracted_text': body,
            'source': name,
            'date': datetime.now(JP).strftime('%Y-%m-%d'),
            'language': 'ja',
            'scraped_at': datetime.now(JP).isoformat(),
            'published_at': published_at,
            'scraper': 'requests-yomiuri'
        }
        with open(fp, 'w') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=4)
        session_seen.add(article_url)
        saved += 1
        print(f'    + {title[:40]}...')
        time.sleep(1)
    return saved, skipped
