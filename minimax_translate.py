import json, urllib.request, urllib.error, time

CONFIG = '/home/blablom/.openclaw/openclaw.json'
MODEL = 'MiniMax-M2.5'

def get_endpoint_and_key():
    cfg = json.load(open(CONFIG))
    base = cfg['models']['providers']['minimax']['baseUrl'].rstrip('/')
    import os
    key = os.environ.get('MINIMAX_API_KEY')
    if not key:
        ap = cfg.get('auth', {}).get('profiles', {}).get('minimax:default', {})
        key = ap.get('apiKey') or ap.get('key')
    if not key:
        raise RuntimeError('No MINIMAX_API_KEY env var and no key in config')
    return base + '/v1/messages', key

def translate(title, body, max_chars=8000):
    endpoint, key = get_endpoint_and_key()
    prompt = (
        'Translate this Japanese news article to natural English. '
        'Return ONLY a JSON object with two keys: title_en, body_en. '
        'No markdown, no code fences, no preamble.\n\n'
        'TITLE:\n' + (title or '')[:1000] + '\n\n'
        'BODY:\n' + (body or '')[:max_chars]
    )
    payload = {
        'model': MODEL,
        'max_tokens': 4096,
        'temperature': 0.2,
        'messages': [{'role': 'user', 'content': prompt}],
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'x-api-key': key,
            'anthropic-version': '2023-06-01',
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode('utf-8'))
    blocks = result.get('content', [])
    text = ''.join(b.get('text', '') for b in blocks if b.get('type') == 'text').strip()
    if text.startswith('```'):
        text = '\n'.join(text.splitlines()[1:])
        if text.endswith('```'):
            text = text[:-3].rstrip()
    return json.loads(text)
