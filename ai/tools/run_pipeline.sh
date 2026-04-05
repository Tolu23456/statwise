#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# StatWise Data Pipeline Runner
# Downloads all datasets then cleans and merges them by year.
# Designed to run quietly in the background — all output is
# timestamped and goes to console + ai/data/pipeline.log
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AI_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$AI_DIR/build"
RAW_DIR="$AI_DIR/data/raw"
CLEAN_DIR="$AI_DIR/data/clean"
LOG_FILE="$AI_DIR/data/pipeline.log"

FORCE=""
SKIP_DOWNLOAD=""
SKIP_CLEAN=""
WORKERS=2

for arg in "$@"; do
    case $arg in
        --force)         FORCE="--force" ;;
        --skip-download) SKIP_DOWNLOAD=1 ;;
        --skip-clean)    SKIP_CLEAN=1 ;;
        --workers=*)     WORKERS="${arg#*=}" ;;
        --verbose)       VERBOSE="--verbose" ;;
    esac
done
VERBOSE="${VERBOSE:-}"

log() {
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[$ts] $*" | tee -a "$LOG_FILE"
}

# ─── Build binaries if not present (or if source is newer) ────────────────────
build_if_needed() {
    local bin="$BUILD_DIR/$1"
    local src="$SCRIPT_DIR/$2"
    if [[ ! -x "$bin" ]] || [[ "$src" -nt "$bin" ]]; then
        log "Building $1…"
        mkdir -p "$BUILD_DIR"
        g++ -std=c++17 -O2 -march=native -Wall -pthread \
            -o "$bin" "$src" -pthread
        log "Built $bin ✓"
    else
        log "$1 is up to date ✓"
    fi
}

mkdir -p "$RAW_DIR" "$CLEAN_DIR"
: > "$LOG_FILE"  # truncate log at start of new run

log "═══════════════════════════════════════════════════════"
log " StatWise Data Pipeline"
log " Workers     : $WORKERS"
log " Force       : ${FORCE:-no}"
log " Raw dir     : $RAW_DIR"
log " Clean dir   : $CLEAN_DIR"
log "═══════════════════════════════════════════════════════"

# ─── Build tools ──────────────────────────────────────────────────────────────
build_if_needed "dataset_downloader" "dataset_downloader.cpp"
build_if_needed "dataset_cleaner"    "dataset_cleaner.cpp"

# ─── Phase 1: Download ────────────────────────────────────────────────────────
if [[ -z "$SKIP_DOWNLOAD" ]]; then
    log "── Phase 1: Downloading datasets ──────────────────────"
    # Run downloader with nice -n 10 so it doesn't compete with the app
    nice -n 10 "$BUILD_DIR/dataset_downloader" "$RAW_DIR" $FORCE \
        2>&1 | tee -a "$LOG_FILE"
    log "── Phase 1 complete ───────────────────────────────────"
else
    log "── Phase 1: SKIPPED (--skip-download) ─────────────────"
fi

# ─── Phase 2: Clean & merge ───────────────────────────────────────────────────
if [[ -z "$SKIP_CLEAN" ]]; then
    log "── Phase 2: Cleaning & merging by year ────────────────"
    # Run cleaner with reduced niceness — it's CPU-bounded
    nice -n 10 "$BUILD_DIR/dataset_cleaner" \
        "$RAW_DIR" "$CLEAN_DIR" \
        --workers "$WORKERS" \
        $VERBOSE \
        2>&1 | tee -a "$LOG_FILE"
    log "── Phase 2 complete ───────────────────────────────────"
else
    log "── Phase 2: SKIPPED (--skip-clean) ────────────────────"
fi

# ─── Summary ──────────────────────────────────────────────────────────────────
log "═══════════════════════════════════════════════════════"
log " Pipeline finished. Clean files:"
ls -lh "$CLEAN_DIR"/*.csv 2>/dev/null | awk '{print "  " $5 "  " $9}' | tee -a "$LOG_FILE" || true
log "═══════════════════════════════════════════════════════"
