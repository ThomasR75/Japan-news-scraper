"""
test_translate_parallel.py — the translate step must actually run in parallel.

    cd japan_news_scraper && python3 test_translate_parallel.py

On 2026-09-29 the pipeline was killed by systemd at the 60-minute mark, 190 of
292 articles into a sequential translate loop at ~20 s per article. Nothing was
delivered. The loop had been fine for two months only because the Nikkei
cookies were dead and there was nothing to translate; the day they were fixed,
the workload tripled and the timer's ceiling was still sized for the old world.

The fix is a worker pool. The point of these tests is not that the pool code
exists — it is that N articles take ~N/workers of the sequential time, that
every file still gets its own atomic write, and that one article giving up
does not take the others with it.
"""
import json, os, tempfile, time, sys

import translate_minimax as tm

# ---- fake API ---------------------------------------------------------------

def fake_translate(delay, fail_titles=()):
    def _t(title, body):
        time.sleep(delay)
        if title in fail_titles:
            raise ConnectionError('simulated outage')
        return {'title_en': 'EN ' + title, 'body_en': 'EN ' + body}
    return _t


def make_archive(n):
    base = tempfile.mkdtemp(prefix='tp_')
    sec = os.path.join(base, 'Nikkei_Economy')
    os.makedirs(sec)
    todo = []
    for i in range(n):
        f = os.path.join(sec, 'a%02d.json' % i)
        json.dump({'title': 'T%02d' % i, 'extracted_text': 'B%02d' % i},
                  open(f, 'w', encoding='utf-8'), ensure_ascii=False)
        todo.append(('Nikkei_Economy', f))
    return base, todo


# ---- tests ------------------------------------------------------------------

def t_one_article_is_translated_and_saved():
    base, todo = make_archive(1)
    tm.translate = fake_translate(0.0)
    tm.BACKOFF = 0
    status, section, name, err = tm.translate_one(*todo[0])
    d = json.load(open(todo[0][1], encoding='utf-8'))
    assert status == 'ok', (status, err)
    assert d['title_en'] == 'EN T00' and d['translated_text'] == 'EN B00', d
    assert d['translated_by'] == tm.MODEL and d.get('translated_at'), d
    assert not os.path.exists(todo[0][1] + '.tmp'), 'tmp file left behind'
    print('  ✓ one article: translated fields written, atomic tmp cleaned up')


def t_a_give_up_leaves_the_file_untouched():
    base, todo = make_archive(1)
    tm.translate = fake_translate(0.0, fail_titles=('T00',))
    tm.BACKOFF = 0
    before = open(todo[0][1], encoding='utf-8').read()
    status, section, name, err = tm.translate_one(*todo[0])
    assert status == 'err' and 'ConnectionError' in err, (status, err)
    assert open(todo[0][1], encoding='utf-8').read() == before, 'file was modified on failure'
    print('  ✓ an article that gives up is reported and its file is left alone')


def t_pool_is_actually_parallel():
    """8 articles at 0.3 s each: sequential is 2.4 s, four workers ~0.6 s."""
    base, todo = make_archive(8)
    tm.translate = fake_translate(0.3)
    tm.BACKOFF = 0
    t0 = time.time()
    done, errors, counts = tm.run(todo, workers=4, quiet=True)
    elapsed = time.time() - t0
    assert done == 8 and errors == 0, (done, errors)
    assert elapsed < 1.5, 'took %.2fs — that is sequential, not parallel' % elapsed
    for _, f in todo:
        assert json.load(open(f, encoding='utf-8')).get('translated_text'), f
    print('  ✓ 8 x 0.3s articles finished in %.2fs on 4 workers (sequential would be 2.4s)' % elapsed)


def t_one_failure_does_not_sink_the_batch():
    base, todo = make_archive(6)
    tm.translate = fake_translate(0.05, fail_titles=('T02',))
    tm.BACKOFF = 0
    done, errors, counts = tm.run(todo, workers=3, quiet=True)
    assert done == 5 and errors == 1, (done, errors)
    ok = [f for _, f in todo if json.load(open(f, encoding='utf-8')).get('translated_text')]
    assert len(ok) == 5, len(ok)
    print('  ✓ one article giving up costs exactly one article, not the batch')


def t_workers_one_still_works():
    """The sequential path must remain a valid configuration, not a special case."""
    base, todo = make_archive(3)
    tm.translate = fake_translate(0.0)
    done, errors, counts = tm.run(todo, workers=1, quiet=True)
    assert done == 3 and errors == 0
    assert counts == {'Nikkei_Economy': 3}, counts
    print('  ✓ workers=1 degrades to the old sequential behaviour')


if __name__ == '__main__':
    print('Running parallel-translate tests...')
    failed = False
    for fn in (t_one_article_is_translated_and_saved,
               t_a_give_up_leaves_the_file_untouched,
               t_pool_is_actually_parallel,
               t_one_failure_does_not_sink_the_batch,
               t_workers_one_still_works):
        try:
            fn()
        except Exception as e:
            print('  ✗ %s: %s: %s' % (fn.__name__, type(e).__name__, e))
            failed = True
    print('FAILED' if failed else 'All tests passed.')
    sys.exit(1 if failed else 0)
