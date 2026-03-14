#!/usr/bin/env bash
# =============================================================================
# mobiPartner: Local scraping pipeline
# Runs all 3 spiders + post-processing against Supabase
#
# Usage:
#   ./scripts/run_local_pipeline.sh              # run everything
#   ./scripts/run_local_pipeline.sh zonaprop     # run single spider + postprocess
#
# Requires:
#   - .env.local in project root with DATABASE_URL pointing to Supabase
#   - Python venv with scrapy, playwright, and backend deps installed
#   - Firefox installed via: playwright install firefox
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
SCRAPERS_DIR="$BACKEND_DIR/scrapers"

# Load env vars (DATABASE_URL, etc.)
if [ -f "$PROJECT_ROOT/.env.local" ]; then
    set -a
    source "$PROJECT_ROOT/.env.local"
    set +a
elif [ -f "$BACKEND_DIR/.env" ]; then
    set -a
    source "$BACKEND_DIR/.env"
    set +a
fi

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set. Create .env.local with your Supabase DATABASE_URL"
    exit 1
fi

export PYTHONPATH="$BACKEND_DIR"

SPIDERS=("zonaprop" "argenprop" "mercadolibre")

# If a specific spider was requested
if [ "${1:-}" != "" ]; then
    SPIDERS=("$1")
fi

echo "============================================"
echo " mobiPartner Local Pipeline"
echo " $(date)"
echo " DB: ${DATABASE_URL:0:40}..."
echo "============================================"

ERRORS=0

for spider in "${SPIDERS[@]}"; do
    echo ""
    echo "--- Scraping: $spider ---"
    START=$(date +%s)

    if (cd "$SCRAPERS_DIR" && python3 -m scrapy crawl "$spider" 2>&1); then
        ELAPSED=$(( $(date +%s) - START ))
        echo "--- $spider completed (${ELAPSED}s) ---"
    else
        ELAPSED=$(( $(date +%s) - START ))
        echo "--- $spider FAILED (${ELAPSED}s) ---"
        ERRORS=$((ERRORS + 1))
    fi
done

echo ""
echo "--- Post-processing ---"
python3 "$BACKEND_DIR/scripts/run_postprocess.py"

echo ""
echo "============================================"
if [ $ERRORS -gt 0 ]; then
    echo " Pipeline finished with $ERRORS error(s)"
else
    echo " Pipeline finished successfully!"
fi
echo "============================================"
