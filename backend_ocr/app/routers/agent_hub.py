"""
Agent Hub API — the `/api/agents/*` surface (plural; does not collide with the
shipped `/api/agent/*` Vendor-Bill-Agent router).

Exposes the tenant fact knowledge graph for visualization + retrieval, plus the
deterministic payment context that the Payable agent reasons over.

Tenant is taken from the `company_id` query param, matching the incumbent
convention (`disbursement_api._tid`). Every read in `fact_graph` is tenant-scoped.
"""

import asyncio
import json

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from ..config import TENANT_ID, get_conn, put_conn
from ..security.auth import require_tenant
from ..domain import fact_graph

router = APIRouter(prefix="/api/agents", tags=["Agent Hub"])


def _tid(company_id: str | None) -> str:
    # Tenant is derived from the authenticated JWT; client company_id is ignored.
    return require_tenant()


@router.get("/fact-graph")
def fact_graph_view(company_id: str | None = None,
                    node_type: str | None = None,
                    limit: int = Query(default=500, ge=1, le=2000)):
    """Nodes + edges for the tenant's fact graph (no embeddings in the payload)."""
    return fact_graph.fetch_graph(_tid(company_id), node_type=node_type, limit=limit)


@router.get("/fact-graph/search")
def fact_graph_search(q: str = Query(...), company_id: str | None = None,
                      k: int = Query(default=8, ge=1, le=50)):
    """Semantic search over fact nodes."""
    return {"results": fact_graph.search_facts(_tid(company_id), q, k=k)}


@router.get("/fact-graph/neighbors")
def fact_graph_neighbors(node_id: str = Query(...), company_id: str | None = None):
    return fact_graph.neighbors(_tid(company_id), node_id)


@router.get("/payment-context")
def payment_context(company_id: str | None = None):
    """Deterministic cash + per-vendor outstanding, with advisory facts attached.
    Foundation for the Payable agent's 'whom do we pay?' decision."""
    return fact_graph.assemble_payment_context(_tid(company_id))


@router.get("/fact-graph/stream")
async def fact_graph_stream(company_id: str | None = None,
                            interval: float = Query(default=1.5, ge=0.5, le=10.0)):
    """SSE stream that emits new/updated nodes and edges as the graph builds,
    so the UI can animate the graph settling in real time.

    Push would need cross-thread pub/sub with the daemon enrichment workers, so
    we poll `updated_at`/`created_at > last_seen` on a short interval instead —
    same tenant-scoped SELECTs, no shared state.
    """
    tenant_id = _tid(company_id)

    async def _gen():
        last_node_ts = None
        last_edge_ts = None
        # Initial snapshot so the client starts from the current graph.
        snapshot = await asyncio.to_thread(fact_graph.fetch_graph, tenant_id, None, 2000)
        yield f"event: snapshot\ndata: {json.dumps(snapshot)}\n\n"
        idle = 0
        while True:
            nodes, edges, last_node_ts, last_edge_ts = await asyncio.to_thread(
                _poll_deltas, tenant_id, last_node_ts, last_edge_ts
            )
            if nodes or edges:
                idle = 0
                yield f"event: delta\ndata: {json.dumps({'nodes': nodes, 'edges': edges})}\n\n"
            else:
                idle += 1
                yield ": keep-alive\n\n"   # comment frame keeps the connection open
                # Stop after ~30s of no changes; the client can reconnect if needed.
                if idle > int(30 / interval):
                    yield "event: done\ndata: {}\n\n"
                    return
            await asyncio.sleep(interval)

    return StreamingResponse(_gen(), media_type="text/event-stream")


def _poll_deltas(tenant_id, last_node_ts, last_edge_ts):
    """Return (new_nodes, new_edges, max_node_ts, max_edge_ts) since the watermarks."""
    conn = get_conn()
    try:
        from psycopg2.extras import RealDictCursor
        cur = conn.cursor(cursor_factory=RealDictCursor)

        if last_node_ts is None:
            cur.execute("SELECT max(updated_at) AS m FROM fact_graph_nodes WHERE tenant_id=%s",
                        (tenant_id,))
            last_node_ts = cur.fetchone()["m"]
            cur.execute("SELECT max(created_at) AS m FROM fact_graph_edges WHERE tenant_id=%s",
                        (tenant_id,))
            last_edge_ts = cur.fetchone()["m"]
            return [], [], last_node_ts, last_edge_ts

        cur.execute(
            "SELECT node_id::text AS id, node_type AS type, label, ref_id::text, properties, updated_at "
            "FROM fact_graph_nodes WHERE tenant_id=%s AND updated_at > %s ORDER BY updated_at ASC LIMIT 500",
            (tenant_id, last_node_ts),
        )
        nrows = cur.fetchall()
        nodes = []
        for r in nrows:
            last_node_ts = r["updated_at"]
            d = dict(r)
            d.pop("updated_at", None)
            nodes.append(d)

        cur.execute(
            "SELECT edge_id::text, src_node_id::text AS source, dst_node_id::text AS target, "
            "relation, weight, created_at FROM fact_graph_edges "
            "WHERE tenant_id=%s AND created_at > %s ORDER BY created_at ASC LIMIT 500",
            (tenant_id, last_edge_ts),
        )
        edges = []
        for r in cur.fetchall():
            last_edge_ts = r["created_at"]
            d = dict(r)
            d.pop("created_at", None)
            edges.append(d)

        return nodes, edges, last_node_ts, last_edge_ts
    finally:
        put_conn(conn)
