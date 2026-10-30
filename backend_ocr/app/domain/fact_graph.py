"""
Tenant-scoped Fact Knowledge Graph + Vector DB.

One knowledge graph PER TENANT, stored in Postgres (pgvector). Every node/edge
carries a `tenant_id` and the graph is *strictly* isolated: no edge may ever
connect two tenants (enforced in `upsert_edge`).

The graph gives Agent-Hub agents a shared, semantic, connected view of a tenant:
vendors -> bills, contacts -> vendors, and free-form *facts* extracted from bills
and vendor email chains (e.g. "at least 30% of the due amount must be paid before
15 Aug"). Facts are embedded so agents can retrieve them by meaning.

Design constraints (PRD §10/§21): money amounts are ALWAYS computed by
deterministic SQL — the LLM only extracts advisory facts, it never does math.

Enrichment is fire-and-forget off the request path (`enqueue`), so a failure or a
slow OpenAI call never affects the user. Everything is idempotent (content-hash on
node text, ON CONFLICT-style upserts) so backfill and live hooks can both run and
re-run safely.
"""

import json
import hashlib
import traceback
from concurrent.futures import ThreadPoolExecutor

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from ..config import OPENAI_API_KEY, get_conn, put_conn

# ── Model config ──────────────────────────────────────────────────────────────
EMBED_MODEL = "text-embedding-3-small"   # 1536 dims — good cost/quality
EMBED_DIM = 1536
EXTRACT_MODEL = "gpt-4o-mini"            # already the house model everywhere else

# Node types
NODE_TENANT = "tenant"
NODE_VENDOR = "vendor"
NODE_BILL = "bill"
NODE_CONTACT = "contact"
NODE_FACT = "fact"

# Relation taxonomy
REL_OWNS = "owns"            # tenant -> vendor
REL_HAS_BILL = "has_bill"    # vendor -> bill
REL_POC_OF = "poc_of"        # contact -> vendor
REL_CONSTRAINS = "constrains"  # fact -> bill/vendor (hard terms)
REL_ABOUT = "about"          # fact -> vendor/bill (soft: preference/relationship/risk)

_FACT_TYPES = {"payment_term", "constraint", "preference", "relationship", "risk"}


# ── OpenAI helpers (embeddings are new wiring; chat re-uses the house style) ────
def _client():
    from openai import OpenAI
    return OpenAI(api_key=OPENAI_API_KEY)


def _embed(text: str):
    """Embed a single string. Returns a list[float] or None if unavailable."""
    if not OPENAI_API_KEY or not (text or "").strip():
        return None
    try:
        resp = _client().embeddings.create(model=EMBED_MODEL, input=text[:8000])
        return resp.data[0].embedding
    except Exception:
        traceback.print_exc()
        return None


def _embed_batch(texts: list) -> list:
    """Embed many strings in one call (cuts round-trips during backfill)."""
    clean = [(t or "")[:8000] for t in texts]
    if not OPENAI_API_KEY or not clean:
        return [None] * len(texts)
    try:
        resp = _client().embeddings.create(model=EMBED_MODEL, input=clean)
        return [d.embedding for d in resp.data]
    except Exception:
        traceback.print_exc()
        return [None] * len(texts)


def _vec_literal(vec):
    """Format a vector for `%s::vector` binding, or None."""
    if vec is None:
        return None
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


