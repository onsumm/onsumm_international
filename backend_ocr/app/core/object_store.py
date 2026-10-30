"""Durable document storage in S3.

Every document the app keeps — invoice PDFs, bill attachments, vendor
documents, agent-drafted bills, brand logos — is written here. One bucket per
environment, picked by
`S3_BUCKET`: `onsumm-assets` for prod, `onsumm-assets-dev` for dev. Credentials
come from boto3's default chain (the EC2 instance role, or AWS_* env vars).

A stored document is referred to by a *ref*, the string saved in the row's
path column:

    s3://onsumm-assets/vendor-docs/<tenant>/<supplier>/<doc_id>

S3 is the primary copy, never the only one. `save` also writes a backup to
backend_ocr/stored_objects/<key> (invoices skip it: their backup is the
raw_invoice_uploads.file_data column), and `load` falls back to that backup when
S3 errors or has lost the object. Nothing in the app deletes from S3: a user's
delete soft-deletes the row and drops only the disk copy (`drop_local_copy`).

Rows written before this module existed hold an absolute local path (or, for
invoices, bytes in file_data). `load` reads either kind of ref, and
scripts/migrate_docs_to_s3.py copies them to S3 leaving the originals in place.

With `S3_BUCKET` unset (a laptop with no AWS access) `save` writes under
backend_ocr/stored_objects/ and returns that path — the same shape of ref a
legacy row has, so every reader works the same way in both modes.

Keys always start `<kind>/<tenant_id>/`, so a tenant's documents can be listed,
exported or deleted as one prefix.

No document ever has a public URL. The bucket blocks all public access, and
the browser reaches a file only through `signed_url`: after the API has checked
the caller's tenant it issues a link to that one object that expires in
SIGNED_URL_TTL seconds — CloudFront-signed when the CDN is configured, else
S3-presigned. Without a valid signature both CloudFront and S3 refuse.
"""
from __future__ import annotations

import json
import logging
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..config import (CLOUDFRONT_KEY_ID, CLOUDFRONT_PRIVATE_KEY_PATH, FILES_CDN_DOMAIN, S3_BUCKET,
                      S3_REGION, SIGNED_URL_TTL)
from .storage import BACKEND_ROOT

log = logging.getLogger(__name__)

# Where `save` writes the backup copy (or the only copy, with S3 off).
LOCAL_ROOT = BACKEND_ROOT / "stored_objects"

_client = None


def enabled() -> bool:
    return bool(S3_BUCKET)


def _s3():
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config
        _client = boto3.client(
            "s3",
            region_name=S3_REGION,
            config=Config(retries={"max_attempts": 5, "mode": "standard"},
                          connect_timeout=5, read_timeout=60),
        )
    return _client


def _part(value) -> str:
    """One path segment of a key. Client-supplied names can carry slashes or
    `..`; neither may reach a key, nor — in local mode — a filesystem path."""
    s = str(value or "").replace("\\", "/").split("/")[-1].strip()
    return s if s not in ("", ".", "..") else "_"


def key(kind: str, tenant_id, *parts) -> str:
    return "/".join([kind, _part(tenant_id), *(_part(p) for p in parts)])


def is_s3(ref) -> bool:
    return isinstance(ref, str) and ref.startswith("s3://")


def _split(ref: str) -> tuple[str, str]:
    bucket, _, k = ref[len("s3://"):].partition("/")
    return bucket, k


def _write_backup(object_key: str, data: bytes) -> Path:
    dest = LOCAL_ROOT / object_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def _backup_of(ref: str) -> Path:
    return LOCAL_ROOT / _split(ref)[1]


def save(object_key: str, data: bytes, content_type: str | None = None,
         local_backup: bool = True) -> str:
    """Store `data` under `object_key` and return its ref. The local backup is
    written first, so a failed S3 upload still leaves the bytes on disk."""
    if not enabled():
        return str(_write_backup(object_key, data))
    if local_backup:
        _write_backup(object_key, data)
    extra = {"ServerSideEncryption": "AES256"}
    if content_type:
        extra["ContentType"] = content_type
    _s3().put_object(Bucket=S3_BUCKET, Key=object_key, Body=data, **extra)
    return f"s3://{S3_BUCKET}/{object_key}"


def load(ref) -> bytes | None:
    """The stored bytes, or None when the ref is empty or the object is gone."""
    if not ref:
        return None
    if is_s3(ref):
        from botocore.exceptions import ClientError
        bucket, k = _split(ref)
        try:
            return _s3().get_object(Bucket=bucket, Key=k)["Body"].read()
        except Exception as e:
            backup = _backup_of(ref)
            if backup.is_file():
                log.warning("S3 read of %s failed (%s); serving the local backup", ref, e)
                return backup.read_bytes()
            if isinstance(e, ClientError) and e.response.get("Error", {}).get("Code") in (
                    "NoSuchKey", "404", "NoSuchBucket"):
                return None
            raise
    p = Path(ref)
    return p.read_bytes() if p.is_file() else None


