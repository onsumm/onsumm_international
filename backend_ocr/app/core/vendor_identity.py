"""Vendor identity rules (PAN derived from GSTIN).

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations


def _effective_pan(gstn: str | None, pan: str | None) -> str | None:
    """A company's PAN. The PAN embedded in a valid 15-char GSTIN (chars 3-12) is
    authoritative — it is checksum-validated — so it wins over the free-form `pan`
    column, which can be stale or wrong. Falls back to the pan column only when
    there is no usable GSTIN. Returns uppercase or None."""
    if gstn and len(gstn.strip()) == 15:
        return gstn.strip()[2:12].upper()
    if pan and pan.strip():
        return pan.strip().upper()
    return None
