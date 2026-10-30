"""Service licensing + platform admin — DB migration.

Targets BOTH databases. The app applies the same DDL on startup, but a running
process only ever reaches the one database it is configured for, and DB_PROD_URL
is read by no application code at all — so prod needs this script, ideally run
*before* the code deploy so the app boots into a ready schema.

Schema lives in backend_ocr/app/licensing_schema.py and is imported here rather
than copied, so the startup path and this script cannot drift.

Usage:
    python3 scripts/licensing_migrate.py --target dev
    python3 scripts/licensing_migrate.py --target prod --dry-run
    python3 scripts/licensing_migrate.py --target both --seed-catalog --seed-packages
    python3 scripts/licensing_migrate.py --target both --backfill
    python3 scripts/licensing_migrate.py --target dev --bootstrap-admin ops@onsumm.ai

Safe to re-run: all DDL is IF NOT EXISTS / ADD COLUMN IF NOT EXISTS, the catalog
seed is an upsert, and --backfill is guarded by the schema_migrations ledger so it
can only ever apply once per database.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_ROOT / ".env")
sys.path.insert(0, str(_ROOT / "backend_ocr"))

from app.security.licensing_schema import DDL, sync_catalog_sql          # noqa: E402
from app.security.services import SERVICES, sort_order                    # noqa: E402

TARGETS = {"dev": "DB_URL", "prod": "DB_PROD_URL"}

BACKFILL_LABEL = "licensing_backfill_v1"


def _dsn(target: str) -> str:
    var = TARGETS[target]
    url = os.getenv(var)
    if not url:
        sys.exit(f"[{target}] {var} is not set in the environment or .env")
    return url


def _preflight(cur, target: str) -> None:
    """Refuse to touch a database that isn't this product's.

    dim_tenants has no CREATE TABLE anywhere in the repo — it exists only in the
    live databases — and every new table below foreign-keys it. Running against
    the wrong DSN would otherwise fail halfway with a confusing FK error.
    """
    cur.execute("SELECT to_regclass('public.dim_tenants')")
    if cur.fetchone()[0] is None:
        sys.exit(f"[{target}] dim_tenants not found — wrong database?")
    cur.execute("SELECT count(*) FROM dim_tenants")
    print(f"[{target}] connected — dim_tenants rows: {cur.fetchone()[0]}")


def _apply_ddl(cur, target: str) -> int:
    failures = 0
    for label, sql in DDL:
        try:
            cur.execute(sql)
            print(f"[{target}] [OK]   {label}")
        except Exception as exc:                     # noqa: BLE001
            failures += 1
            print(f"[{target}] [FAIL] {label}: {exc}")
    return failures


def _seed_catalog(cur, target: str) -> None:
    upsert, retire = sync_catalog_sql()
    for svc in SERVICES:
        cur.execute(upsert, (
            svc.key, svc.display_name, svc.description, svc.category,
            svc.is_core, svc.default_enabled, svc.metered_unit, sort_order(svc.key),
        ))
    cur.execute(retire, ([s.key for s in SERVICES],))
    print(f"[{target}] [OK]   service_catalog synced ({len(SERVICES)} services)")


# Packages are onboarding prefills. Applying one copies rows into tenant_service;
# nothing resolves through a package at request time.
_PACKAGES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("starter", "Starter", "Core accounts payable.", ()),
    ("growth", "Growth", "Core AP plus tax, FX and vendor verification.",
     ("tds", "fx", "kyc_verification", "data_extraction")),
    ("enterprise", "Enterprise", "Everything, including payments and AI modules.",
     ("tds", "fx", "kyc_verification", "data_extraction", "disbursement",
      "agent_hub", "fact_graph", "anomaly_ai", "tally_connector",
      "accounting_api_sync", "email_sending")),
)


def _seed_packages(cur, target: str) -> None:
    core = [s.key for s in SERVICES if s.is_core]
    for order, (key, name, desc, extras) in enumerate(_PACKAGES):
        cur.execute("""
            INSERT INTO service_package (key, name, description, sort_order)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (key) DO UPDATE
               SET name = EXCLUDED.name, description = EXCLUDED.description,
                   sort_order = EXCLUDED.sort_order
            RETURNING package_id
        """, (key, name, desc, order))
        pkg_id = cur.fetchone()[0]
        for svc_key in list(core) + list(extras):
            cur.execute("""
                INSERT INTO service_package_item (package_id, service_key, enabled)
                VALUES (%s, %s, true)
                ON CONFLICT (package_id, service_key) DO UPDATE SET enabled = true
            """, (pkg_id, svc_key))
        print(f"[{target}] [OK]   package '{key}' ({len(core) + len(extras)} services)")


# ── The one-shot backfill ─────────────────────────────────────────────────────
# Every existing tenant keeps exactly what it has today. This encodes a business
# decision, reads live data, and must run once per database — hence a script and
# the schema_migrations guard, never a startup hook.
_BACKFILL: tuple[tuple[str, str], ...] = (
    ("grant everything currently ungated", """
        INSERT INTO tenant_service (tenant_id, service_key, enabled, enabled_at, notes)
        SELECT t.tenant_id, c.service_key, true, now(), 'backfill: pre-licensing default'
          FROM dim_tenants t
          CROSS JOIN service_catalog c
         WHERE c.service_key NOT IN ('sap_matching', 'gst_tax_verification', 'nova_billing')
        ON CONFLICT (tenant_id, service_key) DO NOTHING
    """),
    ("sap_matching from dim_tenants.sap_matching_enabled", """
        INSERT INTO tenant_service (tenant_id, service_key, enabled, enabled_at, notes)
        SELECT t.tenant_id, 'sap_matching', COALESCE(t.sap_matching_enabled, false), now(),
               'backfill: from dim_tenants.sap_matching_enabled'
          FROM dim_tenants t
        ON CONFLICT (tenant_id, service_key) DO NOTHING
    """),
    # The GST tax-type banner piggybacks on the SAP flag in the UI today. Backfill
    # it identically so nothing changes on the day this ships; decoupling the two
    # is then a deliberate, visible act rather than a side effect of the rollout.
    ("gst_tax_verification inherits the SAP flag", """
        INSERT INTO tenant_service (tenant_id, service_key, enabled, enabled_at, notes)
        SELECT t.tenant_id, 'gst_tax_verification', COALESCE(t.sap_matching_enabled, false), now(),
               'backfill: inherited from sap_matching_enabled - decouple deliberately'
          FROM dim_tenants t
        ON CONFLICT (tenant_id, service_key) DO NOTHING
    """),
    ("nova_billing only where vendor accounts exist", """
        INSERT INTO tenant_service (tenant_id, service_key, enabled, enabled_at, notes)
        SELECT t.tenant_id, 'nova_billing',
               EXISTS (SELECT 1 FROM billing_vendor_accounts va WHERE va.tenant_id = t.tenant_id),
               now(), 'backfill: from billing_vendor_accounts presence'
          FROM dim_tenants t
        ON CONFLICT (tenant_id, service_key) DO NOTHING
    """),
)


def _backfill(cur, target: str) -> None:
    cur.execute("SELECT 1 FROM schema_migrations WHERE label = %s", (BACKFILL_LABEL,))
    if cur.fetchone():
        print(f"[{target}] [SKIP] backfill already applied")
        return
    for label, sql in _BACKFILL:
        cur.execute(sql)
        print(f"[{target}] [OK]   backfill: {label} ({cur.rowcount} rows)")
    cur.execute("INSERT INTO schema_migrations (label) VALUES (%s)", (BACKFILL_LABEL,))

    cur.execute("""
        SELECT service_key, count(*) FILTER (WHERE enabled) AS on_count, count(*) AS total
          FROM tenant_service GROUP BY service_key ORDER BY service_key
    """)
    print(f"\n[{target}] resulting entitlements (verify before enforcing):")
    for key, on_count, total in cur.fetchall():
        print(f"[{target}]     {key:24} {on_count}/{total} tenants")


def _bootstrap_admin(cur, target: str, email: str) -> None:
    """Create the first platform admin. Password is read from a prompt, never argv."""
    import bcrypt

    cur.execute("SELECT 1 FROM platform_users WHERE lower(email) = lower(%s)", (email,))
    if cur.fetchone():
        print(f"[{target}] [SKIP] platform user {email} already exists")
        return
    pw = getpass.getpass(f"[{target}] password for {email}: ")
    if len(pw) < 12:
        sys.exit("password must be at least 12 characters")
    if pw != getpass.getpass("confirm: "):
        sys.exit("passwords do not match")
    cur.execute(
        """INSERT INTO platform_users (email, password_hash, name, role)
           VALUES (%s, %s, %s, 'owner')""",
        (email, bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode(), email.split("@")[0]),
    )
    print(f"[{target}] [OK]   platform owner {email} created")


def run(target: str, args) -> int:
    dsn = _dsn(target)
    conn = psycopg2.connect(dsn, connect_timeout=15)
    conn.autocommit = False
    failures = 0
    try:
        with conn.cursor() as cur:
            _preflight(cur, target)
            failures += _apply_ddl(cur, target)
            if args.seed_catalog:
                _seed_catalog(cur, target)
            if args.seed_packages:
                _seed_packages(cur, target)
            if args.backfill:
                _backfill(cur, target)
            if args.bootstrap_admin:
                _bootstrap_admin(cur, target, args.bootstrap_admin)
        if args.dry_run:
            conn.rollback()
            print(f"[{target}] DRY RUN — rolled back")
        elif failures:
            conn.rollback()
            print(f"[{target}] {failures} statement(s) failed — rolled back")
        else:
            conn.commit()
            print(f"[{target}] committed")
    except Exception as exc:                          # noqa: BLE001
        conn.rollback()
        print(f"[{target}] ABORTED: {exc}")
        failures += 1
    finally:
        conn.close()
    return failures


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    # No default: naming the database you are about to change should be deliberate.
    ap.add_argument("--target", required=True, choices=["dev", "prod", "both"])
    ap.add_argument("--dry-run", action="store_true", help="apply then roll back")
    ap.add_argument("--seed-catalog", action="store_true", help="sync services.SERVICES into service_catalog")
    ap.add_argument("--seed-packages", action="store_true", help="seed the onboarding packages")
    ap.add_argument("--backfill", action="store_true", help="one-shot: grant every tenant what it has today")
    ap.add_argument("--bootstrap-admin", metavar="EMAIL", help="create the first platform owner")
    args = ap.parse_args()

    targets = ["dev", "prod"] if args.target == "both" else [args.target]
    total = 0
    for t in targets:
        print(f"\n{'=' * 60}\n  {t.upper()}  ({TARGETS[t]})\n{'=' * 60}")
        total += run(t, args)
    if total:
        sys.exit(f"\n{total} failure(s)")
    print("\nAll targets OK")


if __name__ == "__main__":
    main()