def _content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ── Schema ──────────────────────────────────────────────────────────────────
def init_fact_graph_schema() -> None:
    """Create the extension, tables and indexes. Safe to call repeatedly.

    Called from a FastAPI startup hook AFTER the source tables exist. Failures
    are logged, not raised, so the server still boots during a DB hiccup.
    """
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        # pgvector — the app role may lack CREATE EXTENSION; guard it so the rest
        # of startup still runs, and log a clear instruction if the vector DDL
        # then fails.
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            conn.commit()
        except Exception:
            conn.rollback()
            print("[startup] fact_graph: could not CREATE EXTENSION vector — "
                  "enable the 'vector' extension once via the Neon console, then restart.")

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS fact_graph_nodes (
                node_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id    uuid NOT NULL,
                node_type    text NOT NULL,
                ref_id       uuid,
                label        text NOT NULL,
                properties   jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                embedding    vector({EMBED_DIM}),
                content_hash text,
                created_at   timestamptz NOT NULL DEFAULT now(),
                updated_at   timestamptz NOT NULL DEFAULT now()
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_fgn_entity
                ON fact_graph_nodes (tenant_id, node_type, ref_id)
                WHERE ref_id IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS uq_fgn_fact
                ON fact_graph_nodes (tenant_id, node_type, content_hash)
                WHERE ref_id IS NULL AND content_hash IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_fgn_tenant_type
                ON fact_graph_nodes (tenant_id, node_type);
        """)
        conn.commit()

        # ANN index — HNSW/cosine. Guarded: needs the vector extension to exist.
        try:
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_fgn_embedding_hnsw
                    ON fact_graph_nodes USING hnsw (embedding vector_cosine_ops);
            """)
            conn.commit()
        except Exception:
            conn.rollback()
            print("[startup] fact_graph: HNSW index not created (vector extension missing?).")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS fact_graph_edges (
                edge_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id   uuid NOT NULL,
                src_node_id uuid NOT NULL REFERENCES fact_graph_nodes(node_id) ON DELETE CASCADE,
                dst_node_id uuid NOT NULL REFERENCES fact_graph_nodes(node_id) ON DELETE CASCADE,
                relation    text NOT NULL,
                weight      double precision NOT NULL DEFAULT 1.0,
                properties  jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at  timestamptz NOT NULL DEFAULT now()
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_fge_triple
                ON fact_graph_edges (tenant_id, src_node_id, dst_node_id, relation);
            CREATE INDEX IF NOT EXISTS idx_fge_tenant_src ON fact_graph_edges (tenant_id, src_node_id);
            CREATE INDEX IF NOT EXISTS idx_fge_tenant_dst ON fact_graph_edges (tenant_id, dst_node_id);
        """)
        conn.commit()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS poc_email_messages (
                message_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id    uuid NOT NULL,
                supplier_id  uuid,
                contact_id   uuid,
                direction    text NOT NULL,
                gmail_msg_id text,
                thread_id    text,
                from_email   text,
                to_email     text,
                subject      text,
                body         text,
                sent_at      timestamptz,
                created_at   timestamptz NOT NULL DEFAULT now(),
                embedded_at  timestamptz
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_pem_gmail
                ON poc_email_messages (tenant_id, gmail_msg_id, direction)
                WHERE gmail_msg_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_pem_tenant_supplier ON poc_email_messages (tenant_id, supplier_id);
            CREATE INDEX IF NOT EXISTS idx_pem_thread ON poc_email_messages (tenant_id, thread_id);
        """)
        conn.commit()
        print("[startup] fact_graph tables ready")
    except Exception:
        if conn:
            conn.rollback()
        print("[startup] ERROR creating fact_graph schema:")
        traceback.print_exc()
    finally:
        if conn:
            put_conn(conn)


# ── Node / edge upserts ───────────────────────────────────────────────────────
def upsert_node(cur, tenant_id, node_type, ref_id, label, properties=None,
                embed_text=None) -> str:
    """Upsert an *entity* node (vendor/bill/contact/tenant), keyed by
    (tenant_id, node_type, ref_id). Re-embeds only when `embed_text` changed."""
    properties = properties or {}
    h = _content_hash(embed_text) if embed_text else None
    cur.execute(
        "SELECT node_id::text, content_hash FROM fact_graph_nodes "
        "WHERE tenant_id=%s AND node_type=%s AND ref_id=%s",
        (tenant_id, node_type, ref_id),
    )
    row = cur.fetchone()
    if row:
        node_id = row["node_id"]
        if embed_text and row["content_hash"] != h:
            emb = _embed(embed_text)
            cur.execute(
                "UPDATE fact_graph_nodes SET label=%s, properties=%s, "
                "embedding=%s::vector, content_hash=%s, updated_at=now() WHERE node_id=%s",
                (label, Json(properties), _vec_literal(emb), h, node_id),
            )
        else:
            cur.execute(
                "UPDATE fact_graph_nodes SET label=%s, properties=%s, updated_at=now() "
                "WHERE node_id=%s",
                (label, Json(properties), node_id),
            )
        return node_id

    emb = _embed(embed_text) if embed_text else None
    cur.execute(
        "INSERT INTO fact_graph_nodes (tenant_id, node_type, ref_id, label, properties, "
        "embedding, content_hash) VALUES (%s,%s,%s,%s,%s,%s::vector,%s) "
        "ON CONFLICT (tenant_id, node_type, ref_id) WHERE ref_id IS NOT NULL "
        "DO UPDATE SET label=EXCLUDED.label, properties=EXCLUDED.properties, updated_at=now() "
        "RETURNING node_id::text",
        (tenant_id, node_type, ref_id, label, Json(properties), _vec_literal(emb), h),
    )
    return cur.fetchone()["node_id"]


