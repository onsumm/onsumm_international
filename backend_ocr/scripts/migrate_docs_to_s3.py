"""
One-time copy of existing documents into the environment's S3 bucket.

Run from repo root, on the server that holds the files (prod: ~/app, dev:
~/app-dev — each checkout's disk has only the files uploaded through it), with
that checkout's .env (S3_BUCKET set) loaded:

    python3 backend_ocr/scripts/migrate_docs_to_s3.py                 # dry run: counts only
    python3 backend_ocr/scripts/migrate_docs_to_s3.py --apply         # copy to S3, repoint rows

What is copied:
    raw_invoice_uploads.file_data  -> invoices/<tenant>/<upload_id>/<file_name>   (storage_path)
    bill_attachments.file_path     -> bill-attachments/<tenant>/<invoice_id>/<name> (file_path)
    vendor_documents.file_path     -> vendor-docs/<tenant>/<supplier_id>/<doc_id>   (file_path)
    brands.logo_path               -> brand-logos/<tenant>/<name>                   (logo_path)

Nothing is deleted: the original stays where it is as the backup. Invoices
keep their file_data bytes (the app falls back to them if S3 fails); for the
others the local file is also copied to stored_objects/<key>, the backup path
the app reads when S3 fails, and the original file is left untouched. Each
object is checked in S3 (size matches) before its row is repointed, so a
failure leaves the row reading the original.

Re-runnable: rows already holding an s3:// ref are skipped.
"""
import argparse
import mimetypes
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.config import get_conn, put_conn
from app.core import object_store


def _verified(ref: str, size: int) -> bool:
    bucket, k = object_store._split(ref)
    try:
        return object_store._s3().head_object(Bucket=bucket, Key=k)["ContentLength"] == size
    except Exception:
        return False


def _put(object_key: str, data: bytes, content_type, local_backup: bool = True) -> str | None:
    ref = object_store.save(object_key, data, content_type, local_backup=local_backup)
    return ref if _verified(ref, len(data)) else None


def migrate_invoices(apply: bool) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT upload_id::text, tenant_id::text, file_name FROM raw_invoice_uploads
                            WHERE file_data IS NOT NULL AND COALESCE(storage_path, '') NOT LIKE 's3://%%'
                            ORDER BY created_at""")
            todo = cur.fetchall()
        print(f"invoices: {len(todo)} PDF(s) held only in the DB")
        moved = failed = 0
        for upload_id, tenant_id, file_name in todo if apply else []:
            with conn.cursor() as cur:
                cur.execute("SELECT file_data FROM raw_invoice_uploads WHERE upload_id=%s", (upload_id,))
                data = bytes(cur.fetchone()[0])
                ref = _put(object_store.key("invoices", tenant_id, upload_id, file_name or "invoice.pdf"),
                           data, "application/pdf", local_backup=False)
                if not ref:
                    failed += 1
                    print(f"  FAILED {upload_id}")
                    continue
                cur.execute("UPDATE raw_invoice_uploads SET storage_path=%s WHERE upload_id=%s", (ref, upload_id))
            conn.commit()
            moved += 1
        if apply:
            print(f"  copied {moved}, failed {failed}")
    finally:
        put_conn(conn)


def _migrate_paths(label: str, select_sql: str, update_sql: str, key_for, apply: bool) -> None:
    """Rows of (id, tenant_id, path, ...extra, is_deleted) whose path is a
    local file. A soft-deleted row is copied to S3 too, but gets no disk backup."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(select_sql)
            rows = cur.fetchall()
        print(f"{label}: {len(rows)} on local disk")
        moved = missing = failed = 0
        for row in rows if apply else []:
            row_id, tenant_id, path = row[0], row[1], row[2]
            data = object_store.load(path)
            if data is None:
                missing += 1
                print(f"  not on this server: {row_id} ({path})")
                continue
            object_key, content_type = key_for(row)
            ref = _put(object_key, data, content_type, local_backup=not row[-1])
            if not ref:
                failed += 1
                print(f"  FAILED {row_id}")
                continue
            with conn.cursor() as cur:
                cur.execute(update_sql, (ref, row_id))
            conn.commit()
            moved += 1
        if apply:
            print(f"  copied {moved}, not on this server {missing}, failed {failed}")
    finally:
        put_conn(conn)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="copy and repoint (default: dry run)")
    args = ap.parse_args()

    if not object_store.enabled():
        sys.exit("S3_BUCKET is not set for this checkout; nothing to migrate to.")
    print(f"bucket: {object_store.S3_BUCKET}" + ("" if args.apply else "   (dry run)"))

    migrate_invoices(args.apply)

    _migrate_paths(
        "bill attachments",
        """SELECT id::text, tenant_id::text, file_path, invoice_id::text, file_name, file_type,
                  deleted_at IS NOT NULL
             FROM bill_attachments WHERE file_path IS NOT NULL AND file_path NOT LIKE 's3://%%'""",
        "UPDATE bill_attachments SET file_path=%s WHERE id=%s",
        lambda r: (object_store.key("bill-attachments", r[1], r[3], os.path.basename(r[2])), r[5]),
        args.apply,
    )
    _migrate_paths(
        "vendor documents",
        """SELECT id::text, tenant_id::text, file_path, supplier_id::text, file_name,
                  deleted_at IS NOT NULL
             FROM vendor_documents WHERE file_path IS NOT NULL AND file_path NOT LIKE 's3://%%'""",
        "UPDATE vendor_documents SET file_path=%s WHERE id=%s",
        lambda r: (object_store.key("vendor-docs", r[1], r[3] or "unlinked", r[0]),
                   mimetypes.guess_type(r[4] or "")[0]),
        args.apply,
    )
    _migrate_paths(
        "brand logos",
        """SELECT id::text, tenant_id::text, logo_path, false
             FROM brands WHERE logo_path IS NOT NULL AND logo_path NOT LIKE 's3://%%'""",
        "UPDATE brands SET logo_path=%s WHERE id=%s",
        lambda r: (object_store.key("brand-logos", r[1], os.path.basename(r[2])),
                   mimetypes.guess_type(r[2])[0]),
        args.apply,
    )


if __name__ == "__main__":
    main()
