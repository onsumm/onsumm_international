#!/usr/bin/env python3
"""Give every vendor a legal name alongside its brand name.

A vendor now has two names:

  dim_suppliers.name        the brand / trading name the business uses
  dim_suppliers.legal_name  the registered name, as the GST portal returns it

Until now there was only `name`, and GST verification overwrote it with the
portal's legal name whenever the two differed. This script fills legal_name for
every existing vendor:

  1. the GST portal's legal name for the vendor's primary GSTIN, when we have
     one (supplier_gstins.legal_name) — the authoritative registered name;
  2. else the legal name the GST portal returned at the vendor's latest
     verification (wf_audit_log.new_values_jsonb ->> 'api_legal_name');
  3. otherwise the vendor's current name, so both names start the same.

It never changes `name`. Vendors whose brand name was overwritten by the portal
are surfaced in Review ("Vendor name was replaced with the GST portal's legal
name"), where the user chooses to restore the brand name — that is a decision,
not something to guess here.

Run before deploying the code that reads legal_name (prod: before the API
restart). Idempotent: only rows with legal_name still NULL are touched, so a
re-run after new vendors arrive fills just those.

    python3 scripts/backfill_vendor_legal_name.py            # apply
    python3 scripts/backfill_vendor_legal_name.py --dry-run  # report only

Reads the database URL from config.py (.env) — no hardcoded credentials.
"""
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402


ADD_COLUMN = "ALTER TABLE dim_suppliers ADD COLUMN IF NOT EXISTS legal_name text"

# Per vendor: the primary GSTIN's portal legal name if there is one, else the
# current name. Only vendors not yet filled.
PLAN = """
    SELECT s.supplier_id,
           s.name,
           COALESCE(NULLIF(TRIM(g.legal_name), ''), NULLIF(TRIM(a.legal), ''), s.name) AS legal_name,
           (COALESCE(NULLIF(TRIM(g.legal_name), ''), NULLIF(TRIM(a.legal), '')) IS NOT NULL) AS from_gst
      FROM dim_suppliers s
      LEFT JOIN LATERAL (
          SELECT sg.legal_name
            FROM supplier_gstins sg
           WHERE sg.supplier_id = s.supplier_id
             AND sg.tenant_id   = s.tenant_id
           ORDER BY sg.is_primary DESC, sg.created_at
           LIMIT 1
      ) g ON TRUE
      LEFT JOIN LATERAL (
          SELECT al.new_values_jsonb ->> 'api_legal_name' AS legal
            FROM wf_audit_log al
           WHERE al.table_name = 'dim_suppliers'
             AND al.entity_id  = s.supplier_id
             AND al.tenant_id  = s.tenant_id
             AND al.new_values_jsonb ? 'api_legal_name'
           ORDER BY al.created_at DESC
           LIMIT 1
      ) a ON TRUE
     WHERE s.legal_name IS NULL
"""


def _connect():
    """Verified TLS first; the self-hosted dev box's certificate is not in the
    RDS bundle, so fall back to encrypted-but-unverified there (same as
    review_hub_migrate.py)."""
    try:
        return psycopg2.connect(
            config.DATABASE_URL, sslmode=config.DB_SSL, sslrootcert=config.DB_SSL_CERT
        )
    except psycopg2.OperationalError as exc:
        if "certificate verify failed" not in str(exc):
            raise
        print("  [note] connecting encrypted but unverified (self-hosted dev box)")
        return psycopg2.connect(config.DATABASE_URL, sslmode="require")


def run(dry_run: bool) -> None:
    conn = _connect()
    conn.autocommit = False
    cur = conn.cursor()
    # Fail fast rather than queue behind a long-held lock on a live table.
    cur.execute("SET lock_timeout = '10s'")
    cur.execute(ADD_COLUMN)

    cur.execute(PLAN)
    rows = cur.fetchall()
    differs = [r for r in rows if r[2] != r[1]]
    print(f"Vendors to fill: {len(rows)}")
    print(f"  legal name from the GST portal: {sum(1 for r in rows if r[3])}")
    print(f"  same as the brand name:         {len(rows) - len(differs)}")
    print(f"  legal name differs from brand:  {len(differs)}")
    for _sid, name, legal, _g in differs[:15]:
        print(f"    {name!r}  →  legal {legal!r}")
    if len(differs) > 15:
        print(f"    … and {len(differs) - 15} more")

    if dry_run:
        conn.rollback()
        print("\nDry run — nothing written.")
        return

    cur.execute(f"""
        UPDATE dim_suppliers s
           SET legal_name = p.legal_name
          FROM ({PLAN}) p
         WHERE s.supplier_id = p.supplier_id
           AND s.legal_name IS NULL
    """)
    written = cur.rowcount
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM dim_suppliers WHERE legal_name IS NULL")
    left = cur.fetchone()[0]
    conn.close()
    print(f"\nFilled {written}. Vendors still without a legal name: {left}.")


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