def upsert_fact_node(cur, tenant_id, fact: dict, source: dict) -> str:
    """Upsert a *fact* node, keyed by content_hash of the fact text."""
    fact_text = (fact.get("fact_text") or "").strip()
    if not fact_text:
        return None
    fact_type = fact.get("fact_type")
    if fact_type not in _FACT_TYPES:
        fact_type = "constraint"
    h = _content_hash(fact_text)
    props = {
        "fact_type": fact_type,
        "entities": fact.get("entities") or [],
        "amounts": fact.get("amounts") or [],
        "dates": fact.get("dates") or [],
        "confidence": fact.get("confidence"),
        "source": source,
    }
    cur.execute(
        "SELECT node_id::text FROM fact_graph_nodes "
        "WHERE tenant_id=%s AND node_type='fact' AND content_hash=%s",
        (tenant_id, h),
    )
    row = cur.fetchone()
    if row:
        node_id = row["node_id"]
        cur.execute(
            "UPDATE fact_graph_nodes SET properties=%s, updated_at=now() WHERE node_id=%s",
            (Json(props), node_id),
        )
        return node_id

    emb = _embed(fact_text)
    cur.execute(
        "INSERT INTO fact_graph_nodes (tenant_id, node_type, ref_id, label, properties, "
        "embedding, content_hash) VALUES (%s,'fact',NULL,%s,%s,%s::vector,%s) "
        "ON CONFLICT (tenant_id, node_type, content_hash) WHERE ref_id IS NULL AND content_hash IS NOT NULL "
        "DO UPDATE SET properties=EXCLUDED.properties, updated_at=now() "
        "RETURNING node_id::text",
        (tenant_id, fact_text[:200], Json(props), _vec_literal(emb), h),
    )
    return cur.fetchone()["node_id"]


def upsert_edge(cur, tenant_id, src_node_id, dst_node_id, relation,
                weight: float = 1.0, properties=None) -> None:
    """Upsert an edge. HARD tenant-isolation: refuses to link nodes that do not
    both belong to `tenant_id`, so a graph can never span two tenants."""
    if not src_node_id or not dst_node_id:
        return
    cur.execute(
        "SELECT node_id::text FROM fact_graph_nodes "
        "WHERE node_id IN (%s,%s) AND tenant_id=%s",
        (src_node_id, dst_node_id, tenant_id),
    )
    owned = {r["node_id"] for r in cur.fetchall()}
    if src_node_id not in owned or dst_node_id not in owned:
        # Isolation violation — never create a cross-tenant edge.
        raise ValueError(
            f"fact_graph tenant isolation: edge {relation} endpoints not both in tenant {tenant_id}"
        )
    cur.execute(
        "INSERT INTO fact_graph_edges (tenant_id, src_node_id, dst_node_id, relation, weight, properties) "
        "VALUES (%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (tenant_id, src_node_id, dst_node_id, relation) "
        "DO UPDATE SET weight=EXCLUDED.weight, properties=EXCLUDED.properties",
        (tenant_id, src_node_id, dst_node_id, relation, weight, Json(properties or {})),
    )


# ── Fact extraction (LLM) ───────────────────────────────────────────────────
_EXTRACT_SYSTEM = """You are an accounts-payable analyst. From the supplied text, extract
DISCRETE, actionable FACTS that a finance/payment agent would need — especially anything about
payment timing, amounts, discounts, penalties, holds, disputes, or the relationship with the vendor.

Extract ONLY facts explicitly stated in the text. Do NOT compute or infer monetary totals; any
amounts you return are quotations of stated terms (e.g. "30%", "₹50,000", "15 days"), advisory only.

Respond as STRICT JSON:
{"facts":[{"fact_text":"<one crisp sentence>","fact_type":"payment_term|constraint|preference|relationship|risk",
"entities":["..."],"amounts":[{"kind":"percent|absolute|days","value":<number>}],
"dates":["YYYY-MM-DD"],"confidence":0.0-1.0}]}
Return {"facts":[]} if nothing noteworthy. Prefer fewer, high-signal facts over many trivial ones."""


