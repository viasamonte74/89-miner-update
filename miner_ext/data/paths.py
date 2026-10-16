"""Canonical on-disk layout under /workspace (no persistent volume — back up)."""
from __future__ import annotations

import os

WORKSPACE = os.environ.get("WORKSPACE", "/workspace")
DATA_ROOT = os.environ.get("SN89_DATA_ROOT", os.path.join(WORKSPACE, "sn89_data"))
RAW_WINDOWS = os.path.join(DATA_ROOT, "raw_windows")
PARQUET_DIR = os.path.join(DATA_ROOT, "parquet")
MANIFEST_PATH = os.path.join(DATA_ROOT, "manifest.json")
COVERAGE_PATH = os.path.join(DATA_ROOT, "coverage.json")
ARCHIVE_STATE = os.path.join(DATA_ROOT, "archiver_state.json")

HF_PUBLIC_BASE = os.environ.get(
    "SN89_HF_PUBLIC_BASE", "https://partner.infinitequant.app/sn89/hf"
)
USER_AGENT = os.environ.get("SN89_UA", "sn89-miner-ext/0.1 (+research; polite)")


def ensure_dirs() -> None:
    for p in (DATA_ROOT, RAW_WINDOWS, PARQUET_DIR):
        os.makedirs(p, exist_ok=True)
