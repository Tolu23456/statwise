#!/usr/bin/env bash
# StatWise Data Pipeline v3.0
# Runs dataset_downloader then dataset_cleaner sequentially.
# Designed to run as a Replit workflow (console output type).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BINDIR="$ROOT/build"
RAW="$ROOT/ai/data/raw"
CLEAN="$ROOT/ai/data/clean"

echo "╔══════════════════════════════════════════════════════════╗"
echo "║         StatWise Data Pipeline v3.0                     ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo "[pipeline] Root     : $ROOT"
echo "[pipeline] Raw dir  : $RAW"
echo "[pipeline] Clean dir: $CLEAN"
echo "[pipeline] Started  : $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# ── verify binaries ────────────────────────────────────────────────────────────
if [ ! -x "$BINDIR/dataset_downloader" ] || [ ! -x "$BINDIR/dataset_cleaner" ]; then
    echo "[pipeline] ERROR: binaries missing — rebuilding…"
    cd "$ROOT/ai/tools" && make all
fi

# ── create raw subdirectories ─────────────────────────────────────────────────
for d in xgabora football_data understat international worldcup openfootball statsbomb; do
    mkdir -p "$RAW/$d"
done
mkdir -p "$CLEAN"

# ── phase 1: download ──────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════════"
echo " PHASE 1 — Downloading raw data (1,198 tasks, skips existing)"
echo "══════════════════════════════════════════════════════════════"
echo "[pipeline] Running downloader — this will take a while."
echo "[pipeline] Already-downloaded files are skipped automatically."
echo "[pipeline] Press Ctrl+C to stop gracefully and resume later."
echo ""

"$BINDIR/dataset_downloader" "$RAW"
DL_EXIT=$?

echo ""
if [ $DL_EXIT -eq 0 ]; then
    echo "[pipeline] Download phase completed successfully."
else
    echo "[pipeline] Download phase finished with some failures (exit=$DL_EXIT)."
    echo "[pipeline] This is normal — many season/league combos are optional."
fi

# ── phase 2: clean ─────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════════"
echo " PHASE 2 — Cleaning and merging to YYYY_matches.csv"
echo "══════════════════════════════════════════════════════════════"
echo ""

"$BINDIR/dataset_cleaner" "$RAW" "$CLEAN"
CL_EXIT=$?

# ── summary ───────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════════"
echo " PIPELINE COMPLETE"
echo "══════════════════════════════════════════════════════════════"
echo "[pipeline] Finished: $(date '+%Y-%m-%d %H:%M:%S')"
echo "[pipeline] Downloader exit : $DL_EXIT"
echo "[pipeline] Cleaner exit    : $CL_EXIT"
echo ""
echo "[pipeline] Output files:"
if ls "$CLEAN"/*.csv 1>/dev/null 2>&1; then
    wc -l "$CLEAN"/*.csv | tail -5
    echo "  ...  (run: wc -l $CLEAN/*.csv  to see all)"
else
    echo "  No clean files found — check logs above."
fi
echo ""
echo "[pipeline] Next step: run 'Train Model' workflow to retrain the AI."
echo "══════════════════════════════════════════════════════════════"