def _extract_facts(context_text: str, hint: str) -> list:
    if not OPENAI_API_KEY or not (context_text or "").strip():
        return []
    try:
        resp = _client().chat.completions.create(
            model=EXTRACT_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": f"{hint}\n\nTEXT:\n{context_text[:8000]}"},
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        facts = data.get("facts") or []
        return [f for f in facts if isinstance(f, dict) and (f.get("fact_text") or "").strip()]
    except Exception:
        traceback.print_exc()
        return []


def extract_facts_from_bill(bill: dict, vendor: dict) -> list:
    ctx = (
        f"Vendor: {vendor.get('name')}\n"
        f"Invoice number: {bill.get('invoice_number')}\n"
        f"Invoice date: {bill.get('invoice_date')}\n"
        f"Due date: {bill.get('due_date')}\n"
        f"Total amount: {bill.get('total_amount')} {bill.get('currency_code') or ''}\n"
        f"Outstanding: {bill.get('outstanding_amount')}\n"
        f"Vendor default payment terms (days): {vendor.get('payment_terms_days')}\n"
        f"Category: {bill.get('category')}  Department: {bill.get('department')}"
    )
    return _extract_facts(ctx, "This is a single vendor invoice. Extract stated payment terms / constraints.")


def extract_facts_from_email(body: str, subject: str, vendor_name: str) -> list:
    ctx = f"Vendor: {vendor_name}\nSubject: {subject}\n\n{body}"
    return _extract_facts(
        ctx,
        "This is a vendor email / thread. Extract any payment instructions, deadlines, "
        "part-payment demands, penalties, disputes or relationship notes.",
    )


# ── Composed enrichers (each owns its own connection — safe off request path) ──
def _ensure_tenant_node(cur, tenant_id, do_embed: bool = True) -> str:
    cur.execute("SELECT company_name FROM dim_tenants WHERE tenant_id=%s", (tenant_id,))
    row = cur.fetchone()
    name = (row["company_name"] if row else None) or "Tenant"
    return upsert_node(cur, tenant_id, NODE_TENANT, tenant_id, name,
                       {"tenant_id": str(tenant_id)},
                       embed_text=(f"Company {name}" if do_embed else None))


def _ensure_vendor_node(cur, tenant_id, supplier_id, vendor: dict = None, do_embed: bool = True) -> str:
    if vendor is None:
        cur.execute(
            "SELECT supplier_id::text, name, gstn, email, payment_terms_days, status "
            "FROM dim_suppliers WHERE tenant_id=%s AND supplier_id=%s",
            (tenant_id, supplier_id),
        )
        vendor = cur.fetchone()
    if not vendor:
        return None
    name = vendor.get("name") or "Vendor"
    props = {
        "gstn": vendor.get("gstn"),
        "email": vendor.get("email"),
        "payment_terms_days": vendor.get("payment_terms_days"),
        "status": vendor.get("status"),
    }
    embed = (f"Vendor {name}. GSTIN {vendor.get('gstn')}. "
             f"Default payment terms {vendor.get('payment_terms_days')} days.") if do_embed else None
    vid = upsert_node(cur, tenant_id, NODE_VENDOR, supplier_id, name, props, embed_text=embed)
    tid_node = _ensure_tenant_node(cur, tenant_id, do_embed=do_embed)
    upsert_edge(cur, tenant_id, tid_node, vid, REL_OWNS)
    return vid


def _link_facts(cur, tenant_id, facts, source, subject_node_id, vendor_node_id):
    """Create fact nodes and connect them to the subject (bill/email) + vendor."""
    for f in facts:
        fid = upsert_fact_node(cur, tenant_id, f, source)
        if not fid:
            continue
        ftype = f.get("fact_type")
        rel = REL_CONSTRAINS if ftype in ("payment_term", "constraint") else REL_ABOUT
        conf = f.get("confidence")
        weight = float(conf) if isinstance(conf, (int, float)) else 1.0
        if vendor_node_id:
            upsert_edge(cur, tenant_id, fid, vendor_node_id, rel, weight)
        if subject_node_id and subject_node_id != vendor_node_id:
            upsert_edge(cur, tenant_id, fid, subject_node_id, rel, weight)


def enrich_vendor(tenant_id, supplier_id, do_facts: bool = True, do_embed: bool = True) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        vid = _ensure_vendor_node(cur, tenant_id, supplier_id, do_embed=do_embed)
        if not vid:
            conn.rollback()
            return
        cur.execute(
            "SELECT id::text, name, role, email, phone FROM supplier_contacts "
            "WHERE tenant_id=%s AND supplier_id=%s",
            (tenant_id, supplier_id),
        )
        for c in cur.fetchall():
            cid = upsert_node(
                cur, tenant_id, NODE_CONTACT, c["id"],
                c.get("name") or c.get("email") or "Contact",
                {"role": c.get("role"), "email": c.get("email"), "phone": c.get("phone")},
                embed_text=(f"Contact {c.get('name')} ({c.get('role')}) {c.get('email')}" if do_embed else None),
            )
            upsert_edge(cur, tenant_id, cid, vid, REL_POC_OF)
        conn.commit()
    except Exception:
        conn.rollback()
        traceback.print_exc()
    finally:
        put_conn(conn)


def enrich_bill(tenant_id, invoice_id, do_facts: bool = True, do_embed: bool = True) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT fi.invoice_id::text, fi.supplier_id::text, fi.invoice_number, fi.invoice_date, "
            "       fi.due_date, fi.total_amount, fi.outstanding_amount, fi.currency_code, "
            "       fi.status, fi.payment_status, fi.category, fi.department, "
            "       s.name AS vendor_name, s.gstn, s.payment_terms_days, s.email, s.status AS vendor_status "
            "FROM fact_invoices fi "
            "LEFT JOIN dim_suppliers s ON s.supplier_id=fi.supplier_id AND s.tenant_id=fi.tenant_id "
            "WHERE fi.tenant_id=%s AND fi.invoice_id=%s",
            (tenant_id, invoice_id),
        )
        b = cur.fetchone()
        if not b:
            conn.rollback()
            return
        vid = None
        if b["supplier_id"]:
            vid = _ensure_vendor_node(cur, tenant_id, b["supplier_id"], {
                "name": b["vendor_name"], "gstn": b["gstn"], "email": b["email"],
                "payment_terms_days": b["payment_terms_days"], "status": b["vendor_status"],
            }, do_embed=do_embed)
        bill_props = {
            "invoice_number": b["invoice_number"],
            "invoice_date": str(b["invoice_date"]) if b["invoice_date"] else None,
            "due_date": str(b["due_date"]) if b["due_date"] else None,
            "total_amount": float(b["total_amount"]) if b["total_amount"] is not None else None,
            "outstanding_amount": float(b["outstanding_amount"]) if b["outstanding_amount"] is not None else None,
            "currency_code": b["currency_code"],
            "status": b["status"],
            "payment_status": b["payment_status"],
        }
        embed = (f"Invoice {b['invoice_number']} from {b['vendor_name']}. "
                 f"Amount {b['total_amount']} {b['currency_code'] or ''}. Due {b['due_date']}. "
                 f"Outstanding {b['outstanding_amount']}. Status {b['payment_status'] or b['status']}.") if do_embed else None
        bid = upsert_node(cur, tenant_id, NODE_BILL, invoice_id,
                          b["invoice_number"] or "Invoice", bill_props, embed_text=embed)
        if vid:
            upsert_edge(cur, tenant_id, vid, bid, REL_HAS_BILL)

        if do_facts:
            facts = extract_facts_from_bill(b, {
                "name": b["vendor_name"], "payment_terms_days": b["payment_terms_days"],
            })
            _link_facts(cur, tenant_id, facts,
                        {"source": "bill", "invoice_id": str(invoice_id)}, bid, vid)
        conn.commit()
    except Exception:
        conn.rollback()
        traceback.print_exc()
    finally:
        put_conn(conn)


