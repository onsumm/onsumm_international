"""Recover the GST detail for bills ingested before it was kept.

The CGST/SGST/IGST split, the rates, the taxable value and the place of supply.

Extraction has always read every component and its rate; ingest added them up,
wrote the sum to stg_bills.tax_amount and dropped the parts. The parts were
never actually lost — `_insert_ocr_result` stores the whole extraction payload
in raw_ocr_results.raw_response_jsonb, which nothing has ever read back.

So this is a recovery, not a re-extraction: no OCR runs, no model is called,
nothing is re-read from a PDF. It copies what is already in the database into
the columns the bill detail now serves from.

Only fills blanks — every column is written through COALESCE — so it can never
overwrite a value someone corrected by hand, and it is safe to run as many
times as you like. Re-running after a new column is added fills just that
column for bills an earlier run already touched.

    python3 scripts/backfill_gst_breakdown.py --dry-run
    python3 scripts/backfill_gst_breakdown.py
    python3 scripts/backfill_gst_breakdown.py --tenant <uuid>

Reads DATABASE_URL from config.py (.env) — no hardcoded credentials.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402


def _connect():
    try:
        return psycopg2.connect(
            config.DATABASE_URL, sslmode=config.DB_SSL, sslrootcert=config.DB_SSL_CERT
        )
    except psycopg2.OperationalError as exc:
        if "certificate verify failed" not in str(exc):
            raise
        print("  [note] server certificate is not in the RDS bundle — connecting "
              "encrypted but unverified (expected on the self-hosted dev box)")
        return psycopg2.connect(config.DATABASE_URL, sslmode="require")


# The newest extraction per upload: a bill re-uploaded after a correction has
# more than one row, and the latest is the one ingest acted on.
FIND_SQL = """
    SELECT b.stg_bill_id, b.bill_number, b.tax_amount,
           o.raw_response_jsonb -> 'taxes' AS taxes,
           o.raw_response_jsonb ->> 'place_of_supply' AS place_of_supply
      FROM stg_bills b
      JOIN LATERAL (
            SELECT r.raw_response_jsonb
              FROM raw_ocr_results r
             WHERE r.upload_id = b.upload_id
             ORDER BY r.ocr_id DESC
             LIMIT 1
      ) o ON TRUE
     WHERE b.upload_id IS NOT NULL
       AND (    (b.cgst_amount IS NULL AND b.sgst_amount IS NULL AND b.igst_amount IS NULL)
             OR b.place_of_supply IS NULL )
       AND (%(tenant)s IS NULL OR b.tenant_id = %(tenant)s::uuid)
       AND o.raw_response_jsonb ? 'taxes'
"""

UPDATE_SQL = """
    UPDATE stg_bills
       SET taxable_amount = COALESCE(taxable_amount, %(taxable)s),
           cgst_amount = COALESCE(cgst_amount, %(cgst)s),
           sgst_amount = COALESCE(sgst_amount, %(sgst)s),
           igst_amount = COALESCE(igst_amount, %(igst)s),
           cess_amount = COALESCE(cess_amount, %(cess)s),
           cgst_rate = COALESCE(cgst_rate, %(cgst_rate)s),
           sgst_rate = COALESCE(sgst_rate, %(sgst_rate)s),
           igst_rate = COALESCE(igst_rate, %(igst_rate)s),
           place_of_supply = COALESCE(place_of_supply, %(pos)s)
     WHERE stg_bill_id = %(id)s
"""


def _num(v):
    """The payload is model output: numbers arrive as numbers, as strings with
    commas, and occasionally as empty strings."""
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", help="restrict to one tenant uuid")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be filled and write nothing")
    args = ap.parse_args()

    conn = _connect()
    conn.autocommit = False
    filled = skipped = mismatched = 0

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(FIND_SQL, {"tenant": args.tenant})
            rows = cur.fetchall()
            print(f"{len(rows)} bill(s) missing some GST detail, with an extraction to read\n")

            for r in rows:
                taxes = r["taxes"] or {}
                parts = {k: _num(taxes.get(f"{k}_amount")) for k in ("cgst", "sgst", "igst", "cess")}

                pos = (r["place_of_supply"] or "").strip() or None

                if not any(parts.values()) and not pos:
                    # The extraction recorded no components either — there is
                    # nothing to recover, and writing zeroes would assert a
                    # split that was never read.
                    skipped += 1
                    continue

                total = sum(v for v in parts.values() if v)
                stated = _num(r["tax_amount"])
                if stated is not None and abs(total - stated) > 1:
                    # The bill's tax total has been edited since. Still worth
                    # storing — the split is true of the document, and the API
                    # marks it as not reconciling — but worth counting out loud.
                    mismatched += 1

                if not args.dry_run:
                    cur.execute(UPDATE_SQL, {
                        "id": r["stg_bill_id"],
                        "taxable": _num(taxes.get("taxable_amount")),
                        "cgst": parts["cgst"], "sgst": parts["sgst"],
                        "igst": parts["igst"], "cess": parts["cess"],
                        "pos": pos,
                        "cgst_rate": _num(taxes.get("cgst_rate")),
                        "sgst_rate": _num(taxes.get("sgst_rate")),
                        "igst_rate": _num(taxes.get("igst_rate")),
                    })
                filled += 1

        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    verb = "would fill" if args.dry_run else "filled"
    print(f"{verb} {filled}")
    print(f"skipped {skipped} (the extraction recorded nothing to recover)")
    if mismatched:
        print(f"of those filled, {mismatched} no longer add up to the bill's tax "
              f"total — the total was edited after upload. The bill detail says "
              f"so rather than hiding it.")


if __name__ == "__main__":
    main()
