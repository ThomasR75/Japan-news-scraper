"""
test_nas_archive.py — the NAS copy must never be able to hang the pipeline.

    cd japan_news_scraper && python3 test_nas_archive.py

2026-09-30 (05:00 JST run): translation finished in 14 minutes, then
generate_html sat for 69 minutes inside `shutil.copy2(..., /mnt/botsaves/...)`
until the box was power-cycled. Nothing was delivered. The NAS had stopped
answering nine hours earlier, and /mnt/botsaves is a HARD NFS mount: a dead
server does not raise, it blocks. `try/except` cannot catch a call that never
returns, so the copy runs in a child process with a deadline, and the digest
is already on local disk before it starts.

The blocking destination is simulated with a FIFO: opening it for writing
blocks until a reader appears, which is what the dead mount did.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

from nas_archive import archive_to_nas


def _src(d, name="digest.html", body="<html>ok</html>"):
    p = Path(d) / name
    p.write_text(body)
    return p


def t_copies_when_the_destination_is_healthy():
    with tempfile.TemporaryDirectory() as d:
        src = _src(d)
        dest = Path(d) / "nas" / "japan_news"          # does not exist yet
        ok = archive_to_nas([(src, "daily_digest_2026-09-30.html"), (src, "daily_digest_today.html")], dest, timeout=5)
        assert ok is True
        assert (dest / "daily_digest_2026-09-30.html").read_text() == "<html>ok</html>"
        assert (dest / "daily_digest_today.html").exists()
    print("  ✓ a healthy destination gets both files, directory created")


def t_a_blocking_destination_returns_false_within_the_deadline():
    with tempfile.TemporaryDirectory() as d:
        src = _src(d)
        dest = Path(d) / "nas"
        dest.mkdir()
        os.mkfifo(dest / "daily_digest_today.html")     # writing here blocks forever
        t0 = time.monotonic()
        ok = archive_to_nas([(src, "daily_digest_today.html")], dest, timeout=1)
        took = time.monotonic() - t0
        assert ok is False, "a copy that never finished must not be reported as done"
        assert took < 3, f"returned after {took:.1f}s; the deadline was 1s"
    print("  ✓ a destination that blocks costs the deadline, not the pipeline")


def t_an_unwritable_destination_returns_false_and_does_not_raise():
    with tempfile.TemporaryDirectory() as d:
        src = _src(d)
        ok = archive_to_nas([(src, "x.html")], Path("/proc/definitely/not/writable"), timeout=5)
        assert ok is False
    print("  ✓ an unwritable destination is a False, never an exception")


def t_generate_html_never_touches_the_nas_in_process():
    src = (Path(__file__).parent / "generate_html.py").read_text()
    assert "archive_to_nas(" in src, "generate_html must archive through the bounded helper"
    for banned in ("BOTSaves_DIR.mkdir", "shutil.copy2(output_path, BOTSaves_DIR", "shutil.copy2(today_path, BOTSaves_DIR"):
        assert banned not in src, f"in-process NAS access is back: {banned}"
    print("  ✓ generate_html has no in-process write to /mnt/botsaves")


if __name__ == "__main__":
    print("Running nas-archive tests...")
    failed = False
    for fn in (t_copies_when_the_destination_is_healthy,
               t_a_blocking_destination_returns_false_within_the_deadline,
               t_an_unwritable_destination_returns_false_and_does_not_raise,
               t_generate_html_never_touches_the_nas_in_process):
        try:
            fn()
        except Exception as e:
            print(f"  ✗ {fn.__name__}: {type(e).__name__}: {e}")
            failed = True
    print("FAILED" if failed else "All tests passed.")
    sys.exit(1 if failed else 0)