def enrich_email(tenant_id, message_id) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT message_id::text, supplier_id::text, thread_id, from_email, subject, body "
            "FROM poc_email_messages WHERE tenant_id=%s AND message_id=%s",
            (tenant_id, message_id),
        )
        m = cur.fetchone()
        if not m or not (m["body"] or "").strip():
            conn.rollback()
            return
        vid = None
        vendor_name = m["from_email"] or "Vendor"
        if m["supplier_id"]:
            vid = _ensure_vendor_node(cur, tenant_id, m["supplier_id"])
            cur.execute("SELECT name FROM dim_suppliers WHERE tenant_id=%s AND supplier_id=%s",
                        (tenant_id, m["supplier_id"]))
            r = cur.fetchone()
            if r:
                vendor_name = r["name"]

        # Optionally link facts to the specific bill this thread produced.
        bill_node_id = None
        if m["thread_id"] and m["supplier_id"]:
            cur.execute(
                "SELECT n.node_id::text FROM fact_graph_nodes n "
                "WHERE n.tenant_id=%s AND n.node_type='bill' AND n.ref_id IN ("
                "  SELECT invoice_id FROM fact_invoices WHERE tenant_id=%s AND supplier_id=%s"
                ") ORDER BY n.updated_at DESC LIMIT 1",
                (tenant_id, tenant_id, m["supplier_id"]),
            )
            r = cur.fetchone()
            bill_node_id = r["node_id"] if r else None

        facts = extract_facts_from_email(m["body"], m["subject"], vendor_name)
        source = {"source": "email", "message_id": str(message_id), "thread_id": m["thread_id"]}
        subject_node = bill_node_id or vid
        _link_facts(cur, tenant_id, facts, source, subject_node, vid)

        cur.execute("UPDATE poc_email_messages SET embedded_at=now() WHERE message_id=%s", (message_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        traceback.print_exc()
    finally:
        put_conn(conn)


# ── Fire-and-forget dispatcher ──────────────────────────────────────────────
_EXECUTOR = None


def _executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        # Bounded so background enrichment can't exhaust the DB pool (maxconn=50)
        # or burst OpenAI calls.
        _EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="fact-graph")
    return _EXECUTOR


def enqueue(fn, *args) -> None:
    """Run enrichment off the request path. Never raises into the caller."""
    def _run():
        try:
            fn(*args)
        except Exception:
            traceback.print_exc()
    try:
        _executor().submit(_run)
    except Exception:
        traceback.print_exc()


# ── Retrieval API for agents ──────────────────────────────────────────────────
def search_facts(tenant_id, query, k: int = 8, fact_types: list = None) -> list:
    """Semantic search over the tenant's fact nodes (cosine distance)."""
    emb = _embed(query)
    if emb is None:
        return []
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        sql = (
            "SELECT node_id::text, label, properties, (embedding <=> %(q)s::vector) AS distance "
            "FROM fact_graph_nodes "
            "WHERE tenant_id=%(tid)s AND node_type='fact' AND embedding IS NOT NULL"
        )
        params = {"q": _vec_literal(emb), "tid": tenant_id, "k": k}
        if fact_types:
            sql += " AND (properties->>'fact_type') = ANY(%(ft)s)"
            params["ft"] = list(fact_types)
        sql += " ORDER BY distance ASC LIMIT %(k)s"
        cur.execute(sql, params)
        out = []
        for r in cur.fetchall():
            props = r["properties"] or {}
            out.append({
                "node_id": r["node_id"],
                "fact_text": r["label"],
                "fact_type": props.get("fact_type"),
                "confidence": props.get("confidence"),
                "distance": float(r["distance"]) if r["distance"] is not None else None,
                "properties": props,
            })
        return out
    finally:
        put_conn(conn)


def neighbors(tenant_id, node_id, depth: int = 1) -> dict:
    """1-hop neighbourhood (nodes + edges) around a node, tenant-scoped."""
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT edge_id::text, src_node_id::text AS source, dst_node_id::text AS target, "
            "relation, weight FROM fact_graph_edges "
            "WHERE tenant_id=%s AND (src_node_id=%s OR dst_node_id=%s)",
            (tenant_id, node_id, node_id),
        )
        edges = [dict(r) for r in cur.fetchall()]
        ids = {node_id}
        for e in edges:
            ids.add(e["source"])
            ids.add(e["target"])
        cur.execute(
            "SELECT node_id::text AS id, node_type AS type, label, properties "
            "FROM fact_graph_nodes WHERE tenant_id=%s AND node_id = ANY(%s::uuid[])",
            (tenant_id, list(ids)),
        )
        nodes = [dict(r) for r in cur.fetchall()]
        return {"nodes": nodes, "edges": edges}
    finally:
        put_conn(conn)


