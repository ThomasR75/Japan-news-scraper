#!/bin/bash
# Japan News daily pipeline orchestrator.
# Invoked by systemd unit japan-news-pipeline.timer at 20:00 UTC (05:00 JST).
# Steps follow PIPELINE_CHECKLIST.md: scrape (non-Nikkei + Nikkei),
# translate any missing English fields via MiniMax, render two HTML digests,
# send both to Telegram.
#
# Rebuilt 2026-06-22 after the workspace rm — entry point was never on GitHub.
# Scripts moved out of ~/bin/ into the workspace per the session migration.

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

WORKSPACE_ROOT="$(cd .. && pwd)"
NIKKEI_DIR="${WORKSPACE_ROOT}/nikkei_scraper"
LOG_DIR="$PWD/logs"
mkdir -p "$LOG_DIR"

DATE_JST="$(TZ=Asia/Tokyo date +%F)"
LOG="$LOG_DIR/pipeline_${DATE_JST}.log"
exec > >(tee -a "$LOG") 2>&1

echo "==== Japan News pipeline $(date -u +%FT%TZ) (JST date $DATE_JST) ===="

# Scrapers may partially fail — log the error but continue. Translator and
# digest steps abort the pipeline on failure (no point sending an empty doc).
run_scraper() {
    local label="$1"; shift
    echo "--- $label ---"
    if "$@"; then
        echo "$label OK"
    else
        echo "$label FAILED (continuing)"
    fi
}

run_strict() {
    local label="$1"; shift
    echo "--- $label ---"
    if ! "$@"; then
        echo "$label FAILED — aborting pipeline"
        exit 1
    fi
}

# 1. Non-Nikkei sources (~18 outlets via requests/RSS/Lightpanda).
run_scraper "scraper_non_nikkei.py"  python3 scraper_non_nikkei.py

# 2. Nikkei sections (6, via the standalone scraper subproject).
run_scraper "nikkei_scraper.py"       python3 "$NIKKEI_DIR/nikkei_scraper.py"

# 3. MiniMax translates any article that's still JP-only.
run_strict  "translate_minimax.py"    python3 translate_minimax.py

# 4-5. Render the two digests (non-Nikkei + Nikkei).
run_strict  "generate_html non-nikkei" python3 generate_html.py --exclude-nikkei
run_strict  "generate_html nikkei"     python3 generate_html.py --nikkei-only

# Nikkei digest gets mirrored to digests/ for send_digests.sh.
mkdir -p digests
NIKKEI_SRC="data/reports/daily_digest_nikkei_${DATE_JST}.html"
NIKKEI_DST="digests/nikkei_daily_${DATE_JST}.html"
if [ -e "$NIKKEI_SRC" ]; then
    cp "$NIKKEI_SRC" "$NIKKEI_DST"
    echo "mirrored Nikkei digest to $NIKKEI_DST"
else
    echo "WARN: $NIKKEI_SRC not found — skipping digests/ mirror"
fi

# 6. Telegram delivery — send_digests.sh is in ~/bin/ (survived the rm).
run_strict  "send_digests.sh" /home/blablom/bin/send_digests.sh "$DATE_JST"

# Post-run cleanup: prune raw article archives older than 3 days.
python3 cleanup_archive.py || true

echo "==== Done $(date -u +%FT%TZ) ===="
