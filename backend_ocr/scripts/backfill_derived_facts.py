"""
Derived-fact backfill for EXISTING data — built by Claude Code's own reasoning,
NOT gpt-4o-mini. (gpt-4o-mini is reserved for enriching NEW bills/emails via the
live hooks.)

Old data has almost no free-text fact source: inbound vendor emails were never
stored, and the outbound history is placeholder/test messages. So instead of
fabricating facts, this encodes the genuinely durable, high-signal payment
constraints a Payable agent must honour, derived deterministically from real,
stable vendor attributes:

  1. KYB payment gate  — vendors whose KYB status is FAILED or UNVERIFIED *and*
     who are owed money. A hard "do not disburse until verified" constraint.
  2. Non-standard terms — vendors on payment terms other than the net-30 default.

Deliberately NOT turned into facts (would be noise or go stale):
  - net-30 default terms (349 vendors, identical) — already a vendor-node property.
  - outstanding / overdue / partial amounts — volatile; the Payable agent computes
    these live and deterministically via fact_graph.assemble_payment_context().

Facts are embedded with text-embedding-3-small (cheap; not gpt-4o-mini) so they are
retrievable by search_facts. Strictly tenant-scoped; re-runnable (content-hash upsert).

Run:  python3 backend_ocr/scripts/backfill_derived_facts.py
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from psycopg2.extras import RealDictCursor
from app.config import get_conn, put_conn
from app.domain import fact_graph


def _vendor_node_id(cur, tenant_id, supplier_id):
    cur.execute(
        "SELECT node_id::text FROM fact_graph_nodes "
        "WHERE tenant_id=%s AND node_type='vendor' AND ref_id=%s",
        (tenant_id, supplier_id),
    )
    r = cur.fetchone()
    return r["node_id"] if r else None


def _add_fact(cur, tenant_id, supplier_id, fact_text, fact_type, source, entities):
    vnode = _vendor_node_id(cur, tenant_id, supplier_id)
    if not vnode:
        return False
    fid = fact_graph.upsert_fact_node(cur, tenant_id, {
        "fact_text": fact_text, "fact_type": fact_type,
        "entities": entities, "amounts": [], "dates": [], "confidence": 1.0,
    }, source)
    if not fid:
        return False
    fact_graph.upsert_edge(cur, tenant_id, fid, vnode, fact_graph.REL_CONSTRAINS, 1.0)
    return True


def kyb_gate_facts():
    conn = get_conn()
    made = 0
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT DISTINCT s.tenant_id::text tid, s.supplier_id::text sid, s.name, s.status
            FROM dim_suppliers s
            JOIN fact_invoices fi ON fi.supplier_id=s.supplier_id AND fi.tenant_id=s.tenant_id
                                 AND fi.outstanding_amount > 0
            WHERE s.status IN ('FAILED','UNVERIFIED')
        """)
        for r in cur.fetchall():
            name = r["name"] or "This vendor"
            if r["status"] == "FAILED":
                txt = (f"{name} FAILED KYB verification — do not release payment until "
                       f"KYB is re-verified.")
            else:
                txt = (f"{name} is not KYB-verified — verify KYB before releasing any payment.")
            try:
                if _add_fact(cur, r["tid"], r["sid"], txt, "risk",
                             {"source": "derived", "basis": "kyb_status", "status": r["status"]},
                             [name]):
                    made += 1
                    conn.commit()
                else:
                    conn.rollback()
            except Exception:
                conn.rollback(); traceback.print_exc()
    finally:
        put_conn(conn)
    print(f"KYB payment-gate facts: {made}")
    return made


def nonstandard_terms_facts():
    conn = get_conn()
    made = 0
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT DISTINCT s.tenant_id::text tid, s.supplier_id::text sid, s.name, s.payment_terms_days d
            FROM dim_suppliers s
            JOIN fact_graph_nodes n ON n.tenant_id=s.tenant_id AND n.node_type='vendor' AND n.ref_id=s.supplier_id
            WHERE s.payment_terms_days IS NOT NULL AND s.payment_terms_days <> 30
        """)
        for r in cur.fetchall():
            name = r["name"] or "This vendor"
            txt = f"{name} operates on net {int(r['d'])}-day payment terms (non-standard)."
            try:
                if _add_fact(cur, r["tid"], r["sid"], txt, "payment_term",
                             {"source": "derived", "basis": "payment_terms_days", "days": int(r["d"])},
                             [name]):
                    made += 1
                    conn.commit()
                else:
                    conn.rollback()
            except Exception:
                conn.rollback(); traceback.print_exc()
    finally:
        put_conn(conn)
    print(f"Non-standard payment-term facts: {made}")
    return made


def summary():
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT count(*) n FROM fact_graph_nodes WHERE node_type='fact'")
        print("total fact nodes now:", cur.fetchone()["n"])
        cur.execute("""SELECT count(*) n FROM fact_graph_edges e
                       JOIN fact_graph_nodes s ON e.src_node_id=s.node_id
                       JOIN fact_graph_nodes d ON e.dst_node_id=d.node_id
                       WHERE s.tenant_id<>d.tenant_id""")
        n = cur.fetchone()["n"]
        print("cross-tenant edges:", n, "(must be 0)" if n == 0 else "!! ISOLATION VIOLATION")
    finally:
        put_conn(conn)


if __name__ == "__main__":
    fact_graph.init_fact_graph_schema()
    kyb_gate_facts()
    nonstandard_terms_facts()
    summary()
