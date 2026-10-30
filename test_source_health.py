"""
test_source_health.py — detecting a source that has gone quiet.

    python3 test_source_health.py

The fixtures are verbatim shapes from logs/pipeline_2026-10-30.log, the day
Mainichi (dark 70 nights), Yomiuri Local (dark since June) and Nikkei (dark
4 mornings) were all found by hand. The parser has to read each of the three
print formats, ignore the earlier run blocks that same-day reruns append, and
the streak logic has to speak once, remind rarely, and say when it is over.
"""
from __future__ import annotations

import sys

from source_health import last_run_block, parse_log, render, update

LOG = """\
==== Japan News pipeline 2026-10-29T20:00:50Z (JST date 2026-10-30) ====
--- scraper_non_nikkei.py ---
--- Asahi Politics ---
    Found 15 article links
    +  参政・神谷氏...
  → 10 saved
--- Mainichi Politics ---
    Found 0 article links
  → 0 saved
--- NHK Politics ---
    RSS feed: 108 items
  → 10 saved
--- NHK Economy ---
    RSS returned 503
  → 0 saved
--- Yomiuri Local ---
    Found 0 article links
  → 0 saved, 0 skipped
scraper_non_nikkei.py OK
--- nikkei_scraper.py ---
[Nikkei Markets] fetching https://www.nikkei.com/markets/
  AUTH CHALLENGE PAGE (4559 bytes) — nikkei_cookies.json likely needs refresh
[Nikkei Business] fetching https://www.nikkei.com/business/
[Nikkei Business] saved=10 skipped=5
=== TOTAL: saved=10 skipped=5 ===
nikkei_scraper.py OK
--- translate_minimax.py ---
==== Done 2026-10-29T20:10:13Z ====
"""


def t_parses_every_print_format() -> None:
    c = parse_log(LOG)
    assert c == {"Asahi Politics": 15, "Mainichi Politics": 0, "NHK Politics": 108,
                 "NHK Economy": 0, "Yomiuri Local": 0,
                 "Nikkei Markets": 0, "Nikkei Business": 15}, c
    print("  ✓ requests 'Found', NHK 'RSS feed', Nikkei saved+skipped and AUTH CHALLENGE all read")


def t_step_labels_are_not_sources() -> None:
    """'--- scraper_non_nikkei.py ---' and '--- translate_minimax.py ---' are run.sh steps."""
    c = parse_log(LOG)
    assert not any("." in k for k in c), [k for k in c if "." in k]
    print("  ✓ run.sh step labels are not mistaken for sources")


def t_only_the_last_run_block_counts() -> None:
    """Same-day reruns append; the morning's dead Nikkei must not shadow the fixed rerun."""
    rerun = LOG.replace("  AUTH CHALLENGE PAGE (4559 bytes) — nikkei_cookies.json likely needs refresh",
                        "[Nikkei Markets] saved=10 skipped=3")
    rerun = rerun.replace("2026-10-29T20:00:50Z", "2026-10-29T23:36:27Z")
    c = parse_log(LOG + rerun)
    assert c["Nikkei Markets"] == 13, c["Nikkei Markets"]
    assert last_run_block(LOG + rerun).count("==== Japan News pipeline") == 1
    print("  ✓ only the final run block in an appended log is judged")


def t_a_crashed_scraper_leaves_headers_with_no_count() -> None:
    cut = LOG.split("    Found 0 article links\n  → 0 saved\n--- NHK")[0]  # dies inside Mainichi
    c = parse_log(cut)
    assert c["Mainichi Politics"] == 0 and "NHK Politics" not in c, c
    print("  ✓ a header with no count reads as 0; sources never reached are absent")


# --- streaks -----------------------------------------------------------------

def run_days(zero_sources, days, state=None, counts_ok=("Asahi Politics",)):
    """Simulate `days` runs with the same set of dark sources; return state + all events."""
    state = state or {}
    events = []
    for _ in range(days):
        counts = {s: 0 for s in zero_sources}
        counts.update({s: 15 for s in counts_ok})
        state, alerts, recov = update(state, counts)
        events.append((alerts, recov))
    return state, events


def t_first_alert_at_threshold_not_before() -> None:
    _, ev = run_days(["Mainichi Politics"], 3)
    assert ev[0] == ([], []) and ev[1] == ([], []), ev[:2]
    assert ev[2] == ([("Mainichi Politics", 3)], []), ev[2]
    print("  ✓ silent for two dark runs, alerts on the third")


def t_reminders_are_weekly_not_daily() -> None:
    _, ev = run_days(["Mainichi Politics"], 20)
    alert_days = [i + 1 for i, (a, _) in enumerate(ev) if a]
    assert alert_days == [3, 10, 17], alert_days
    print("  ✓ then a reminder every 7 runs, not every morning")


def t_recovery_announced_once_and_state_dropped() -> None:
    state, _ = run_days(["Mainichi Politics"], 5)
    state, alerts, recov = update(state, {"Mainichi Politics": 15, "Asahi Politics": 15})
    assert recov == [("Mainichi Politics", 5)] and not alerts, (alerts, recov)
    assert state == {}, state
    state, alerts, recov = update(state, {"Mainichi Politics": 15, "Asahi Politics": 15})
    assert (alerts, recov) == ([], []) and state == {}
    print("  ✓ recovery is announced once and the source leaves the state file")


def t_a_blip_recovers_silently() -> None:
    """One or two empty nights (a site outage) must not produce a 'Back' message."""
    state, _ = run_days(["Sankei Economy"], 2)
    state, alerts, recov = update(state, {"Sankei Economy": 15, "Asahi Politics": 15})
    assert (alerts, recov) == ([], []) and state == {}, (alerts, recov, state)
    print("  ✓ a sub-threshold blip comes back without a word")


def t_a_source_missing_from_the_log_counts_as_dark() -> None:
    state, _ = run_days(["Yomiuri Local"], 2)
    state, alerts, _ = update(state, {"Asahi Politics": 15})          # scraper died before Yomiuri
    assert alerts == [("Yomiuri Local", 3)], alerts
    print("  ✓ a known source absent from the run is counted dark, not forgotten")


def t_healthy_sources_carry_no_state() -> None:
    state, _, _ = update({}, {"Asahi Politics": 15, "NHK Politics": 108})
    assert state == {}
    print("  ✓ healthy sources write nothing to the state file")


def t_render_mentions_every_source_and_recovery() -> None:
    msg = render([("Mainichi Politics", 3), ("Yomiuri Local", 3)], [("Nikkei Markets", 4)])
    for needle in ("Mainichi Politics", "Yomiuri Local", "3 runs", "Nikkei Markets", "after 4 dark runs"):
        assert needle in msg, needle
    assert render([], []) == ""
    print("  ✓ the message names each dark source, its run count, and recoveries")


if __name__ == "__main__":
    print("Running source-health tests...")
    failed = False
    for fn in (t_parses_every_print_format,
               t_step_labels_are_not_sources,
               t_only_the_last_run_block_counts,
               t_a_crashed_scraper_leaves_headers_with_no_count,
               t_first_alert_at_threshold_not_before,
               t_reminders_are_weekly_not_daily,
               t_recovery_announced_once_and_state_dropped,
               t_a_blip_recovers_silently,
               t_a_source_missing_from_the_log_counts_as_dark,
               t_healthy_sources_carry_no_state,
               t_render_mentions_every_source_and_recovery):
        try:
            fn()
        except Exception as e:
            print(f"  ✗ {fn.__name__}: {type(e).__name__}: {e}")
            failed = True
    print("FAILED" if failed else "All tests passed.")
    sys.exit(1 if failed else 0)
