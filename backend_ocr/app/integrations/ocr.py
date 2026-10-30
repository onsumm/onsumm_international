"""
OCR module for the FastAPI upload server.
Delegates all logic to the shared utils/ocr_engine module so the OCR
implementation lives in one place and is shared with the pipeline worker.
"""
import os
import sys

# Allow import of the shared pipeline utilities when uvicorn runs from backend_ocr/.
# Anchored on core.storage rather than counted out with dirname(): this module has
# moved package depth once already, and the count silently pointed one level short.
from ..core.storage import PROJECT_ROOT

_PIPELINE_ROOT = str(PROJECT_ROOT)
if _PIPELINE_ROOT not in sys.path:
    sys.path.insert(0, _PIPELINE_ROOT)

from utils.ocr_engine import (   # noqa: F401  (re-exported for callers)
    PdfTooLongError,
    SYSTEM_PROMPT,
    TEXT_SYSTEM_PROMPT,
    _call_text,
    _call_vision,
    _encode_page,
    _pdf_to_text,
    _text_layer_ok,
    check_pdf_pages,
    invoice_upload_id,
    process_image_async,
    process_pdf,
    process_pdf_async,
)


def enforce_pdf_page_limit(data: bytes, filename: str = "") -> None:
    """check_pdf_pages for an upload route: an over-long PDF becomes a 413."""
    from fastapi import HTTPException
    try:
        check_pdf_pages(data, filename)
    except PdfTooLongError as e:
        raise HTTPException(status_code=413, detail=str(e))


# A photo or screenshot of a bill is read the same way a scanned PDF is: turned
# into a one-page PDF first, so storage, the bill viewer and OCR see one format.
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def is_invoice_file(filename: str) -> bool:
    return filename.lower().endswith((".pdf",) + IMAGE_EXTS)


def as_pdf(data: bytes, filename: str) -> tuple[bytes, str]:
    """The upload as PDF bytes and its stored name; a PDF passes through."""
    if not filename.lower().endswith(IMAGE_EXTS):
        return data, filename
    import io
    from pathlib import Path
    from PIL import Image, ImageOps
    from fastapi import HTTPException
    try:
        img = Image.open(io.BytesIO(data))
        # Phone photos are stored sideways with a rotation tag; apply it.
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail=f"{filename} is not a readable image")
    out = io.BytesIO()
    img.save(out, format="PDF", resolution=150)
    return out.getvalue(), Path(filename).stem + ".pdf"
