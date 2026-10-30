#!/usr/bin/env python3
"""Backfill actor_name for email-link approvals in bill_approval_chain.

Historically, approvals/send-backs done "Via email link" recorded the generic
role label (e.g. "Approver") as actor_name, because the shared email link only
carried the role. This script rewrites those rows to the specific person
assigned to that step in tenant_chain_config — but ONLY when the step has
exactly one assignee, so attribution is unambiguous.

Dry-run by default: prints the rows it WOULD change. Pass --apply to write.

Usage:
    /usr/local/bin/python3.12 scripts/backfill_email_link_actor_name.py
    /usr/local/bin/python3.12 scripts/backfill_email_link_actor_name.py --apply
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend_ocr.app.config import get_conn, put_conn  # noqa: E402

# Generic role labels produced by the old code path (see approve_via_link).
ROLE_LABELS = {"Approver", "Accountant", "Finance Controller"}


def _load_chain_rows(cur, tenant_id, department):
    """Ordered non-locked steps (with assignees) for a tenant+department.

    Falls back to the tenant-wide (department IS NULL) chain when no
    department-specific chain exists — mirrors _load_chain in main.py.
    """
    if department:
        cur.execute(
            "SELECT role, locked, assignees FROM tenant_chain_config "
            "WHERE tenant_id=%s AND department=%s AND is_snapshot=false ORDER BY step_order",
            (tenant_id, department),
        )
        rows = cur.fetchall()
        if rows:
            return [r for r in rows if not r[1]]
    cur.execute(
        "SELECT role, locked, assignees FROM tenant_chain_config "
        "WHERE tenant_id=%s AND department IS NULL AND is_snapshot=false ORDER BY step_order",
        (tenant_id,),
    )
    rows = cur.fetchall()
    return [r for r in rows if not r[1]]


def main(apply: bool, invoice_ids: list[str] | None = None):
    conn = get_conn()
    conn.autocommit = False
    planned = []   # (id, old_name, new_name)
    skipped_multi = 0
    skipped_none = 0
    try:
        with conn.cursor() as cur:
            sql = (
                """SELECT c.id, c.invoice_id, c.tenant_id, c.step, c.actor_name,
                          fi.department
                   FROM bill_approval_chain c
                   LEFT JOIN fact_invoices fi ON fi.invoice_id = c.invoice_id
                   WHERE c.comment = 'Via email link'
                     AND c.action IN ('approve', 'send_back')"""
            )
            params: list = []
            if invoice_ids:
                sql += " AND c.invoice_id = ANY(%s::uuid[])"
                params.append(invoice_ids)
            sql += " ORDER BY c.tenant_id, c.invoice_id, c.step"
            cur.execute(sql, params)
            rows = cur.fetchall()

            # Cache chain lookups per (tenant, department).
            chain_cache = {}
            for row_id, invoice_id, tenant_id, step, actor_name, department in rows:
                if actor_name not in ROLE_LABELS:
                    continue  # already a real name, or unexpected value — leave it
                key = (str(tenant_id), department)
                if key not in chain_cache:
                    chain_cache[key] = _load_chain_rows(cur, str(tenant_id), department)
                steps = chain_cache[key]
                idx = (step or 0) - 1  # stored step is 1-based index into non-locked steps
                if idx < 0 or idx >= len(steps):
                    skipped_none += 1
                    continue
                assignees = steps[idx][2] or []
                assignees = [a.strip() for a in assignees if a and a.strip()]
                if len(assignees) == 1:
                    new_name = assignees[0]
                    if new_name != actor_name:
                        planned.append((row_id, invoice_id, actor_name, new_name))
                elif len(assignees) > 1:
                    skipped_multi += 1
                else:
                    skipped_none += 1

            print(f"Candidate email-link rows scanned: {len(rows)}")
            print(f"Rows to update (single assignee): {len(planned)}")
            print(f"Skipped (multiple assignees, ambiguous): {skipped_multi}")
            print(f"Skipped (no assignee / step out of range): {skipped_none}")
            print()
            for row_id, iid, old, new in planned[:200]:
                print(f"  #{row_id} [{iid}]: {old!r} -> {new!r}")
            if len(planned) > 200:
                print(f"  ... and {len(planned) - 200} more")

            if not apply:
                print("\nDRY RUN — no changes written. Re-run with --apply to commit.")
                conn.rollback()
                return

            for row_id, _iid, _old, new in planned:
                cur.execute(
                    "UPDATE bill_approval_chain SET actor_name=%s WHERE id=%s",
                    (new, row_id),
                )
            conn.commit()
            print(f"\nAPPLIED — updated {len(planned)} rows.")
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


if __name__ == "__main__":
    ids = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(apply="--apply" in sys.argv, invoice_ids=ids or None)
