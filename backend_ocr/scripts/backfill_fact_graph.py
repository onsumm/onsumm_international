"""
One-time backfill: build the tenant fact knowledge graph from existing data.

Run from repo root:
    python3 backend_ocr/scripts/backfill_fact_graph.py --all --concurrency 5
    python3 backend_ocr/scripts/backfill_fact_graph.py --tenant d0e00000-0000-0000-0000-000000000003

Per tenant (each processed fully independently — the graph is strictly
tenant-isolated, so no node/edge ever spans two tenants):
    1. vendors + their contacts   (enrich_vendor)
    2. invoices                    (enrich_bill)
    3. outbound emails from vendor_outreach_log -> poc_email_messages -> enrich_email

CAVEAT: historical *inbound* vendor emails were fetched from Gmail and discarded,
so they are NOT in any table and cannot be backfilled. Only outbound history is
available for old data; inbound coverage begins going forward via the live poller
hook. Re-runnable: all upserts are idempotent (content-hash / ON CONFLICT).

Bounded parallelism (asyncio.Semaphore around asyncio.to_thread) keeps OpenAI
concurrency in check. Finishes with a cross-tenant-edge assertion (must be 0).
"""
import argparse
import asyncio
import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from psycopg2.extras import RealDictCursor
from app.config import get_conn, put_conn
from app.domain import fact_graph


def _all_tenants() -> list:
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT tenant_id::text AS tid FROM dim_tenants ORDER BY tenant_id")
        return [r["tid"] for r in cur.fetchall()]
    finally:
        put_conn(conn)


def _ids(sql, params) -> list:
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        put_conn(conn)


def _backfill_outbound_emails(tenant_id: str) -> list:
    """Copy vendor_outreach_log history into poc_email_messages (outbound),
    returning the message_ids that need enrichment."""
    conn = get_conn()
    ids = []
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT supplier_id::text, email_to, subject, body, thread_id, message_id, requested_at "
            "FROM vendor_outreach_log WHERE tenant_id=%s AND status='sent' AND body IS NOT NULL",
            (tenant_id,),
        )
        rows = cur.fetchall()
        for r in rows:
            cur.execute("""
                INSERT INTO poc_email_messages
                    (tenant_id, supplier_id, direction, gmail_msg_id, thread_id,
                     to_email, subject, body, sent_at)
                VALUES (%s,%s,'outbound',%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_id, gmail_msg_id, direction) WHERE gmail_msg_id IS NOT NULL
                DO UPDATE SET body=EXCLUDED.body
                RETURNING message_id::text
            """, (tenant_id, r["supplier_id"], r["message_id"], r["thread_id"],
                  r["email_to"], r["subject"], r["body"], r["requested_at"]))
            got = cur.fetchone()
            if got:
                ids.append(got["message_id"])
        conn.commit()
    except Exception:
        conn.rollback()
        traceback.print_exc()
    finally:
        put_conn(conn)
    return ids


async def _run_bounded(sem, fn, *args):
    async with sem:
        try:
            await asyncio.to_thread(fn, *args)
            return True
        except Exception:
            traceback.print_exc()
            return False


async def backfill_tenant(tenant_id, concurrency, limit, structural):
    print(f"\n=== Tenant {tenant_id} ===")
    sem = asyncio.Semaphore(concurrency)
    # structural mode: deterministic nodes/edges only — NO gpt-4o-mini, NO embeddings.
    # (Facts for old data are added separately using Claude Code's own reasoning.)
    kw = dict(do_facts=not structural, do_embed=not structural)

    vendors = _ids(
        "SELECT supplier_id::text AS id FROM dim_suppliers WHERE tenant_id=%s" +
        (" LIMIT %s" if limit else ""),
        (tenant_id, limit) if limit else (tenant_id,),
    )
    print(f"  vendors: {len(vendors)}")
    await asyncio.gather(*[
        _run_bounded(sem, lambda i=v["id"]: fact_graph.enrich_vendor(tenant_id, i, **kw)) for v in vendors
    ], return_exceptions=True)

    bills = _ids(
        "SELECT invoice_id::text AS id FROM fact_invoices WHERE tenant_id=%s ORDER BY invoice_date DESC" +
        (" LIMIT %s" if limit else ""),
        (tenant_id, limit) if limit else (tenant_id,),
    )
    print(f"  bills: {len(bills)}")
    await asyncio.gather(*[
        _run_bounded(sem, lambda i=b["id"]: fact_graph.enrich_bill(tenant_id, i, **kw)) for b in bills
    ], return_exceptions=True)

    if not structural:
        email_ids = _backfill_outbound_emails(tenant_id)
        print(f"  outbound emails: {len(email_ids)}")
        await asyncio.gather(*[
            _run_bounded(sem, fact_graph.enrich_email, tenant_id, mid) for mid in email_ids
        ], return_exceptions=True)


def _assert_isolation():
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT count(*) AS n FROM fact_graph_edges e "
            "JOIN fact_graph_nodes s ON e.src_node_id=s.node_id "
            "JOIN fact_graph_nodes d ON e.dst_node_id=d.node_id "
            "WHERE s.tenant_id <> d.tenant_id"
        )
        n = cur.fetchone()["n"]
        if n:
            print(f"\n!! ISOLATION VIOLATION: {n} cross-tenant edges found")
        else:
            print("\nOK: 0 cross-tenant edges (tenant isolation holds)")
    finally:
        put_conn(conn)


def _counts():
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT node_type, count(*) AS n FROM fact_graph_nodes GROUP BY node_type ORDER BY node_type")
        print("\nNode counts:")
        for r in cur.fetchall():
            print(f"  {r['node_type']:8s} {r['n']}")
        cur.execute("SELECT count(*) AS n FROM fact_graph_edges")
        print(f"Edges: {cur.fetchone()['n']}")
    finally:
        put_conn(conn)


async def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--tenant", help="single tenant UUID")
    g.add_argument("--all", action="store_true", help="all tenants (each isolated)")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--limit", type=int, default=None, help="cap vendors/bills per tenant (testing)")
    ap.add_argument("--structural", action="store_true",
                    help="deterministic nodes/edges only — no gpt-4o-mini, no embeddings")
    ap.add_argument("--truncate", action="store_true",
                    help="wipe fact_graph_nodes/edges before building (fresh rebuild)")
    args = ap.parse_args()

    fact_graph.init_fact_graph_schema()
    if args.truncate:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("TRUNCATE fact_graph_edges, fact_graph_nodes")
            conn.commit()
            print("Truncated fact_graph_nodes + fact_graph_edges.")
        finally:
            put_conn(conn)

    tenants = _all_tenants() if args.all else [args.tenant]
    mode = "structural (no LLM/embeddings)" if args.structural else "full (gpt-4o-mini + embeddings)"
    print(f"Backfilling {len(tenants)} tenant(s), concurrency={args.concurrency}, mode={mode}")

    for tid in tenants:
        try:
            await backfill_tenant(tid, args.concurrency, args.limit, args.structural)
        except Exception:
            print(f"  ERROR backfilling tenant {tid}:")
            traceback.print_exc()

    _counts()
    _assert_isolation()


if __name__ == "__main__":
    asyncio.run(main())
