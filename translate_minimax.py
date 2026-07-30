import json, glob, os, sys, time
from datetime import datetime, timezone, timedelta

from minimax_translate import translate, MODEL

JP = timezone(timedelta(hours=9))
BASE = '/home/blablom/.openclaw/workspace/japan_news_scraper/data/news_archive/raw'
PAUSE = 1
MAX_RETRIES = 3
BACKOFF = 10

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

# Load env if not set
if not os.environ.get('MINIMAX_API_KEY'):
    envfile = os.path.expanduser('~/.config/openclaw/secrets.env')
    if os.path.exists(envfile):
        with open(envfile) as fh:
            for line in fh:
                if line.startswith('MINIMAX_API_KEY='):
                    v = line.split('=', 1)[1].strip().strip('"').strip("'")
                    os.environ['MINIMAX_API_KEY'] = v

todo = []
for s in sorted(os.listdir(BASE)):
    sd = os.path.join(BASE, s)
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

log('Files needing translation: ' + str(len(todo)))
if not todo:
    log('Nothing to do.')
    sys.exit(0)
log('Estimated time: ~' + str(len(todo) * PAUSE // 60 + 1) + ' min')

done = errors = 0
section_counts = {}
for i, (section, f) in enumerate(todo, 1):
    try:
        d = load_json(f)
        title = (d.get('title') or '').strip()
        body = (d.get('extracted_text') or '').strip()
        if not body and not title:
            errors += 1
            continue
        r, err = translate_with_retry(title, body)
        if not r:
            errors += 1
            log('  GIVE UP ' + os.path.basename(f) + ': ' + (err or 'no response'))
            continue
        if r.get('body_en'):
            d['translated_text'] = r['body_en']
        if r.get('title_en'):
            d['title_en'] = r['title_en']
        d['translated_at'] = datetime.now(JP).isoformat()
        # Derived from minimax_translate.MODEL, not hardcoded: this label was
        # stamped 'MiniMax-M2.7' while the API call actually sent M2.5.
        d['translated_by'] = MODEL
        save_json_atomic(f, d)
        done += 1
        section_counts[section] = section_counts.get(section, 0) + 1
        if i % 10 == 0 or i == len(todo):
            log('Progress ' + str(i) + '/' + str(len(todo)) + '  ok=' + str(done) + ' err=' + str(errors) + '  current=[' + section + ']')
    except Exception as e:
        errors += 1
        log('  WRAPPER ERR ' + os.path.basename(f) + ': ' + type(e).__name__ + ': ' + str(e)[:120])
    time.sleep(PAUSE)

log('=' * 60)
log('COMPLETE  total=' + str(len(todo)) + '  ok=' + str(done) + '  err=' + str(errors))
for s, n in sorted(section_counts.items()):
    log('  ' + s + ': ' + str(n))