def replace(ref, data: bytes, content_type: str | None = None) -> None:
    """Overwrite the object a ref points at (an edited attachment). The bucket
    is versioned, so the previous bytes stay recoverable."""
    if is_s3(ref):
        bucket, k = _split(ref)
        _write_backup(k, data)
        extra = {"ServerSideEncryption": "AES256"}
        if content_type:
            extra["ContentType"] = content_type
        _s3().put_object(Bucket=bucket, Key=k, Body=data, **extra)
    else:
        Path(ref).write_bytes(data)


def drop_local_copy(ref) -> None:
    """A user deleted the document (its row is soft-deleted): the disk copy
    goes, the S3 object stays as the record. Nothing in the app deletes from
    S3. A pre-S3 row's file is its only copy, so it is kept for the backfill.
    Best effort — failing here must not fail the request."""
    if not is_s3(ref):
        return
    try:
        _backup_of(ref).unlink(missing_ok=True)
    except Exception:
        log.warning("could not remove local copy of %s", ref, exc_info=True)


@contextmanager
def local_path(ref, suffix: str = "") -> Iterator[Path]:
    """A filesystem path holding the document, for code that can only read a
    file (pymupdf). An S3 object is downloaded to a temp file for the duration."""
    if not is_s3(ref):
        yield Path(ref)
        return
    data = load(ref)
    if data is None:
        raise FileNotFoundError(ref)
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(data)
        tmp.flush()
        yield Path(tmp.name)


def invoice_pdf(cur, upload_id, tenant_id=None) -> bytes | None:
    """An uploaded invoice's PDF: from S3 when the row has a ref there, else
    from the legacy file_data column. `cur` is a plain (tuple) cursor."""
    sql = "SELECT storage_path, file_data FROM raw_invoice_uploads WHERE upload_id = %s"
    args = [upload_id]
    if tenant_id:
        sql += " AND tenant_id = %s"
        args.append(tenant_id)
    cur.execute(sql, args)
    row = cur.fetchone()
    if not row:
        return None
    storage_path, file_data = row
    if is_s3(storage_path):
        data = load(storage_path)
        if data:
            return data
    return bytes(file_data) if file_data else None


# ── Signed links ─────────────────────────────────────────────────────────────

# The content type the API answers `?signed=1` with, so the browser can tell a
# link apart from a document (a stored JSON file is application/json).
SIGNED_LINK_TYPE = "application/vnd.onsumm.signed-link+json"

_cf_signer = None


def _cloudfront_signer():
    global _cf_signer
    if _cf_signer is None:
        from botocore.signers import CloudFrontSigner
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        key = serialization.load_pem_private_key(Path(CLOUDFRONT_PRIVATE_KEY_PATH).read_bytes(), password=None)
        # CloudFront verifies RSA-SHA1 signatures; this is its required scheme.
        _cf_signer = CloudFrontSigner(CLOUDFRONT_KEY_ID,
                                      lambda msg: key.sign(msg, padding.PKCS1v15(), hashes.SHA1()))
    return _cf_signer


def _cdn_enabled() -> bool:
    return bool(FILES_CDN_DOMAIN and CLOUDFRONT_KEY_ID and CLOUDFRONT_PRIVATE_KEY_PATH)


def signed_url(ref) -> str | None:
    """A short-lived link to one stored object, or None for a document not in
    S3 (a pre-S3 row), which the API then streams itself. Call only after the
    caller's access to the document has been checked."""
    if not is_s3(ref):
        return None
    bucket, k = _split(ref)
    if _cdn_enabled() and bucket == S3_BUCKET:
        from urllib.parse import quote
        url = f"https://{FILES_CDN_DOMAIN}/{quote(k)}"
        return _cloudfront_signer().generate_presigned_url(
            url, date_less_than=_utc_in(SIGNED_URL_TTL))
    return _s3().generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": k}, ExpiresIn=SIGNED_URL_TTL)


def _utc_in(seconds: int):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(time.time() + seconds, tz=timezone.utc)


def signed_link_response(ref):
    """The `?signed=1` answer for a document in S3, or None to stream it —
    including when signing fails, so a CDN misconfiguration degrades to the
    API serving the file rather than to an error."""
    try:
        url = signed_url(ref)
    except Exception:
        log.exception("could not sign a link for %s; streaming it instead", ref)
        url = None
    if not url:
        return None
    from fastapi.responses import Response
    return Response(content=json.dumps({"url": url, "expires_in": SIGNED_URL_TTL}),
                    media_type=SIGNED_LINK_TYPE,
                    headers={"Cache-Control": "no-store"})
