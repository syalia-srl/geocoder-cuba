#!/bin/bash
# Weekly update script for Cuba Geocoder
# Runs via cron: 0 3 * * 0 /opt/geocoder/scripts/weekly_update.sh

set -euo pipefail

# Configuration
GEOCODER_DIR="/opt/geocoder"
VENV_DIR="$GEOCODER_DIR/venv"
LOG_FILE="$GEOCODER_DIR/logs/weekly_update.log"
PBF_URL="https://download.geofabrik.de/central-america/cuba-latest.osm.pbf"
MAX_LOG_LINES=10000

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" | tee -a "$LOG_FILE" >&2
}

log_success() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] SUCCESS: $*" | tee -a "$LOG_FILE"
}

log_warning() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARNING: $*" | tee -a "$LOG_FILE"
}

log_error() {
    log "${RED}ERROR: $1${NC}"
}

log_success() {
    log "${GREEN}$1${NC}"
}

log_warning() {
    log "${YELLOW}$1${NC}"
}

# Ensure directories exist
mkdir -p "$GEOCODER_DIR/data/versions"
mkdir -p "$GEOCODER_DIR/logs"

# Rotate log if too large
if [[ -f "$LOG_FILE" ]]; then
    lines=$(wc -l < "$LOG_FILE")
    if [[ $lines -gt $MAX_LOG_LINES ]]; then
        tail -n $MAX_LOG_LINES "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
        log "Rotated log file (was $lines lines)"
    fi
fi

log "=== Starting weekly update ==="

# Activate virtual environment
if [[ -f "$VENV_DIR/bin/activate" ]]; then
    source "$VENV_DIR/bin/activate"
else
    log_error "Virtual environment not found at $VENV_DIR"
    exit 1
fi

# Change to project directory
cd "$GEOCODER_DIR"

# Step 1: Download new PBF
log "Step 1: Downloading PBF from Geofabrik..."
if python -m geocoder.cli.main download --force; then
    log_success "PBF downloaded successfully"
else
    log_error "PBF download failed"
    exit 1
fi

# Step 2: Build database (full Cuba)
log "Step 2: Building database (full Cuba)..."
if python -m geocoder.cli.main build --full-cuba; then
    log_success "Database built successfully"
else
    log_error "Database build failed"
    exit 1
fi

# Step 3: Verify database
log "Step 3: Verifying database..."
stats=$(python -m geocoder.cli.main stats 2>/dev/null || echo "FAILED")
if [[ "$stats" != "FAILED" ]]; then
    log_success "Database stats: $stats"
else
    log_error "Database verification failed"
    exit 1
fi

# Step 4: Run quick sanity check
log "Step 4: Running sanity check..."
if python -m geocoder.cli.main search "Calle 23 415" --json 2>/dev/null | grep -q "Calle 23"; then
    log_success "Sanity check passed"
else
    log_warning "Sanity check returned unexpected results"
fi

# Step 5: Restart service (systemd)
log "Step 5: Restarting geocoder service..."
if systemctl reload geocoder-api 2>/dev/null; then
    log_success "Service reloaded"
elif systemctl restart geocoder-api 2>/dev/null; then
    log_success "Service restarted"
else
    log_warning "Could not restart service (may need manual restart)"
fi

# Step 6: Health check
log "Step 6: Health check..."
sleep 5  # Wait for service to be ready
if curl -sf http://localhost:8000/api/v1/health >/dev/null; then
    log_success "Health check passed"
else
    log_error "Health check failed - service may not be responding"
    exit 1
fi

log_success "=== Weekly update completed successfully ==="

# Notify (optional: email, Slack, etc.)
# echo "Geocoder weekly update completed" | mail -s "Geocoder Update" admin@example.com

exit 0