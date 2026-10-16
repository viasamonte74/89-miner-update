#!/usr/bin/env bash
# Off-box backup helper. NEVER include wallets, mnemonics, tokens, or .env secrets.
# Usage: DEST=user@host:/path ./miner_ext/ops/backup.sh
set -euo pipefail
WORKSPACE="${WORKSPACE:-/workspace}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="${DEST:-}"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BUNDLE="$WORKSPACE/backups/sn89_${STAMP}"
mkdir -p "$BUNDLE"

echo "packing (no secrets) → $BUNDLE"
rsync -a --exclude 'wallets' --exclude '*.pem' --exclude 'id_*' \
  "$WORKSPACE/sn89_data/" "$BUNDLE/sn89_data/" 2>/dev/null || mkdir -p "$BUNDLE/sn89_data"
rsync -a "$ROOT/miner_ext/models/" "$BUNDLE/models/" 2>/dev/null || true
rsync -a "$ROOT/miner_ext/results/" "$BUNDLE/results/" 2>/dev/null || true
cp -a "$ROOT/miner_ext/PHASE0.md" "$BUNDLE/" 2>/dev/null || true

if [[ -n "$DEST" ]]; then
  echo "rsync to $DEST"
  rsync -a "$BUNDLE/" "$DEST/sn89_${STAMP}/"
else
  echo "DEST not set — local bundle only at $BUNDLE"
  echo "Set DEST=user@host:/path and re-run to ship off-box."
fi
