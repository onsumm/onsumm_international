"""
Is this bill's supplier a Nova-mapped vendor at all? Matches a raw
dim_suppliers.name (e.g. "Signzy Technologies Pvt. Ltd.") against
nova_vendor_mapping.json's vendors{} catalog keys (e.g. "Signzy") — the same
canonical vendor names algorithm.py's certified_pairs()/reconcile() expect.

Deliberately conservative: a normalized containment match only (canonical
name in normalized supplier name), not a fuzzy score — a false positive here
means the orchestrator calls the live Nova API and reconciles a bill that has
nothing to do with Nova, so precision matters more than recall. "GMO
GlobalSign Certificate Services Private Limited" correctly does NOT match any
of Signzy/Ignosis/Scienaptic/etc.
"""
from __future__ import annotations

import re
from typing import Optional


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\b(pvt|private|ltd|limited|technologies|technology|inc|corp|co)\b\.?", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def match_nova_vendor(supplier_name: str, mapping: dict) -> Optional[str]:
    """Returns the canonical vendor key from mapping["vendors"] (e.g. "Signzy"),
    or None if this supplier isn't a Nova vendor."""
    if not supplier_name:
        return None
    normalized_supplier = _normalize(supplier_name)
    if not normalized_supplier:
        return None

    for vendor_key in mapping.get("vendors", {}):
        if vendor_key == "_note":
            continue
        normalized_vendor = _normalize(vendor_key)
        if normalized_vendor and normalized_vendor in normalized_supplier:
            return vendor_key
    return None