def fetch_graph(tenant_id, node_type: str = None, limit: int = 500) -> dict:
    """Full (capped) graph for the visualization. Never returns embeddings."""
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        # When capping, keep the high-value nodes (tenant, vendors, contacts, facts)
        # and let the numerous bills be the first to drop — facts are the point.
        order = ("CASE node_type WHEN 'tenant' THEN 0 WHEN 'vendor' THEN 1 "
                 "WHEN 'contact' THEN 2 WHEN 'fact' THEN 3 ELSE 4 END, created_at")
        if node_type:
            cur.execute(
                f"SELECT node_id::text AS id, node_type AS type, label, ref_id::text, properties, updated_at "
                f"FROM fact_graph_nodes WHERE tenant_id=%s AND node_type=%s ORDER BY {order} LIMIT %s",
                (tenant_id, node_type, limit),
            )
        else:
            cur.execute(
                f"SELECT node_id::text AS id, node_type AS type, label, ref_id::text, properties, updated_at "
                f"FROM fact_graph_nodes WHERE tenant_id=%s ORDER BY {order} LIMIT %s",
                (tenant_id, limit),
            )
        nodes = [dict(r) for r in cur.fetchall()]
        node_ids = [n["id"] for n in nodes]
        edges = []
        if node_ids:
            cur.execute(
                "SELECT edge_id::text, src_node_id::text AS source, dst_node_id::text AS target, "
                "relation, weight FROM fact_graph_edges "
                "WHERE tenant_id=%s AND src_node_id = ANY(%s::uuid[]) AND dst_node_id = ANY(%s::uuid[])",
                (tenant_id, node_ids, node_ids),
            )
            edges = [dict(r) for r in cur.fetchall()]
        for n in nodes:
            n.pop("updated_at", None)
        return {"nodes": nodes, "edges": edges}
    finally:
        put_conn(conn)


