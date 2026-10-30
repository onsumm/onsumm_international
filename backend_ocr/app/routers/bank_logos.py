"""Bank symbols for the vendor card: GET /api/bank-logos and /api/bank-logos/{code}.png.

The symbols (no wordmark, transparent PNG) are keyed by the IFSC bank code,
the first four letters of an IFSC (HDFC0001721 → HDFC). They live in the
environment's S3 bucket under static/bank-logos/, uploaded once by
scripts/upload_bank_logos.py, and the copy in backend_ocr/assets/bank_logos is
the backup: it's served whenever S3 is off, unreachable or missing the file,
and it's also the list of which banks have a symbol.

Nothing here is public. The bucket stays Block-Public-Access and the bytes go
through this logged-in endpoint like every other stored file.
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Response

from ..config import S3_BUCKET
from ..core import object_store
from ..core.storage import BACKEND_ROOT
from ..security.auth import require_tenant

log = logging.getLogger(__name__)

router = APIRouter()

LOGO_DIR = BACKEND_ROOT / "assets" / "bank_logos"
S3_PREFIX = "static/bank-logos"
_CODE = re.compile(r"^[A-Z]{4}$")


def available_codes() -> list[str]:
    return sorted(p.stem for p in LOGO_DIR.glob("*.png") if _CODE.match(p.stem))


def logo_bytes(code: str) -> bytes | None:
    """S3 first, then the bundled backup; None when the bank has no symbol."""
    if object_store.enabled():
        try:
            data = object_store.load(f"s3://{S3_BUCKET}/{S3_PREFIX}/{code}.png")
            if data:
                return data
        except Exception as e:  # S3 down or misconfigured: the backup still serves
            log.warning("bank logo %s: S3 read failed (%s); serving the bundled copy", code, e)
    p = LOGO_DIR / f"{code}.png"
    return p.read_bytes() if p.is_file() else None


@router.get("/api/bank-logos")
def list_bank_logos():
    require_tenant()
    return {"codes": available_codes()}


@router.get("/api/bank-logos/{code}.png")
def get_bank_logo(code: str):
    require_tenant()
    code = code.upper()
    if not _CODE.match(code):
        raise HTTPException(status_code=404, detail="No logo for this bank")
    data = logo_bytes(code)
    if not data:
        raise HTTPException(status_code=404, detail="No logo for this bank")
    # The same image for everyone and it rarely changes: let the browser keep it.
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "private, max-age=2592000"})
