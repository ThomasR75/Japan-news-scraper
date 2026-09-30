"""
test_minimax_key.py — every MiniMax caller finds the key the same way.

    cd japan_news_scraper && python3 test_minimax_key.py

2026-10-01 05:35 JST: translation succeeded, scoring failed on every batch
with "No MINIMAX_API_KEY env var and no key in config", no ranked digest.
OpenClaw no longer keeps the key in openclaw.json (auth moved into its own
store), so the config branch is dead. The translator survived because
translate_minimax.load_env() reads ~/.config/openclaw/secrets.env into the
environment first; the scorer imports the shared lookup and had no such
step. Until the reboot the systemd user session happened to carry the key,
which hid the gap. The fallback now lives in the shared lookup itself.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import minimax_translate as mt


def _config_without_key(d):
    p = Path(d) / "openclaw.json"
    p.write_text(json.dumps({"models": {"providers": {"minimax": {"baseUrl": "https://api.example/anthropic/"}}},
                             "auth": {"profiles": {"minimax:default": {"provider": "minimax", "mode": "api-key"}}}}))
    return str(p)


def t_key_comes_from_secrets_env_when_env_and_config_lack_it():
    with tempfile.TemporaryDirectory() as d:
        env = Path(d) / "secrets.env"
        env.write_text("# comment\nOTHER=1\nMINIMAX_API_KEY=sk-test-from-file\n")
        saved = (mt.CONFIG, mt.SECRETS_ENV, os.environ.pop("MINIMAX_API_KEY", None))
        try:
            mt.CONFIG, mt.SECRETS_ENV = _config_without_key(d), str(env)
            endpoint, key = mt.get_endpoint_and_key()
        finally:
            mt.CONFIG, mt.SECRETS_ENV = saved[0], saved[1]
            if saved[2] is not None:
                os.environ["MINIMAX_API_KEY"] = saved[2]
        assert key == "sk-test-from-file", "key must come from secrets.env"
        assert endpoint == "https://api.example/anthropic/v1/messages"
    print("  ✓ with no env var and no key in config, the key is read from secrets.env")


def t_env_var_wins_over_the_file():
    with tempfile.TemporaryDirectory() as d:
        env = Path(d) / "secrets.env"
        env.write_text("MINIMAX_API_KEY=sk-file\n")
        saved = (mt.CONFIG, mt.SECRETS_ENV, os.environ.get("MINIMAX_API_KEY"))
        try:
            mt.CONFIG, mt.SECRETS_ENV = _config_without_key(d), str(env)
            os.environ["MINIMAX_API_KEY"] = "sk-env"
            _, key = mt.get_endpoint_and_key()
        finally:
            mt.CONFIG, mt.SECRETS_ENV = saved[0], saved[1]
            if saved[2] is None:
                os.environ.pop("MINIMAX_API_KEY", None)
            else:
                os.environ["MINIMAX_API_KEY"] = saved[2]
        assert key == "sk-env"
    print("  ✓ an explicit env var still wins")


def t_nothing_anywhere_still_raises_with_a_clear_message():
    with tempfile.TemporaryDirectory() as d:
        saved = (mt.CONFIG, mt.SECRETS_ENV, os.environ.pop("MINIMAX_API_KEY", None))
        try:
            mt.CONFIG, mt.SECRETS_ENV = _config_without_key(d), str(Path(d) / "absent.env")
            try:
                mt.get_endpoint_and_key()
                raise AssertionError("expected RuntimeError")
            except RuntimeError as e:
                assert mt.SECRETS_ENV in str(e) and "config" in str(e), str(e)
        finally:
            mt.CONFIG, mt.SECRETS_ENV = saved[0], saved[1]
            if saved[2] is not None:
                os.environ["MINIMAX_API_KEY"] = saved[2]
    print("  ✓ no key anywhere raises, and the message names all three places it looked")


if __name__ == "__main__":
    print("Running minimax-key tests...")
    failed = False
    for fn in (t_key_comes_from_secrets_env_when_env_and_config_lack_it, t_env_var_wins_over_the_file,
               t_nothing_anywhere_still_raises_with_a_clear_message):
        try:
            fn()
        except Exception as e:
            print(f"  ✗ {fn.__name__}: {type(e).__name__}: {e}")
            failed = True
    print("FAILED" if failed else "All tests passed.")
    sys.exit(1 if failed else 0)
