"""
Loads and caches lendix/nova_vendor_mapping.json — NOVA_RECONCILIATION_LOGIC.md §2.1.

The mapping JSON has a different lifecycle than this service's deploys (finance-edited,
rare, reviewed — see §1), so it's read from disk and hot-reloaded on mtime change rather
than baked in at build time.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

_DEFAULT_PATH = Path(__file__).resolve().parents[3] / "lendix" / "nova_vendor_mapping.json"
MAPPING_PATH = Path(os.getenv("NOVA_VENDOR_MAPPING_PATH", str(_DEFAULT_PATH)))

_cache: Optional[dict] = None
_cache_mtime: Optional[float] = None


def load() -> dict:
    global _cache, _cache_mtime
    mtime = MAPPING_PATH.stat().st_mtime
    if _cache is None or mtime != _cache_mtime:
        with open(MAPPING_PATH, "r", encoding="utf-8") as f:
            _cache = json.load(f)
        _cache_mtime = mtime
    return _cache
