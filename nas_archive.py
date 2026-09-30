"""
nas_archive.py — copy files to the NAS without ever being able to hang on it.

/mnt/botsaves is a HARD NFS mount over Tailscale. When the NAS stops
answering, a write to it does not fail, it blocks — and no try/except can
catch a call that never returns. On 2026-09-30 that held generate_html for
69 minutes after translation had finished, and nothing was delivered.

So every touch of the NAS (the mkdir included) happens in a child process
with a deadline. On expiry the child's process group is killed and NOT
waited for. The archive copy is a convenience; the digest is already on
local disk and the pipeline goes on to deliver it either way.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys

DEFAULT_TIMEOUT = 20    # seconds. A dead NAS costs this once per call, nothing more.

_SCRIPT = ('d="$1"; shift; mkdir -p "$d" || exit 1; '
           'while [ $# -gt 0 ]; do cp -p "$1" "$d/$2" || exit 1; shift 2; done')


def archive_to_nas(pairs, dest_dir, timeout=DEFAULT_TIMEOUT):
    """Copy each (src_path, dest_name) into dest_dir. True only if every copy
    finished inside the deadline. Never raises, never blocks past `timeout`."""
    args = []
    for src, name in pairs:
        args += [str(src), str(name)]
    try:
        p = subprocess.Popen(["sh", "-c", _SCRIPT, "sh", str(dest_dir), *args],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        print(f"NAS archive skipped: {e}", file=sys.stderr)
        return False
    try:
        rc = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        print(f"NAS archive TIMED OUT after {timeout}s writing to {dest_dir} — "
              f"is the NAS reachable? Continuing without the archive copy.", file=sys.stderr)
        return False
    if rc != 0:
        print(f"NAS archive failed (exit {rc}) writing to {dest_dir}", file=sys.stderr)
        return False
    return True
