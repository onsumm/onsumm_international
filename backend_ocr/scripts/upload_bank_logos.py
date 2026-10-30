"""
Upload the bank symbols to the environment's S3 bucket.

Run from repo root on the server, in the checkout whose .env has S3_BUCKET set
(prod: ~/app, dev: ~/app-dev):

    python3 backend_ocr/scripts/upload_bank_logos.py            # dry run: lists what would go up
    python3 backend_ocr/scripts/upload_bank_logos.py --apply    # upload to static/bank-logos/<CODE>.png

Re-runnable: each file is overwritten with the same bytes. The files in
backend_ocr/assets/bank_logos stay where they are as the backup the API
serves when S3 can't. Nothing is made public; objects get the bucket's
default (private) access, like every other stored file.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend_ocr.app.config import S3_BUCKET  # noqa: E402
from backend_ocr.app.core import object_store  # noqa: E402
from backend_ocr.app.routers.bank_logos import LOGO_DIR, S3_PREFIX, available_codes  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="upload (default is a dry run)")
    args = ap.parse_args()
    if not object_store.enabled():
        print("S3_BUCKET is not set in this checkout's .env; nothing to upload to.")
        return 1
    codes = available_codes()
    print(f"{len(codes)} bank symbols -> s3://{S3_BUCKET}/{S3_PREFIX}/")
    for code in codes:
        key = f"{S3_PREFIX}/{code}.png"
        if args.apply:
            object_store.save(key, (LOGO_DIR / f"{code}.png").read_bytes(), "image/png", local_backup=False)
            print("  uploaded", key)
        else:
            print("  would upload", key)
    if not args.apply:
        print("Dry run. Re-run with --apply to upload.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
