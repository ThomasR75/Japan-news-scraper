import json, glob, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

from minimax_translate import translate, MODEL

JP = timezone(timedelta(hours=9))
BASE = '/home/blablom/.openclaw/workspace/japan_news_scraper/data/news_archive/raw'
MAX_RETRIES = 3
BACKOFF = 10
# Each article is one MiniMax round trip of ~20s, almost all of it waiting on
# the API — so a thread pool is the right tool and 4 workers cut a 292-article
# night from ~97 min to ~25. Sequential was fine only while the Nikkei cookies
# were dead and there was little to translate; the day they were fixed
# (2026-09-28) the workload tripled and systemd killed the run at 60 min with
# nothing delivered. Override with TRANSLATE_WORKERS=1 to get the old loop.
# Default lowered 4 -> 2 on 2026-09-30 at Thomas's request (170 articles took
# 14 min at 4, so ~28 at 2 — well inside the unit's 3 h ceiling).
WORKERS = int(os.environ.get('TRANSLATE_WORKERS', '2') or 2)
SECONDS_PER_ARTICLE = 20   # for the estimate line only

def log(m):
    ts = datetime.now(JP).strftime('%H:%M:%S')
    print('[' + ts + ' JST] ' + m, flush=True)

def load_json(path):
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)

def save_json_atomic(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=4)
    os.replace(tmp, path)

def translate_with_retry(title, body):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = translate(title, body)
            if r and r.get('title_en') and r.get('body_en'):
                return r, None
            return None, 'incomplete response'
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = BACKOFF * attempt
                log('  retry ' + str(attempt) + '/' + str(MAX_RETRIES) + ' after ' + str(wait) + 's: ' + type(e).__name__)
                time.sleep(wait)
            else:
                return None, type(e).__name__ + ': ' + str(e)[:120]
    return None, 'unreachable'

def load_env():
    if not os.environ.get('MINIMAX_API_KEY'):
        envfile = os.path.expanduser('~/.config/openclaw/secrets.env')
        if os.path.exists(envfile):
            with open(envfile) as fh:
                for line in fh:
                    if line.startswith('MINIMAX_API_KEY='):
                        v = line.split('=', 1)[1].strip().strip('"').strip("'")
                        os.environ['MINIMAX_API_KEY'] = v

def find_todo(base=BASE):
    todo = []
    for s in sorted(os.listdir(base)):
        sd = os.path.join(base, s)
        if not os.path.isdir(sd):
            continue
        for f in glob.glob(sd + '/*.json'):
            try:
                d = load_json(f)
                needs_body = d.get('extracted_text') and not d.get('translated_text')
                needs_title = d.get('title') and not d.get('title_en')
                if needs_body or needs_title:
                    todo.append((s, f))
            except Exception as e:
                log('  SKIP unreadable ' + os.path.basename(f) + ': ' + type(e).__name__)
    return todo

def translate_one(section, f):
    # One article, start to finish, on a worker thread. Returns
    # (status, section, filename, error) and never raises: the pool must keep
    # draining if a single article blows up. The write is atomic per file and
    # each file has its own .tmp, so workers cannot clobber each other.
    name = os.path.basename(f)
    try:
        d = load_json(f)
        title = (d.get('title') or '').strip()
        body = (d.get('extracted_text') or '').strip()
        if not body and not title:
            return 'err', section, name, 'empty article'
        r, err = translate_with_retry(title, body)
        if not r:
            return 'err', section, name, err or 'no response'
        if r.get('body_en'):
            d['translated_text'] = r['body_en']
        if r.get('title_en'):
            d['title_en'] = r['title_en']
        d['translated_at'] = datetime.now(JP).isoformat()
        # Derived from minimax_translate.MODEL, not hardcoded: this label was
        # stamped 'MiniMax-M2.7' while the API call actually sent M2.5.
        d['translated_by'] = MODEL
        save_json_atomic(f, d)
        return 'ok', section, name, None
    except Exception as e:
        return 'err', section, name, 'WRAPPER ERR ' + type(e).__name__ + ': ' + str(e)[:120]

def run(todo, workers=WORKERS, quiet=False):
    # Counters and logging live on the main thread; workers only return
    # tuples. That keeps the progress lines in one place and needs no lock.
    done = errors = 0
    section_counts = {}
    total = len(todo)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(translate_one, s, f) for s, f in todo]
        for i, fut in enumerate(as_completed(futures), 1):
            status, section, name, err = fut.result()
            if status == 'ok':
                done += 1
                section_counts[section] = section_counts.get(section, 0) + 1
            else:
                errors += 1
                if not quiet:
                    log('  GIVE UP ' + name + ': ' + str(err))
            if not quiet and (i % 10 == 0 or i == total):
                log('Progress ' + str(i) + '/' + str(total) + '  ok=' + str(done) + ' err=' + str(errors) + '  current=[' + section + ']')
    return done, errors, section_counts

def main():
    load_env()
    todo = find_todo()
    log('Files needing translation: ' + str(len(todo)))
    if not todo:
        log('Nothing to do.')
        return 0
    log('Workers: ' + str(WORKERS) + '  estimated ~' + str(len(todo) * SECONDS_PER_ARTICLE // max(1, WORKERS) // 60 + 1) + ' min')
    done, errors, section_counts = run(todo, WORKERS)
    log('=' * 60)
    log('COMPLETE  total=' + str(len(todo)) + '  ok=' + str(done) + '  err=' + str(errors))
    for s, n in sorted(section_counts.items()):
        log('  ' + s + ': ' + str(n))
    return 0

if __name__ == '__main__':
    sys.exit(main())
