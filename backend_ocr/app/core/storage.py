"""Filesystem roots for uploaded documents.

Every path the app writes to is anchored here, on one explicit constant, rather
than counted out with `Path(__file__).parent.parent` at each use site. That
counting is what breaks when a module moves between packages: the four roots
below were each written relative to app/main.py, and all four would have pointed
one directory too shallow once they moved into app/core/.

These are process-local directories on the instance's own disk. They are not
shared between the two uvicorn workers' hosts, survive no instance replacement,
and are not backed up — see the storage finding in the scalability audit; the
durable fix is object storage keyed by tenant and upload id.
"""
from __future__ import annotations

from pathlib import Path

# app/core/storage.py -> app/core -> app -> backend_ocr
BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Persistent upload storage next to the app package.
UPLOADS_DIR = BACKEND_ROOT / "stored_uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

# Files attached to a bill, under the upload root.
ATTACH_DIR = UPLOADS_DIR / "attachments"
ATTACH_DIR.mkdir(exist_ok=True)

# Documents held against a vendor (agreements, certificates).
VENDOR_DOCS_DIR = BACKEND_ROOT / "vendor_docs"
VENDOR_DOCS_DIR.mkdir(exist_ok=True)

# Tenant brand logos served to the UI.
BRAND_LOGOS_DIR = BACKEND_ROOT / "brand_logos"
BRAND_LOGOS_DIR.mkdir(exist_ok=True)

# The repository root — one level above backend_ocr. The settings router adds
# this to sys.path to import the pipeline-side `config` module.
PROJECT_ROOT = BACKEND_ROOT.parent