def assemble_payment_context(tenant_id) -> dict:
    """Foundation for the Payable agent's 'whom do we pay?' decision.

    ALL money numbers come from deterministic SQL. Extracted facts are attached
    per-vendor as advisory context only (never used for math).
    """
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("SELECT COALESCE(SUM(balance),0) AS cash FROM remitter_accounts WHERE tenant_id=%s",
                    (tenant_id,))
        cash = float(cur.fetchone()["cash"] or 0)

        cur.execute(
            "SELECT s.supplier_id::text, s.name, s.status AS kyb_status, "
            "       COALESCE(SUM(fi.outstanding_amount),0) AS outstanding, "
            "       MIN(fi.due_date) AS earliest_due, COUNT(*) AS open_bills "
            "FROM fact_invoices fi "
            "JOIN dim_suppliers s ON s.supplier_id=fi.supplier_id AND s.tenant_id=fi.tenant_id "
            "WHERE fi.tenant_id=%s AND fi.outstanding_amount > 0 "
            "GROUP BY s.supplier_id, s.name, s.status "
            "ORDER BY earliest_due ASC NULLS LAST",
            (tenant_id,),
        )
        vendors = []
        by_supplier = {}
        for r in cur.fetchall():
            v = {
                "supplier_id": r["supplier_id"],
                "name": r["name"],
                "kyb_status": r["kyb_status"],
                "outstanding": float(r["outstanding"] or 0),
                "earliest_due": str(r["earliest_due"]) if r["earliest_due"] else None,
                "open_bills": int(r["open_bills"]),
                "facts": [],
            }
            vendors.append(v)
            by_supplier[r["supplier_id"]] = v

        # Attach advisory constraint/payment facts by traversing fact->vendor edges
        # (deterministic; no LLM/embedding call here).
        cur.execute(
            "SELECT f.label, f.properties, v.ref_id::text AS supplier_id "
            "FROM fact_graph_nodes f "
            "JOIN fact_graph_edges e ON e.tenant_id=f.tenant_id AND e.src_node_id=f.node_id "
            "JOIN fact_graph_nodes v ON v.node_id=e.dst_node_id AND v.tenant_id=f.tenant_id "
            "WHERE f.tenant_id=%s AND f.node_type='fact' AND v.node_type='vendor' "
            "AND (f.properties->>'fact_type') IN ('payment_term','constraint','risk')",
            (tenant_id,),
        )
        for r in cur.fetchall():
            v = by_supplier.get(r["supplier_id"])
            if v is not None:
                props = r["properties"] or {}
                v["facts"].append({
                    "fact_text": r["label"],
                    "fact_type": props.get("fact_type"),
                    "confidence": props.get("confidence"),
                    "source": props.get("source"),
                })

        total_outstanding = sum(v["outstanding"] for v in vendors)
        return {
            "tenant_id": str(tenant_id),
            "cash": cash,
            "total_outstanding": total_outstanding,
            "shortfall": round(total_outstanding - cash, 2),
            "vendor_count": len(vendors),
            "vendors": vendors,
        }
    finally:
        put_conn(conn)
