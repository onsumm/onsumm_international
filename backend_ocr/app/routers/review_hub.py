"""Review Hub API — /api/review.

One queue for everything wrong across the platform, scoped to what the caller may
see and ranked by what it costs them.

Two things here are firsts for this codebase and are meant as templates:

* **Keyset pagination.** Every other list in the app loads in full and filters
  client-side (see frontend/src/ui/columnFilters.ts, which documents that as
  deliberate). That is fine for bills; it is not fine for a table that grows
  without bound. The cursor is (rank_score, finding_id), which maps exactly onto
  idx_rh_findings_stream and is stable under concurrent insert — a new
  high-ranked finding arriving mid-pagination shows up on refresh, never as a
  duplicate or a skipped row. OFFSET has neither property.

* **Entitlement filtering inside the handler.** licensing._RULES maps a route to
  exactly one service key, but findings arrive from a dozen services. The hub is
  core and default-on; an individual finding is not. So every row carries
  `source_service` and the list filters it against the tenant's resolved
  entitlements, or a tenant with no FX licence would be shown FX findings.

Money actions are deliberately NOT executable here. Releasing a payment is
rendered as a deep link so the user lands on the screen that already has the OTP
gate, the confirmations and the audit trail — see docs/PAYMENT_GUARDRAILS.md. A
triage queue must not become a second, thinner path to an irreversible operation.

Approving a bill *is* executable here, and the distinction is deliberate. It is
not irreversible — the chain can send a bill back — and the guardrails doc gates
disbursement, not approval. What made acting from a queue objectionable was
thinness: approving something you had not read. The drawer now puts the bill's
PDF, its key fields and its chain one click away, and the approval itself is
confirmed with the vendor and the amount named. It runs through POST
/act/approve-bill, which hands to routers/bill_approvals.bill_action — the
handler that owns the chain rules, the role gate, the assignee gate and the
audit row — with the actor read from the token rather than the request body, so
it is strictly narrower than the path the Bills screen uses.

There is no single generic POST /act dispatcher for `kind='safe'` entries: each
one names, in `action()`'s own `endpoint` field, whatever real endpoint already
owns the write — most often a vendor/bill router route (`PATCH /api/vendors/{id}`,
`POST /api/vendors/{id}/verify-bank`, ...), and the frontend calls it directly.
A hub-owned route under /act only exists for the handful of operations with no
router of their own to call — /act/approve-bill for the one approval, and
/act/merge-misread-vendor and /act/notify for writes (a vendor merge, a
notify's send) that were never a vendor-router endpoint to begin with. All
three are still "call the logic that already owns the operation" — they just
are that logic, because nothing else was.
"""
from __future__ import annotations

import base64
import json
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel, Field

from ..config import get_conn, put_conn
from ..domain.review import bill_actions as review_bill_actions
from ..domain.review import notify_actions as review_notify_actions
from ..domain.review import previews as review_previews
from ..domain.review.visibility import actionable_sql, visibility_sql
from ..domain.vendor_merge import repoint_and_retire
from ..security.auth import current_role, current_username
from ..security.capabilities import capabilities_for, can, require_capability
from ..security.licensing import entitlements
from ..security.tenancy import Tenant

router = APIRouter(prefix="/api/review", tags=["Review Hub"])

MAX_LIMIT = 200
DEFAULT_LIMIT = 12
LANES = ("blocking", "money", "compliance", "quality", "suspected")


class _PoolConn:
    def __enter__(self):
        self._conn = get_conn()
        self._conn.autocommit = False
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            try:
                self._conn.rollback()
            except Exception:
                pass
            put_conn(self._conn, close=True)
        else:
            self._conn.commit()
            put_conn(self._conn)


# ── Cursor ────────────────────────────────────────────────────────────────────

def _encode_cursor(rank_score, finding_id: str) -> str:
    raw = f"{rank_score}|{finding_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None):
    if not cursor:
        return None, None
    try:
        pad = "=" * (-len(cursor) % 4)
        rank, fid = base64.urlsafe_b64decode(cursor + pad).decode().split("|", 1)
        return Decimal(rank), fid
    except Exception:
        # A malformed cursor means "start from the top", not a 500. Cursors end
        # up in shared URLs and get truncated by chat clients.
        return None, None


# ── Serialisation ─────────────────────────────────────────────────────────────

def _lane_of(row) -> str:
    from ..review.ranking import lane_for
    return lane_for(row["family"], row["verification"])


def _num(v):
    return float(v) if v is not None else None


def _iso(v):
    return v.isoformat() if v else None


def _serialise(row, *, my_caps: list[str]) -> dict:
    actions = row.get("actions_jsonb") or []
    if isinstance(actions, str):
        actions = json.loads(actions)
    evidence = row.get("evidence_jsonb") or {}
    if isinstance(evidence, str):
        evidence = json.loads(evidence)

    can_act = row["required_capability"] in my_caps

    return {
        "finding_id":   str(row["finding_id"]),
        "finding_type": row["finding_type"],
        "family":       row["family"],
        "lane":         _lane_of(row),
        "subject":      {"type": row["subject_type"],
                         "id": str(row["subject_id"]) if row["subject_id"] else None},
        "severity":     row["severity"],
        "confidence":   _num(row["confidence"]),
        "verification": row["verification"],
        # The single flag the card uses to pick a solid or dashed rail. Keeping
        # the derivation on the server means the UI never has to know which
        # detectors are deterministic.
        "is_suspected": row["verification"] in ("UNVERIFIED", "UNVERIFIABLE"),
        "money_at_risk":     _num(row["money_at_risk"]),
        "money_currency":    (row["money_currency"] or "INR").strip(),
        "money_at_risk_inr": _num(row["money_at_risk_inr"]),
        "due_at":       _iso(row["due_at"]),
        "rank":         _num(row["rank_score"]),
        "title":        row["title"],
        "detail":       row["detail"] or "",
        "evidence":     evidence,
        # Hoisted out of the evidence payload so the drawer can lay the source
        # document beside the finding without knowing the evidence shape.
        "document":     evidence.get("document_ref") if isinstance(evidence, dict) else None,
        "actions":      [a for a in actions if a.get("capability", "view") in my_caps
                         or a.get("kind") == "navigate"],
        "deep_link":    row["deep_link"],
        "can_act":      can_act,
        "required_capability": row["required_capability"],
        "status":       row["status"],
        "status_reason": row.get("status_reason"),
        "status_by":    row.get("status_by"),
        "snoozed_until": _iso(row.get("snoozed_until")),
        "detector":     row["detector"],
        "source_service": row["source_service"],
        "model":        row.get("model"),
        "occurrence_count": row["occurrence_count"],
        "first_detected_at": _iso(row["first_detected_at"]),
        "last_seen_at": _iso(row["last_seen_at"]),
        "entities":     row.get("entities") or [],
    }


def _licensed_services(tenant: str) -> list[str]:
    try:
        ent = entitlements(tenant)
        enabled = getattr(ent, "enabled", None)
        if enabled:
            return sorted(enabled)
    except Exception:
        pass
    # Entitlement resolution fails open elsewhere in licensing.py (a DB blip must
    # not lock a paying tenant out of their own data); match that here rather
    # than blanking the hub.
    return []


# Orphan vendors are not worth anyone's attention. A finding *about* a vendor
# (subject_type 'vendor') is left out when that vendor record is retired —
# merged away or deactivated, so nothing can be paid or fixed on it — or, for
# bank / GSTIN / PAN findings, when it has no bills at all, so there is nothing
# to pay. Findings about a bill are never hidden this way, even if the bill's
# vendor is inactive: the bill is still real work.
NOT_ORPHAN_VENDOR_SQL = """
    NOT EXISTS (
        SELECT 1 FROM dim_suppliers ov
         WHERE f.subject_type = 'vendor'
           AND ov.supplier_id = f.subject_id
           AND ov.tenant_id   = f.tenant_id
           AND (NOT ov.is_active
                OR (f.family = 'vendor_identity'
                    AND NOT EXISTS (
                        SELECT 1 FROM fact_invoices ofi
                         WHERE ofi.supplier_id = ov.supplier_id
                           AND ofi.tenant_id   = ov.tenant_id
                           AND COALESCE(ofi.soft_status, 'active') = 'active')))
    )"""


# Bank findings checked against the vendor record at read time. A scanner on
# older code (or a pass that has not run yet) can leave a "no bank account" up
# after one was added; the queue must not repeat it. An account lives in
# supplier_bank_accounts or in the older dim_suppliers fields — the vendor card
# shows both, so both count here.
NOT_STALE_BANK_SQL = """
    NOT (
        f.subject_type = 'vendor'
        AND f.finding_type IN ('vendor_bank_missing', 'vendor_bank_unverified', 'vendor_bank_unverifiable')
        AND EXISTS (
            SELECT 1 FROM dim_suppliers bs
             WHERE bs.supplier_id = f.subject_id AND bs.tenant_id = f.tenant_id
               AND (
                     -- "no bank account", but there is one
                     (f.finding_type = 'vendor_bank_missing'
                      AND (NULLIF(TRIM(bs.bank_account), '') IS NOT NULL
                           OR EXISTS (SELECT 1 FROM supplier_bank_accounts bb
                                       WHERE bb.supplier_id = bs.supplier_id
                                         AND bb.tenant_id = bs.tenant_id)))
                     -- "no verified account", but one is verified
                  OR (f.finding_type = 'vendor_bank_unverified'
                      AND ((NULLIF(TRIM(bs.bank_account), '') IS NOT NULL
                            AND COALESCE(bs.bank_verified, false))
                           OR EXISTS (SELECT 1 FROM supplier_bank_accounts bb
                                       WHERE bb.supplier_id = bs.supplier_id
                                         AND bb.tenant_id = bs.tenant_id
                                         AND bb.verified)))
                     -- "never verified" / "unverifiable", but there is no account to verify
                  OR (f.finding_type IN ('vendor_bank_unverified', 'vendor_bank_unverifiable')
                      AND f.detector = 'adapter:vendor_identity'
                      AND NULLIF(TRIM(bs.bank_account), '') IS NULL
                      AND NOT EXISTS (SELECT 1 FROM supplier_bank_accounts bb
                                       WHERE bb.supplier_id = bs.supplier_id
                                         AND bb.tenant_id = bs.tenant_id))
               )
        )
    )"""


# An AI-anomaly finding about a bill that has since been deleted or purged
# points at nothing — the user cannot find it. Narrow on purpose: other
# detectors' "bill" subjects can be records outside fact_invoices (Nova
# billing rows), so the rule is only applied where the id is known to be a bill.
NOT_DEAD_ANOMALY_BILL_SQL = """
    NOT (
        f.detector = 'adapter:ai_anomalies'
        AND f.subject_type = 'bill'
        AND NOT EXISTS (SELECT 1 FROM fact_invoices dfi
                         WHERE dfi.invoice_id = f.subject_id AND dfi.tenant_id = f.tenant_id)
        AND NOT EXISTS (SELECT 1 FROM stg_bills dsb
                         WHERE dsb.stg_bill_id = f.subject_id AND dsb.tenant_id = f.tenant_id)
    )"""


def _entitlement_clause(tenant: str) -> tuple[str, dict]:
    services = _licensed_services(tenant)
    if not services:
        return "TRUE", {}
    return "f.source_service = ANY(%(ent_services)s)", {"ent_services": services}


# ── GET /api/review/findings ──────────────────────────────────────────────────

# Sortable columns, as a whitelist mapping to SQL. A whitelist rather than a
# pass-through because the value lands in an ORDER BY, which no amount of
# parameterisation makes safe.
#
# Priority is a CASE and not the severity column itself: `severity_level` is a
# Postgres enum created outside this repo, so ordering by it would order by a
# declaration order this codebase cannot see. The CASE also puts CRITICAL at the
# TOP of the scale, so "descending" means "worst first" on this column exactly
# as it does on money and age — a sort direction that reverses its meaning
# column to column is a sort nobody trusts.
SORT_COLUMNS = {
    "rank":     "f.rank_score",
    "type":     "f.family",
    "vendor":   "vendor_label",
    "money":    "f.money_at_risk_inr",
    "priority": ("CASE f.severity::text WHEN 'CRITICAL' THEN 3 WHEN 'HIGH' THEN 2 "
                 "WHEN 'MEDIUM' THEN 1 ELSE 0 END"),
    "status":   "f.status",
    "age":      "f.first_detected_at",
}

# ── Rollups: several findings shown and counted as one card ──────────────────
# A vendor whose payments failed six times is one thing to deal with, not six
# rows; every FX problem is one place to look. A rollup is still N findings
# underneath — each line is resolved or discarded on its own — but the queue,
# its pager and every count show the group, so the number only drops once the
# last line is dealt with. Anything not named here rolls up to itself.
ROLLUP_KEY_SQL = """
    CASE
      WHEN f.finding_type = 'disbursement_failed' THEN 'pay:' || COALESCE(
          (SELECT e.entity_id::text FROM rh_finding_entities e
            WHERE e.finding_id = f.finding_id AND e.entity_type = 'vendor' LIMIT 1),
          f.finding_id::text)
      WHEN f.family = 'fx' THEN 'fx'
      ELSE f.finding_id::text
    END"""

# Columns of the rolled-up row a sort reads: the group's total, top rank and
# worst priority, so a group sorts where its most urgent member would.
ROLLED_SORT_COLUMNS = {
    "rank":     "roll_rank",
    "type":     "family",
    "vendor":   "vendor_label",
    "money":    "roll_money",
    "priority": "roll_sev",
    "status":   "status",
    "age":      "first_detected_at",
}


@router.get("/findings")
def list_findings(
    tenant: Tenant,
    lane: str | None = Query(default=None),
    scope: str = Query(default="actionable", pattern="^(actionable|visible|all)$"),
    family: list[str] | None = Query(default=None),
    severity: list[str] | None = Query(default=None),
    finding_type: list[str] | None = Query(default=None),
    group: bool = Query(default=True),
    subject_type: str | None = Query(default=None),
    subject_id: str | None = Query(default=None),
    status: str = Query(default="open", pattern="^(open|snoozed|resolved)$"),
    min_money_inr: float | None = Query(default=None),
    q: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    # Page-at-a-time, for the ungrouped queue. Keyset pagination cannot answer
    # "page 4 of 26" or "showing 76–100 of 636", and a flat queue of 636
    # findings is unusable without both. The grouped path keeps its cursor:
    # there the page is a page of *kinds*, and there are never many.
    offset: int = Query(default=0, ge=0),
    sort: str = Query(default="rank", pattern="^(rank|type|vendor|money|priority|status|age)$"),
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
):
    my_caps = capabilities_for()
    where = ["f.tenant_id = %(tenant)s"]
    params: dict = {"tenant": tenant, "limit": limit + 1}

    if status == "open":
        where += ["f.resolved_at IS NULL",
                  "(f.snoozed_until IS NULL OR f.snoozed_until <= now())"]
    elif status == "snoozed":
        where += ["f.resolved_at IS NULL", "f.snoozed_until > now()"]
    else:
        where.append("f.resolved_at IS NOT NULL")

    vis, vis_params = visibility_sql("f")
    where.append(vis)
    params.update(vis_params)

    ent, ent_params = _entitlement_clause(tenant)
    ent = f"({ent}) AND {NOT_ORPHAN_VENDOR_SQL} AND {NOT_STALE_BANK_SQL} AND {NOT_DEAD_ANOMALY_BILL_SQL}"
    where.append(ent)
    params.update(ent_params)

    if scope == "actionable":
        act, act_params = actionable_sql("f", my_caps)
        where.append(act)
        params.update(act_params)

    if lane:
        if lane == "suspected":
            where.append("f.verification IN ('UNVERIFIED','UNVERIFIABLE')")
        else:
            from ..review.ranking import LANE_BY_FAMILY
            fams = [fam for fam, ln in LANE_BY_FAMILY.items() if ln == lane]
            if not fams:
                raise HTTPException(400, f"unknown lane {lane!r}")
            where.append("f.family = ANY(%(lane_fams)s)")
            where.append("f.verification NOT IN ('UNVERIFIED','UNVERIFIABLE')")
            params["lane_fams"] = fams

    if family:
        where.append("f.family = ANY(%(families)s)")
        params["families"] = family
    if severity:
        where.append("f.severity::text = ANY(%(sevs)s)")
        params["sevs"] = [s.upper() for s in severity]
    if finding_type:
        where.append("f.finding_type = ANY(%(ftypes)s)")
        params["ftypes"] = finding_type
    if subject_type:
        where.append("f.subject_type = %(subject_type)s")
        params["subject_type"] = subject_type
    if subject_id:
        where.append("f.subject_id = %(subject_id)s")
        params["subject_id"] = subject_id
    if min_money_inr is not None:
        where.append("f.money_at_risk_inr >= %(min_money)s")
        params["min_money"] = min_money_inr
    if q:
        where.append("(f.title ILIKE %(q)s OR f.detail ILIKE %(q)s)")
        params["q"] = f"%{q}%"

    # A keyset cursor is "everything ranked below this point", which is only a
    # meaningful sentence under the ranking it was issued for. Under any other
    # sort it silently drops rows, so it is ignored rather than honoured wrong.
    cur_rank, cur_id = _decode_cursor(cursor if sort == "rank" else None)
    if cur_rank is not None:
        where.append("(f.rank_score, f.finding_id) < (%(cur_rank)s, %(cur_id)s)")
        params["cur_rank"], params["cur_id"] = cur_rank, cur_id

    entities_sql = """
               COALESCE((
                   SELECT json_agg(json_build_object(
                              'type', e.entity_type, 'id', e.entity_id,
                              'label', e.label, 'role', e.entity_role))
                     FROM rh_finding_entities e WHERE e.finding_id = f.finding_id
               ), '[]'::json) AS entities"""

    if group:
        # One card per KIND of problem, not per instance.
        #
        # 73 vendors each missing a bank verification is one thing to know and one
        # decision to make; rendering it as 73 identical cards is how a queue stops
        # being read. The representative is the highest-ranked member, and the
        # group carries the true count and the summed exposure so nothing is
        # understated by the collapse.
        #
        # The cursor moves to (group rank, finding_id), which stays a total order,
        # so keyset pagination still holds across grouped pages.
        sql = f"""
            WITH scoped AS (
                SELECT f.*, {entities_sql}
                  FROM rh_findings f
                 WHERE {' AND '.join(w for w in where if 'cur_rank' not in w)}
            ), grouped AS (
                SELECT scoped.*,
                       row_number() OVER w                        AS rn,
                       count(*)               OVER p              AS grp_count,
                       sum(money_at_risk_inr) OVER p              AS grp_money,
                       max(rank_score)        OVER p              AS grp_rank
                  FROM scoped
                WINDOW p AS (PARTITION BY finding_type),
                       w AS (PARTITION BY finding_type
                             ORDER BY rank_score DESC, finding_id DESC)
            )
            SELECT * FROM grouped
             WHERE rn = 1
               AND (%(cur_rank)s IS NULL
                    OR (grp_rank, finding_id) < (%(cur_rank)s, %(cur_id)s))
             ORDER BY grp_rank DESC, finding_id DESC
             LIMIT %(limit)s
        """
        params.setdefault("cur_rank", None)
        params.setdefault("cur_id", None)
        total_sql = None
    else:
        # The vendor label lives one table over, so sorting by it needs it in
        # the SELECT. The lookup is an index seek on rh_finding_entities' own
        # primary key, whose leading column is finding_id.
        order_col = ROLLED_SORT_COLUMNS[sort]
        order_dir = "ASC" if direction == "asc" else "DESC"
        sql = f"""
            WITH scoped AS (
                SELECT f.*, {entities_sql},
                       (SELECT e.label FROM rh_finding_entities e
                         WHERE e.finding_id = f.finding_id AND e.entity_type = 'vendor'
                         LIMIT 1)                                 AS vendor_label,
                       {ROLLUP_KEY_SQL}                           AS rollup_key,
                       {SORT_COLUMNS["priority"]}                 AS sev_rank
                  FROM rh_findings f
                 WHERE {' AND '.join(where)}
            ), rolled AS (
                SELECT scoped.*,
                       row_number() OVER r            AS rn,
                       count(*)               OVER p  AS roll_count,
                       sum(money_at_risk_inr) OVER p  AS roll_money,
                       max(rank_score)        OVER p  AS roll_rank,
                       max(sev_rank)          OVER p  AS roll_sev
                  FROM scoped
                WINDOW p AS (PARTITION BY rollup_key),
                       r AS (PARTITION BY rollup_key ORDER BY rank_score DESC, finding_id DESC)
            )
            SELECT * FROM rolled
             WHERE rn = 1
             ORDER BY {order_col} {order_dir} NULLS LAST, roll_rank DESC, finding_id DESC
             LIMIT %(limit)s OFFSET %(offset)s
        """
        params["offset"] = offset
        # One extra count so the pager can say "76–100 of 636". Same WHERE, and
        # every hot index on this table is partial on resolved_at IS NULL, so
        # it stays cheap on the live set.
        total_sql = (f"SELECT COUNT(DISTINCT {ROLLUP_KEY_SQL}) AS n "
                     f"FROM rh_findings f WHERE {' AND '.join(where)}")

    total = None
    with _PoolConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            if total_sql:
                cur.execute(total_sql, params)
                total = int((cur.fetchone() or {}).get("n") or 0)

    has_more = len(rows) > limit
    rows = rows[:limit]
    items = []
    for r in rows:
        item = _serialise(r, my_caps=my_caps)
        if not group and r.get("rollup_key") and r["rollup_key"] != str(r["finding_id"]):
            item["rollup"] = {
                "key": r["rollup_key"],
                "count": int(r.get("roll_count") or 1),
                "money_at_risk_inr": _num(r.get("roll_money")),
            }
        if group:
            n = int(r.get("grp_count") or 1)
            item["group"] = {
                "finding_type": r["finding_type"],
                "count": n,
                "money_at_risk_inr": _num(r.get("grp_money")),
            }
        items.append(item)

    # Same reason: a cursor handed back from a money-sorted or offset page
    # would be read as a rank keyset by the next request. Browse, which pages
    # this endpoint by cursor, asks for neither and still gets one.
    if has_more and rows and (group or (sort == "rank" and not offset)):
        last = rows[-1]
        key = last["grp_rank"] if group else last.get("roll_rank", last.get("rank_score"))
        next_cursor = _encode_cursor(key, str(last["finding_id"]))
    else:
        next_cursor = None

    return {"items": items, "next_cursor": next_cursor, "scope": scope,
            "lane": lane, "grouped": group,
            # Null on the grouped path, where there is no page to be 4 of.
            "total": total, "offset": offset if total is not None else None,
            "sort": sort, "direction": direction}


# ── GET /api/review/counts ────────────────────────────────────────────────────

@router.get("/counts")
def counts(tenant: Tenant):
    """Every number the UI promises is complete, in one call.

    The hub's whole information-architecture claim is "the census is complete,
    the presentation is bounded". That only holds if the counts are honest and
    cheap — if they cost N requests the header starts lying under load.
    """
    my_caps = capabilities_for()
    vis, vis_params = visibility_sql("f")
    act, act_params = actionable_sql("f", my_caps)
    ent, ent_params = _entitlement_clause(tenant)
    ent = f"({ent}) AND {NOT_ORPHAN_VENDOR_SQL} AND {NOT_STALE_BANK_SQL} AND {NOT_DEAD_ANOMALY_BILL_SQL}"

    params = {"tenant": tenant, **vis_params, **act_params, **ent_params}

    # Counted per rollup, the way the queue shows them: a vendor's six failed
    # payments are one item to do until the last of them is dealt with.
    sql = f"""
        WITH scoped AS (
            SELECT f.finding_id, f.family, f.severity, f.verification, f.rank_score,
                   f.money_at_risk_inr,
                   ({act})          AS actionable,
                   {ROLLUP_KEY_SQL} AS rollup_key
              FROM rh_findings f
             WHERE f.tenant_id = %(tenant)s
               AND f.resolved_at IS NULL
               AND (f.snoozed_until IS NULL OR f.snoozed_until <= now())
               AND {vis} AND {ent}
        ), rolled AS (
            SELECT scoped.*,
                   row_number() OVER (PARTITION BY rollup_key, actionable
                                      ORDER BY rank_score DESC, finding_id DESC) AS rn,
                   sum(money_at_risk_inr) OVER (PARTITION BY rollup_key, actionable) AS roll_money
              FROM scoped
        )
        SELECT family, severity::text AS severity, verification, actionable,
               COUNT(*)                       AS n,
               COALESCE(SUM(roll_money), 0)   AS money
          FROM rolled
         WHERE rn = 1
         GROUP BY family, severity, verification, actionable
    """
    extra_sql = f"""
        SELECT
          (SELECT COUNT(*) FROM rh_findings f
            WHERE f.tenant_id = %(tenant)s AND f.resolved_at IS NULL
              AND f.snoozed_until > now() AND {vis} AND {ent})            AS snoozed,
          (SELECT COUNT(*) FROM rh_findings f
            WHERE f.tenant_id = %(tenant)s AND f.resolved_at > now() - interval '7 days'
              AND {vis} AND {ent})                                        AS resolved_week,
          (SELECT COALESCE(SUM(f.money_at_risk_inr), 0) FROM rh_findings f
            WHERE f.tenant_id = %(tenant)s AND f.resolved_at > now() - interval '7 days'
              AND {vis} AND {ent})                                        AS protected_week,
          (SELECT COUNT(*) FROM rh_findings f
            WHERE f.tenant_id = %(tenant)s
              AND f.resolved_at >= date_trunc('month', now())
              AND {vis} AND {ent})                                        AS resolved_month,
          (SELECT COUNT(*) FROM rh_findings f
            WHERE f.tenant_id = %(tenant)s
              AND f.resolved_at >= date_trunc('day', now())
              AND {vis} AND {ent})                                        AS resolved_today,
          -- Of this month's closures, the ones closed the way the hub proposed
          -- rather than waved away. DISMISSED is the disagreement signal, so
          -- accepted/closed is the only honest read of "we were right".
          (SELECT COUNT(*) FROM rh_findings f
            WHERE f.tenant_id = %(tenant)s
              AND f.resolved_at >= date_trunc('month', now())
              AND f.status IN ('RESOLVED', 'AUTO_RESOLVED')
              AND {vis} AND {ent})                                        AS accepted_month,
          -- Due windows are counted over the *actionable* set only: telling
          -- someone two things are overdue when neither is theirs to fix is
          -- how a to-do number stops being believed.
          (SELECT COUNT(*) FROM rh_findings f
            WHERE f.tenant_id = %(tenant)s AND f.resolved_at IS NULL
              AND (f.snoozed_until IS NULL OR f.snoozed_until <= now())
              AND f.due_at IS NOT NULL AND f.due_at < now()
              AND ({act}) AND {vis} AND {ent})                            AS overdue,
          (SELECT COUNT(*) FROM rh_findings f
            WHERE f.tenant_id = %(tenant)s AND f.resolved_at IS NULL
              AND (f.snoozed_until IS NULL OR f.snoozed_until <= now())
              AND f.due_at >= now() AND f.due_at < now() + interval '3 days'
              AND ({act}) AND {vis} AND {ent})                            AS due_soon,
          (SELECT MAX(f.last_seen_at) FROM rh_findings f
            WHERE f.tenant_id = %(tenant)s)                               AS last_scan_at
    """

    with _PoolConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            cur.execute(extra_sql, params)
            extra = cur.fetchone()

    from ..review.ranking import lane_for

    lanes = {ln: {"actionable": 0, "visible": 0, "money_inr": 0.0} for ln in LANES}
    # Per type (family), for the queue's Type filter.
    by_family: dict[str, dict] = {}
    by_severity: dict[str, int] = {}
    total_actionable = total_visible = 0
    total_money = 0.0
    blocking_today = 0

    for r in rows:
        ln = lane_for(r["family"], r["verification"])
        n, money = int(r["n"]), float(r["money"] or 0)
        bucket = lanes.setdefault(ln, {"actionable": 0, "visible": 0, "money_inr": 0.0})
        bucket["visible"] += n
        bucket["money_inr"] += money
        total_visible += n
        total_money += money
        by_severity[r["severity"]] = by_severity.get(r["severity"], 0) + n
        fam = by_family.setdefault(r["family"], {"actionable": 0, "visible": 0})
        fam["visible"] += n
        if r["actionable"]:
            fam["actionable"] += n
        if r["actionable"]:
            bucket["actionable"] += n
            total_actionable += n
        if ln == "blocking":
            blocking_today += n

    return {
        "lanes": lanes,
        "by_family": by_family,
        "by_severity": by_severity,
        "total_actionable": total_actionable,
        "total_visible": total_visible,
        "money_at_risk_inr": total_money,
        "blocking": blocking_today,
        "snoozed": int(extra["snoozed"] or 0),
        "resolved_this_week": int(extra["resolved_week"] or 0),
        "protected_this_week_inr": float(extra["protected_week"] or 0),
        "resolved_this_month": int(extra["resolved_month"] or 0),
        "resolved_today": int(extra["resolved_today"] or 0),
        "accepted_this_month": int(extra["accepted_month"] or 0),
        "due_soon": int(extra["due_soon"] or 0),
        "overdue": int(extra["overdue"] or 0),
        "last_scan_at": _iso(extra["last_scan_at"]),
        "my_capabilities": my_caps,
        "role": current_role(),
    }


# ── GET /api/review/audit ─────────────────────────────────────────────────────

@router.get("/audit")
def audit_log(
    tenant: Tenant,
    finding_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Every change made from the Review Hub, newest first (review/audit.py).

    Who, when, from which finding, what was sent, and — for a vendor or a bill —
    the before → after of each field that changed.
    """
    where = ["a.tenant_id = %(tenant)s"]
    params: dict = {"tenant": tenant, "limit": limit, "offset": offset}
    if finding_id:
        try:
            _uuid.UUID(finding_id)
        except ValueError:
            raise HTTPException(400, "finding_id must be a uuid")
        where.append("a.finding_id = %(fid)s")
        params["fid"] = finding_id
    sql = f"""
        SELECT a.audit_id, a.created_at, a.actor, a.actor_role, a.finding_id, a.surface,
               a.method, a.path, a.entity_type, a.entity_id, a.request_body, a.changes,
               a.status_code,
               f.title AS finding_title, f.finding_type,
               COALESCE(s.name, fi.invoice_number) AS entity_label,
               fi.invoice_date AS bill_invoice_date
          FROM rh_audit_log a
          LEFT JOIN rh_findings f    ON f.finding_id = a.finding_id
          LEFT JOIN dim_suppliers s  ON a.entity_type = 'vendor' AND s.supplier_id::text = a.entity_id
          LEFT JOIN fact_invoices fi ON a.entity_type = 'bill'   AND fi.invoice_id::text = a.entity_id
         WHERE {' AND '.join(where)}
         ORDER BY a.created_at DESC, a.audit_id DESC
         LIMIT %(limit)s OFFSET %(offset)s
    """
    with _PoolConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            cur.execute(f"SELECT COUNT(*) AS n FROM rh_audit_log a WHERE {' AND '.join(where)}", params)
            total = int(cur.fetchone()["n"])
    def _changes(r) -> dict:
        ch = dict(r["changes"] or {})
        # Entries recorded before the snapshot carried terms only have the due
        # date. Say it as terms too, from the bill's own invoice date.
        due = ch.get("due_date")
        inv = r.get("bill_invoice_date")
        if due and "payment_terms_days" not in ch and inv:
            def days(d):
                try:
                    return (datetime.fromisoformat(str(d)[:10]).date() - inv).days if d else None
                except ValueError:
                    return None
            before, after = days(due[0]), days(due[1])
            if before != after:
                ch = {"payment_terms_days": [before, after], **ch}
        return ch

    items = [{
        "audit_id": r["audit_id"], "at": _iso(r["created_at"]),
        "actor": r["actor"], "actor_role": r["actor_role"],
        "finding_id": str(r["finding_id"]) if r["finding_id"] else None,
        "finding_title": r["finding_title"], "finding_type": r["finding_type"],
        "surface": r["surface"], "method": r["method"], "path": r["path"],
        "entity_type": r["entity_type"], "entity_id": r["entity_id"], "entity_label": r["entity_label"],
        "body": r["request_body"] or {}, "changes": _changes(r),
        "status_code": r["status_code"],
    } for r in rows]
    return {"items": items, "total": total, "offset": offset}


# ── GET /api/review/findings/{finding_id}/rollup ──────────────────────────────

@router.get("/findings/{finding_id}/rollup")
def rollup_members(
    finding_id: str,
    tenant: Tenant,
    status: str = Query(default="open", pattern="^(open|snoozed)$"),
    scope: str = Query(default="actionable", pattern="^(actionable|visible)$"),
):
    """Every line of the card this finding is shown in, best-ranked first.

    Same visibility and licensing as the queue, so a line the user could not
    see as a row of its own never appears inside a card either.
    """
    my_caps = capabilities_for()
    vis, vis_params = visibility_sql("f")
    ent, ent_params = _entitlement_clause(tenant)
    ent = f"({ent}) AND {NOT_ORPHAN_VENDOR_SQL} AND {NOT_STALE_BANK_SQL} AND {NOT_DEAD_ANOMALY_BILL_SQL}"
    live = ("(f.snoozed_until IS NULL OR f.snoozed_until <= now())" if status == "open"
            else "f.snoozed_until > now()")
    # The same scope the queue row was counted under, so a card never holds
    # more lines than the row said it had.
    act, act_params = actionable_sql("f", my_caps) if scope == "actionable" else ("TRUE", {})
    sql = f"""
        WITH me AS (
            SELECT {ROLLUP_KEY_SQL} AS k
              FROM rh_findings f
             WHERE f.finding_id = %(fid)s AND f.tenant_id = %(tenant)s
        )
        SELECT f.*,
               COALESCE((
                   SELECT json_agg(json_build_object(
                              'type', e.entity_type, 'id', e.entity_id,
                              'label', e.label, 'role', e.entity_role))
                     FROM rh_finding_entities e WHERE e.finding_id = f.finding_id
               ), '[]'::json) AS entities
          FROM rh_findings f, me
         WHERE f.tenant_id = %(tenant)s
           AND f.resolved_at IS NULL
           AND {live} AND {vis} AND {ent} AND {act}
           AND {ROLLUP_KEY_SQL} = me.k
         ORDER BY f.rank_score DESC, f.finding_id DESC
         LIMIT 200
    """
    try:
        _uuid.UUID(finding_id)
    except ValueError:
        raise HTTPException(404, "Finding not found")
    with _PoolConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, {"fid": finding_id, "tenant": tenant,
                              **vis_params, **ent_params, **act_params})
            rows = cur.fetchall()
    return {"items": [_serialise(r, my_caps=my_caps) for r in rows]}


# ── GET /api/review/findings/{finding_id} ─────────────────────────────────────

@router.get("/findings/{finding_id}")
def get_finding(finding_id: str, tenant: Tenant):
    my_caps = capabilities_for()
    vis, vis_params = visibility_sql("f")

    with _PoolConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT f.*,
                       COALESCE((
                           SELECT json_agg(json_build_object(
                                      'type', e.entity_type, 'id', e.entity_id,
                                      'label', e.label, 'role', e.entity_role))
                             FROM rh_finding_entities e WHERE e.finding_id = f.finding_id
                       ), '[]'::json) AS entities
                  FROM rh_findings f
                 WHERE f.tenant_id = %(tenant)s AND f.finding_id = %(fid)s AND {vis}
                """,
                {"tenant": tenant, "fid": finding_id, **vis_params},
            )
            row = cur.fetchone()
            # 404 rather than 403 for a finding in another tenant, matching
            # security/tenancy.py — the endpoint must not be an existence oracle.
            if not row:
                raise HTTPException(404, "Finding not found")

            cur.execute(
                """
                SELECT event_type, actor, reason, payload_jsonb, created_at
                  FROM rh_finding_events
                 WHERE finding_id = %s ORDER BY created_at DESC LIMIT 50
                """,
                (finding_id,),
            )
            events = cur.fetchall()

            # Cluster = other live findings of the same type in this tenant.
            cur.execute(
                f"""
                SELECT f.finding_id, f.subject_id, f.title, f.money_at_risk_inr,
                       f.condition_hash
                  FROM rh_findings f
                 WHERE f.tenant_id = %(tenant)s AND f.finding_type = %(ft)s
                   AND f.resolved_at IS NULL AND f.finding_id <> %(fid)s AND {vis}
                 ORDER BY f.money_at_risk_inr DESC NULLS LAST LIMIT 200
                """,
                {"tenant": tenant, "ft": row["finding_type"], "fid": finding_id, **vis_params},
            )
            siblings = cur.fetchall()

            # What this finding can show without leaving the hub. Computed on
            # read, not stored: an approval that landed since the last scan has
            # to appear here or the preview is a prettier way of being stale.
            evidence = row.get("evidence_jsonb") or {}
            if isinstance(evidence, str):
                evidence = json.loads(evidence)
            available_previews = review_previews.previews_for(cur, tenant, row, evidence)

            # Whether this bill is waiting on *this* caller is a fact about the
            # bill and the chain, not about the finding, so it is answered here
            # rather than stored at scan time — a bill approved ten minutes ago
            # must not still offer an Approve button.
            bill_id = review_previews.bill_subject_id(row)
            bill = review_previews.bill_row(cur, tenant, bill_id) if bill_id else None
            approve = (review_bill_actions.approve_action(cur, tenant, bill_id, bill)
                       if bill else None)

    out = _serialise(row, my_caps=my_caps)
    out["previews"] = available_previews
    if approve and approve["capability"] in my_caps:
        out["actions"] = [approve, *out["actions"]]
    out["events"] = [
        {"type": e["event_type"], "actor": e["actor"], "reason": e["reason"],
         "at": _iso(e["created_at"])}
        for e in events
    ]
    # `homogeneous` gates the one-click bulk button in the UI. If every sibling
    # shares this finding's condition_hash they are literally the same problem
    # with the same fix; if not, the UI must only offer "review them", because a
    # one-click bulk write across members that quietly differ is the single
    # fastest way to lose a finance team's trust permanently.
    homogeneous = all(s["condition_hash"] == row["condition_hash"] for s in siblings)
    out["cluster"] = {
        "size": len(siblings) + 1,
        "homogeneous": homogeneous,
        "sample": [
            {"finding_id": str(s["finding_id"]), "subject_id": str(s["subject_id"]) if s["subject_id"] else None,
             "title": s["title"], "money_at_risk_inr": _num(s["money_at_risk_inr"]),
             "differs": s["condition_hash"] != row["condition_hash"]}
            for s in siblings[:50]
        ],
        "money_at_risk_inr": (out["money_at_risk_inr"] or 0)
                             + sum(float(s["money_at_risk_inr"] or 0) for s in siblings),
    }
    return out


# ── GET /api/review/findings/{finding_id}/preview/{preview_id} ────────────────

@router.get("/findings/{finding_id}/preview/{preview_id}")
def get_finding_preview(finding_id: str, preview_id: str, tenant: Tenant):
    """One preview's payload, in an ordinary evidence shape.

    Guarded by the same visibility predicate as the finding itself: a preview is
    a second way to read a bill, so it has to answer to the first way's rules.
    Fetched only when the user opens it, which is why the menu carries ids and
    not payloads.
    """
    with _PoolConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            row = _load_owned(cur, tenant, finding_id)
            evidence = row.get("evidence_jsonb") or {}
            if isinstance(evidence, str):
                evidence = json.loads(evidence)
            out = review_previews.build_preview(cur, tenant, row, evidence, preview_id)

    if not out:
        raise HTTPException(404, "No such preview for this finding")
    return out


# ── POST /api/review/act/approve-bill ─────────────────────────────────────────

class ApproveBillRequest(BaseModel):
    finding_id: str
    comment: str | None = None


@router.post("/act/approve-bill")
def act_approve_bill(body: ApproveBillRequest, tenant: Tenant):
    """Approve the bill a finding is about, by asking the endpoint that owns it.

    The hub adds two things to that handler and takes nothing away: the finding
    must be one this caller can see, and the actor is read from the token rather
    than from the request body. Everything that decides whether the approval is
    allowed — the chain, the role, the assignee list — still happens inside
    bill_action, so this can never be a looser path than the Bills screen.
    """
    require_capability("approve")

    with _PoolConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            row = _load_owned(cur, tenant, body.finding_id)
            bill_id = review_previews.bill_subject_id(row)
            if not bill_id:
                raise HTTPException(400, "That finding is not about a bill")
            bill = review_previews.bill_row(cur, tenant, bill_id)
            if not bill:
                raise HTTPException(404, "Bill not found")
            if not review_bill_actions.can_approve(cur, tenant, bill):
                raise HTTPException(403, "This bill is not waiting for your approval")
            actor_name = review_bill_actions.actor_display_name(cur, tenant, current_username() or "")

    # Imported here rather than at module scope: routers/bill_approvals imports
    # routers/bills, and a top-level import would make the review router part of
    # that cycle.
    from .bill_approvals import BillActionRequest, bill_action

    bill_action(bill_id, BillActionRequest(
        action="approve",
        actor_role=current_role() or "",
        actor_name=actor_name,
        comment=body.comment,
    ))

    # The finding said this bill was stuck. It is not stuck any more, so it goes
    # out of the queue now rather than at the next scan — a hub that still shows
    # what you just fixed is a hub you stop trusting.
    set_finding_status(body.finding_id,
                       StatusRequest(status="RESOLVED", reason="approved_from_review"),
                       tenant)

    return {"status": "ok", "message": f"Approved {bill.get('invoice_number') or 'the bill'}."}


# ── Lifecycle ─────────────────────────────────────────────────────────────────

# Five fixed reasons, not free text. A free-text dismissal reads well and tells
# the system nothing; these are the only signal that says which finding types are
# worth keeping, and "never_show_type" is simultaneously the user's mute and the
# input to per-type auto-suppression.
DISMISS_REASONS = ("not_an_issue", "already_handled", "data_is_wrong",
                   "not_my_job", "never_show_type")

UNDO_TTL_SECONDS = 30


class StatusRequest(BaseModel):
    status: str = Field(pattern="^(ACKNOWLEDGED|SNOOZED|RESOLVED|DISMISSED|OPEN)$")
    reason: str | None = None
    snooze_days: int | None = Field(default=None, ge=1, le=365)
    suppress_scope: str | None = Field(default=None,
                                       pattern="^(exact_key|type_on_subject|type_tenant_wide)$")


def _load_owned(cur, tenant: str, finding_id: str) -> dict:
    vis, vis_params = visibility_sql("f")
    cur.execute(
        f"SELECT f.* FROM rh_findings f "
        f"WHERE f.tenant_id = %(tenant)s AND f.finding_id = %(fid)s AND {vis}",
        {"tenant": tenant, "fid": finding_id, **vis_params},
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Finding not found")
    return row


def _write_event(cur, finding_id: str, tenant: str, event_type: str,
                 actor: str | None, reason: str | None = None, payload: dict | None = None):
    cur.execute(
        "INSERT INTO rh_finding_events (finding_id, tenant_id, event_type, actor, reason, payload_jsonb) "
        "VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
        (finding_id, tenant, event_type, actor, reason, json.dumps(payload or {})),
    )


@router.post("/findings/{finding_id}/status")
def set_finding_status(finding_id: str, body: StatusRequest, tenant: Tenant):
    actor = current_username() or "unknown"
    terminal = body.status in ("RESOLVED", "DISMISSED")

    if terminal:
        require_capability("edit")
    if body.status == "DISMISSED" and body.reason not in DISMISS_REASONS:
        raise HTTPException(
            400, f"A dismissal needs one of: {', '.join(DISMISS_REASONS)}"
        )

    snooze_until = (
        datetime.now(timezone.utc) + timedelta(days=body.snooze_days or 7)
        if body.status == "SNOOZED" else None
    )

    with _PoolConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            prev = _load_owned(cur, tenant, finding_id)

            cur.execute(
                """
                UPDATE rh_findings
                   SET status = %s, status_reason = %s, status_by = %s, status_at = now(),
                       snoozed_until = %s,
                       resolved_at = CASE WHEN %s THEN now() ELSE NULL END,
                       updated_at = now()
                 WHERE tenant_id = %s AND finding_id = %s
                RETURNING *
                """,
                (body.status, body.reason, actor, snooze_until, terminal, tenant, finding_id),
            )
            row = cur.fetchone()
            _write_event(cur, finding_id, tenant, body.status, actor, body.reason)

            # Per-type tallies. These drive auto-suppression: a type that people
            # keep dismissing stops being shown, which is the self-correcting
            # loop that keeps an open-ended detector from becoming noise.
            if prev["type_id"] and body.status in ("RESOLVED", "DISMISSED"):
                col = "confirmed_count" if body.status == "RESOLVED" else "dismissed_count"
                cur.execute(
                    f"UPDATE rh_finding_types SET {col} = {col} + 1, updated_at = now() "
                    f"WHERE type_id = %s",
                    (prev["type_id"],),
                )

            if body.suppress_scope:
                cur.execute(
                    """
                    INSERT INTO rh_suppressions
                        (tenant_id, scope, finding_key, finding_type, subject_type,
                         subject_id, reason, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (tenant, body.suppress_scope, prev["finding_key"], prev["finding_type"],
                     prev["subject_type"], prev["subject_id"],
                     body.reason or "muted from the review hub", actor),
                )

            token = None
            if terminal or body.status == "SNOOZED":
                import secrets
                token = secrets.token_urlsafe(18)
                keep = {k: prev[k] for k in
                        ("status", "status_reason", "status_by", "status_at",
                         "snoozed_until", "resolved_at")}
                cur.execute(
                    "INSERT INTO rh_undo_tokens "
                    "(token, tenant_id, finding_id, prev_state, created_by, expires_at) "
                    "VALUES (%s, %s, %s, %s::jsonb, %s, now() + interval '%s seconds')",
                    (token, tenant, finding_id, json.dumps(keep, default=str),
                     actor, UNDO_TTL_SECONDS),
                )

    return {
        "finding": _serialise(row, my_caps=capabilities_for()),
        "undo_token": token,
        "undo_expires_in": UNDO_TTL_SECONDS if token else None,
    }


class UndoRequest(BaseModel):
    undo_token: str


@router.post("/findings/{finding_id}/undo")
def undo_finding(finding_id: str, body: UndoRequest, tenant: Tenant):
    """Restore the state before the last lifecycle change.

    Server-backed rather than client-side, because at triage speed a user
    resolves eight findings in ten seconds and needs the third one back after it
    has already committed.
    """
    actor = current_username() or "unknown"
    with _PoolConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "UPDATE rh_undo_tokens SET used_at = now() "
                " WHERE token = %s AND tenant_id = %s AND finding_id = %s "
                "   AND used_at IS NULL AND expires_at > now() "
                "RETURNING prev_state",
                (body.undo_token, tenant, finding_id),
            )
            tok = cur.fetchone()
            if not tok:
                raise HTTPException(410, "That undo has expired or was already used.")

            prev = tok["prev_state"]
            if isinstance(prev, str):
                prev = json.loads(prev)

            cur.execute(
                """
                UPDATE rh_findings
                   SET status = %s, status_reason = %s, status_by = %s,
                       status_at = %s, snoozed_until = %s, resolved_at = %s,
                       updated_at = now()
                 WHERE tenant_id = %s AND finding_id = %s
                RETURNING *
                """,
                (prev.get("status") or "OPEN", prev.get("status_reason"), prev.get("status_by"),
                 prev.get("status_at"), prev.get("snoozed_until"), prev.get("resolved_at"),
                 tenant, finding_id),
            )
            row = cur.fetchone()
            _write_event(cur, finding_id, tenant, "COMMENT", actor, "undo")

    return {"finding": _serialise(row, my_caps=capabilities_for())}


# ── Bulk ──────────────────────────────────────────────────────────────────────

class BulkRequest(BaseModel):
    finding_ids: list[str] = Field(default_factory=list, max_length=500)
    finding_type: str | None = None
    # OPEN is the un-snooze: the UPDATE below writes snoozed_until = NULL for
    # every non-SNOOZED status, so sending it puts a batch straight back in the
    # queue. The single-finding endpoint has always taken it; leaving it out
    # here meant the Snoozed tab's own bulk bar could only push findings
    # further away, never pull one back.
    status: str = Field(pattern="^(ACKNOWLEDGED|SNOOZED|RESOLVED|DISMISSED|OPEN)$")
    reason: str | None = None
    snooze_days: int | None = Field(default=None, ge=1, le=365)


@router.post("/findings/bulk")
def bulk_status(body: BulkRequest, tenant: Tenant):
    """Apply one decision to a cluster. Returns per-member results, never a bare count.

    Partial failure has to be visible per member — "some failed" in a toast is
    useless when the thing that failed is a ₹3.4L bill.
    """
    require_capability("edit")
    actor = current_username() or "unknown"
    terminal = body.status in ("RESOLVED", "DISMISSED")
    if body.status == "DISMISSED" and body.reason not in DISMISS_REASONS:
        raise HTTPException(400, f"A dismissal needs one of: {', '.join(DISMISS_REASONS)}")

    snooze_until = (
        datetime.now(timezone.utc) + timedelta(days=body.snooze_days or 7)
        if body.status == "SNOOZED" else None
    )

    vis, vis_params = visibility_sql("f")
    ok: list[str] = []
    failed: list[dict] = []

    # psycopg2 renders a list of str as ARRAY['…'], which types as text[], and
    # `uuid = ANY(text[])` has no operator in Postgres — so this whole endpoint
    # 500'd on every by-id call ("operator does not exist: uuid = text"),
    # which is every use of the queue's own bulk bar. Casting the parameter
    # rather than the column keeps the primary-key index in play; the same
    # file already does this at the vendor lookup below. Ids are canonicalised
    # first so a malformed one is reported against itself instead of failing
    # the batch on an invalid-uuid cast.
    valid_ids: list[str] = []
    for raw in body.finding_ids:
        try:
            valid_ids.append(str(_uuid.UUID(str(raw))))
        except (ValueError, AttributeError, TypeError):
            failed.append({"finding_id": str(raw), "error": "not a valid finding id"})

    if body.finding_ids and not valid_ids:
        return {"updated": ok, "failed": failed,
                "counts": {"updated": len(ok), "failed": len(failed)}}

    with _PoolConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if body.finding_ids:
                cur.execute(
                    f"SELECT f.finding_id FROM rh_findings f "
                    f" WHERE f.tenant_id = %(tenant)s AND f.finding_id = ANY(%(ids)s::uuid[]) "
                    f"   AND f.resolved_at IS NULL AND {vis}",
                    {"tenant": tenant, "ids": valid_ids, **vis_params},
                )
            elif body.finding_type:
                cur.execute(
                    f"SELECT f.finding_id FROM rh_findings f "
                    f" WHERE f.tenant_id = %(tenant)s AND f.finding_type = %(ft)s "
                    f"   AND f.resolved_at IS NULL AND {vis} LIMIT 500",
                    {"tenant": tenant, "ft": body.finding_type, **vis_params},
                )
            else:
                raise HTTPException(400, "Give either finding_ids or finding_type.")

            targets = [str(r["finding_id"]) for r in cur.fetchall()]
            requested = set(valid_ids or targets)
            for missing in requested - set(targets):
                failed.append({"finding_id": missing,
                               "error": "not found, already resolved, or not visible to you"})

            # Each member gets its own savepoint. Without one, this loop's
            # per-member error reporting was fiction: psycopg2 runs the whole
            # request in one transaction, so the first member to raise left it
            # aborted and every statement after it came back "current
            # transaction is aborted, commands ignored" — 1 real error and 15
            # pieces of noise, none of which name what actually went wrong.
            # Rolling back to the savepoint puts the transaction back in a
            # usable state, so a member that genuinely cannot be updated costs
            # only itself and the rest of the batch still commits.
            for fid in targets:
                try:
                    cur.execute("SAVEPOINT bulk_member")
                    cur.execute(
                        """
                        UPDATE rh_findings
                           SET status = %s, status_reason = %s, status_by = %s,
                               status_at = now(), snoozed_until = %s,
                               resolved_at = CASE WHEN %s THEN now() ELSE NULL END,
                               updated_at = now()
                         WHERE tenant_id = %s AND finding_id = %s
                        """,
                        (body.status, body.reason, actor, snooze_until, terminal, tenant, fid),
                    )
                    _write_event(cur, fid, tenant, body.status, actor, body.reason,
                                 {"bulk": True})
                    cur.execute("RELEASE SAVEPOINT bulk_member")
                    ok.append(fid)
                except Exception as exc:
                    # Legal while the transaction is aborted — it is the only
                    # thing that is.
                    cur.execute("ROLLBACK TO SAVEPOINT bulk_member")
                    failed.append({"finding_id": fid, "error": str(exc)})

    return {"updated": ok, "failed": failed,
            "counts": {"updated": len(ok), "failed": len(failed)}}



# ── POST /api/review/bill-coding/apply ────────────────────────────────────────

class BillCodingApply(BaseModel):
    finding_id: str
    invoice_id: str
    #: proposal ids to accept — a subset, in any order. "category",
    #: "cost_centre", "gst_split", "ledger:{line_number}".
    accept: list[str] = Field(default_factory=list, max_length=100)


@router.post("/bill-coding/apply")
def apply_bill_coding(body: BillCodingApply, tenant: Tenant):
    """Accept some of a bill's proposed corrections.

    Partial by design. The finding bundles four unrelated kinds of wrong on one
    document precisely so they can be judged separately, and forcing all-or-
    nothing at the last step would undo that — someone who agrees about the
    category and not about the cost centre has to be able to say so.

    Every write goes through the same columns the Bills screen writes, and each
    one is recorded as its own event, so the bill's history reads the same
    whether a person edited it by hand or accepted it here.

    The finding is NOT resolved here. `scan()` yields the current truth, so a
    bill with nothing left to propose stops being asserted and the worker
    auto-resolves it on the next pass. Closing it from this side would be a
    second opinion about the same condition, and the two would disagree the
    first time a write silently failed.
    """
    require_capability("edit")
    actor = current_username() or "unknown"
    accept = [a for a in body.accept if a]
    if not accept:
        raise HTTPException(400, "Nothing selected.")

    applied: list[str] = []
    failed: list[dict] = []

    with _PoolConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            finding = _load_owned(cur, tenant, body.finding_id)
            proposals = {
                p["id"]: p
                for p in (finding.get("evidence_jsonb") or {}).get("proposals", [])
            }
            if not proposals:
                raise HTTPException(409, "This finding no longer proposes anything.")

            # The bill is re-read under the tenant rather than trusted from the
            # request: a finding id and an invoice id arriving together is not
            # evidence that they belong to each other.
            cur.execute(
                "SELECT invoice_id, COALESCE(stg_bill_id, archived_stg_bill_id) AS stg_bill_id "
                "  FROM fact_invoices WHERE invoice_id = %s AND tenant_id = %s",
                (body.invoice_id, tenant),
            )
            bill = cur.fetchone()
            if not bill:
                raise HTTPException(404, "Bill not found")
            stg_bill_id = bill["stg_bill_id"]

            for pid in accept:
                p = proposals.get(pid)
                if not p:
                    failed.append({"id": pid, "error": "no longer proposed"})
                    continue
                if not p.get("writable"):
                    failed.append({"id": pid, "error": "not something we can write"})
                    continue
                try:
                    # Savepoint per proposal, for the same reason the bulk
                    # endpoint has one: without it the first failure aborts the
                    # transaction and every later proposal reports someone
                    # else's error.
                    cur.execute("SAVEPOINT coding_item")
                    _apply_proposal(cur, tenant, body.invoice_id, stg_bill_id, pid, p)
                    _write_event(
                        cur, body.finding_id, tenant, "ACTION_EXECUTED", actor,
                        f"{p.get('label') or pid}: "
                        f"{p.get('current') or 'not set'} -> {p.get('proposed')}",
                        {"proposal": pid},
                    )
                    cur.execute("RELEASE SAVEPOINT coding_item")
                    applied.append(pid)
                except Exception as exc:
                    cur.execute("ROLLBACK TO SAVEPOINT coding_item")
                    failed.append({"id": pid, "error": str(exc)})

            # Wake the adapter so the finding re-scans now rather than on the
            # next unrelated write — the user is watching the drawer.
            if applied:
                cur.execute("SELECT pg_notify('ch_review_adapters', '')")

    return {"applied": applied, "failed": failed,
            "counts": {"applied": len(applied), "failed": len(failed)}}


def _apply_proposal(cur, tenant: str, invoice_id: str, stg_bill_id, pid: str, p: dict) -> None:
    if pid == "category":
        cur.execute(
            "UPDATE fact_invoices SET category = %s WHERE invoice_id = %s AND tenant_id = %s",
            (p["proposed"], invoice_id, tenant),
        )
        if stg_bill_id:
            cur.execute("UPDATE stg_bills SET category = %s WHERE stg_bill_id = %s",
                        (p["proposed"], stg_bill_id))
        return

    if pid == "payment_terms":
        days = p.get("days")
        if days is None:
            raise ValueError("no agreed term on this proposal")
        # The term is the gap between the dates, so moving the term means
        # moving the due date — computed in SQL off the bill's own invoice_date
        # rather than a date the request supplied.
        cur.execute(
            "UPDATE fact_invoices"
            "   SET due_date = invoice_date + (%s || ' days')::interval"
            " WHERE invoice_id = %s AND tenant_id = %s AND invoice_date IS NOT NULL",
            (str(int(days)), invoice_id, tenant),
        )
        if cur.rowcount == 0:
            raise ValueError("this bill has no date to count the term from")
        if stg_bill_id:
            cur.execute(
                "UPDATE stg_bills"
                "   SET due_date = bill_date + (%s || ' days')::interval"
                " WHERE stg_bill_id = %s AND bill_date IS NOT NULL",
                (str(int(days)), stg_bill_id),
            )
        return

    if pid == "department":
        cur.execute(
            "UPDATE fact_invoices SET department = %s WHERE invoice_id = %s AND tenant_id = %s",
            (p["proposed"], invoice_id, tenant),
        )
        return

    if pid == "cost_centre":
        # Resolved by name under this tenant, never by an id from the payload.
        cur.execute(
            "SELECT id FROM cost_centers WHERE tenant_id = %s AND name = %s AND is_active LIMIT 1",
            (tenant, p["proposed"]),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"no active cost centre named {p['proposed']!r}")
        cur.execute(
            "UPDATE fact_invoices SET cost_center_id = %s WHERE invoice_id = %s AND tenant_id = %s",
            (row["id"], invoice_id, tenant),
        )
        return

    if pid.startswith("ledger:"):
        line_number = int(pid.split(":", 1)[1])
        account_id = p.get("account_id")
        if not account_id:
            raise ValueError("no ledger account on this proposal")
        if not stg_bill_id:
            raise ValueError("this bill has no staging row to map lines on")
        cur.execute(
            "UPDATE stg_bill_line_items SET account_id = %s "
            " WHERE stg_bill_id = %s AND line_number = %s AND tenant_id = %s",
            (account_id, stg_bill_id, line_number, tenant),
        )
        if cur.rowcount == 0:
            raise ValueError(f"line {line_number} is not on this bill")
        return

    if pid == "gst_split":
        if not stg_bill_id:
            raise ValueError("this bill has no staging row to hold the split")
        amount = float(p.get("amount") or 0)
        rate = p.get("rate")
        if amount <= 0:
            raise ValueError("nothing to move")
        # The total is untouched either way — this moves the same rupees
        # between columns, it does not re-tax the bill.
        if "half" in p:                      # IGST → CGST + SGST
            half = round(amount / 2, 2)
            cur.execute(
                "UPDATE stg_bills SET igst_amount = NULL, igst_rate = NULL,"
                "       cgst_amount = %s, sgst_amount = %s,"
                "       cgst_rate = %s, sgst_rate = %s"
                " WHERE stg_bill_id = %s",
                (half, round(amount - half, 2), rate, rate, stg_bill_id),
            )
        else:                                # CGST + SGST → IGST
            cur.execute(
                "UPDATE stg_bills SET cgst_amount = NULL, sgst_amount = NULL,"
                "       cgst_rate = NULL, sgst_rate = NULL,"
                "       igst_amount = %s, igst_rate = %s"
                " WHERE stg_bill_id = %s",
                (amount, rate, stg_bill_id),
            )
        return

    raise ValueError(f"unknown proposal {pid!r}")


# ── Rescan ────────────────────────────────────────────────────────────────────

class RescanRequest(BaseModel):
    subject_type: str = "tenant"
    subject_id: str | None = None
    tier: str = Field(default="standard", pattern="^(cheap|standard|deep)$")


@router.post("/rescan")
def rescan(body: RescanRequest, tenant: Tenant):
    """Queue an agent review. The API never calls a model itself.

    This process is sync psycopg2 on a pool of 50; an inline LLM call would hold
    a connection for seconds. So this inserts a row and notifies — the pipeline
    worker owns everything expensive.
    """
    import hashlib

    actor = current_username() or "unknown"
    idem = hashlib.sha256(
        f"{tenant}|{body.subject_type}|{body.subject_id}|user|{actor}|"
        f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}".encode()
    ).hexdigest()

    with _PoolConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO rh_review_requests
                    (tenant_id, subject_type, subject_id, trigger_type, tier,
                     idempotency_key, requested_by)
                VALUES (%s, %s, %s, 'user', %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING request_id
                """,
                (tenant, body.subject_type, body.subject_id, body.tier, idem, actor),
            )
            row = cur.fetchone()
            cur.execute("SELECT pg_notify('ch_review_agent', '')")

    return {
        "request_id": str(row["request_id"]) if row else None,
        # A duplicate inside the same minute is the double-click case, not an error.
        "queued": bool(row),
    }


# ── Executable fixes ──────────────────────────────────────────────────────────
#
# The only writes the hub performs. Each one is narrow, reversible in practice,
# and delegates to the code that already owns the operation. Anything that moves
# money or carries an approval decision is a deep link instead, so the user ends
# up on the screen that already has the OTP gate, the confirmations and the audit
# trail (docs/PAYMENT_GUARDRAILS.md).


class MergeMisreadVendorRequest(BaseModel):
    finding_id: str
    source_supplier_id: str
    target_supplier_id: str


def _norm_name(v: str | None) -> str:
    return (v or "").strip().lower()


@router.post("/act/merge-misread-vendor")
def act_merge_misread_vendor(body: MergeMisreadVendorRequest, tenant: Tenant):
    """Fold a vendor created from a misread GSTIN into the verified record.

    The PAN-equality guard used by /api/vendors/{id}/merge cannot apply here: the
    PANs differ *because* the GSTIN was misread, which is the entire finding. So
    this re-derives the narrower guard on the server rather than trusting the
    finding that proposed it — a client could post any two vendor ids.
    """
    require_capability("edit")
    actor = current_username() or "unknown"

    if body.source_supplier_id == body.target_supplier_id:
        raise HTTPException(400, "Cannot merge a vendor into itself")

    with _PoolConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT supplier_id, name, gstn, pan, status, is_active "
                "  FROM dim_suppliers WHERE tenant_id = %s AND supplier_id = ANY(%s::uuid[])",
                (tenant, [body.source_supplier_id, body.target_supplier_id]),
            )
            found = {str(r["supplier_id"]): r for r in cur.fetchall()}
            src = found.get(body.source_supplier_id)
            tgt = found.get(body.target_supplier_id)
            # 404 rather than 403 for another tenant's row, matching tenancy.py.
            if not src or not tgt:
                raise HTTPException(404, "Vendor not found")

            src_gstn = (src["gstn"] or "").strip()
            tgt_gstn = (tgt["gstn"] or "").strip()

            if _norm_name(src["name"]) != _norm_name(tgt["name"]):
                raise HTTPException(400, "These two vendors do not have the same name")
            if tgt["status"] != "VERIFIED":
                raise HTTPException(400, "The surviving vendor must be a verified one")
            if src["status"] == "VERIFIED":
                raise HTTPException(400, "Refusing to retire a verified vendor record")
            if len(src_gstn) != 15 or len(tgt_gstn) != 15:
                raise HTTPException(400, "Both records need a full GSTIN to compare")
            if src_gstn[:2] != tgt_gstn[:2]:
                raise HTTPException(
                    400, "The state codes differ — these are two registrations, not a misread"
                )
            differing = sum(1 for a, b in zip(src_gstn, tgt_gstn) if a != b)
            if differing == 0 or differing > 6:
                raise HTTPException(
                    400,
                    f"The GSTINs differ in {differing} characters — too many to treat as a "
                    "misread. Merge these by hand if they really are the same vendor.",
                )

            moved = repoint_and_retire(
                cur, body.source_supplier_id, body.target_supplier_id,
                source_gstn=None,           # never carry the misread GSTIN to the survivor
                source_pan=None,
            )

            # Close the finding here rather than waiting for the next adapter pass,
            # so the card disappears the moment the user fixes it.
            cur.execute(
                """
                UPDATE rh_findings
                   SET status = 'RESOLVED', resolved_at = now(), status_at = now(),
                       status_by = %s, status_reason = 'merged into the verified vendor',
                       updated_at = now()
                 WHERE tenant_id = %s AND finding_id = %s AND resolved_at IS NULL
                """,
                (actor, tenant, body.finding_id),
            )
            _write_event(cur, body.finding_id, tenant, "ACTION_EXECUTED", actor,
                         "merge-misread-vendor", {"moved": moved,
                                                  "from": body.source_supplier_id,
                                                  "to": body.target_supplier_id})

    bills = moved.get("fact_invoices", 0)
    return {
        "status": "merged",
        "moved": moved,
        "message": (
            f"{bills} bill{'s' if bills != 1 else ''} moved to {tgt['name']} ({tgt_gstn}). "
            "The duplicate record has been retired."
            if bills else f"The duplicate record for {tgt['name']} has been retired."
        ),
    }


# ── notify ────────────────────────────────────────────────────────────────────
#
# The one action kind whose effect is a message rather than a data write
# (types.py's action() docstring, kind='notify'). There is no single generic
# /act dispatcher in this router — 'safe' actions each name the endpoint that
# already owns their operation instead — so this is that endpoint for notify,
# the same way /act/merge-misread-vendor is for a merge that has no router of
# its own to call.


class NotifyActionRequest(BaseModel):
    finding_id: str
    # "vendor_contact" (contact_id) | "role" (role) | "user" (username).
    destination_type: str
    contact_id: str | None = None
    role: str | None = None
    username: str | None = None
    subject: str | None = None
    message: str


@router.post("/act/notify")
def act_notify(body: NotifyActionRequest, tenant: Tenant):
    """Execute a `kind='notify'` action.

    Always reached with `confirm` already shown by the drawer — this endpoint
    does not re-check that the dialog was seen, the same trust 'safe' and
    'approve' already extend to the client. What it does check server-side is
    who the destination really is (domain.review.notify_actions.resolve_destination),
    the same discipline act_merge_misread_vendor uses for the two vendor ids it
    is handed rather than trusting the client's pair outright.
    """
    require_capability("edit")
    actor = current_username() or "unknown"

    with _PoolConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            finding = _load_owned(cur, tenant, body.finding_id)
            result = review_notify_actions.execute_notify(
                cur, conn, tenant, actor, finding,
                body.model_dump(exclude={"finding_id"}),
            )
            if result.get("status") == "ok":
                _write_event(cur, body.finding_id, tenant, "ACTION_EXECUTED", actor, "notify",
                             {"destination": result.get("destination"),
                              "run_id": result.get("run_id")})

    return result


# ── Types catalogue ───────────────────────────────────────────────────────────

@router.get("/types")
def list_types(tenant: Tenant):
    """The emergent type catalogue with its measured precision.

    Admin-only because it is an operational view: which detectors earn their
    place, and which are being dismissed so often they should stop being shown.
    """
    require_capability("admin")
    with _PoolConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT t.type_id, t.type_slug, t.display_name, t.description, t.family,
                       t.origin, t.status, t.default_severity::text AS default_severity,
                       t.occurrences, t.confirmed_count, t.dismissed_count,
                       t.promoted_detector, t.created_at,
                       (SELECT COUNT(*) FROM rh_findings f
                         WHERE f.type_id = t.type_id AND f.resolved_at IS NULL) AS open_now
                  FROM rh_finding_types t
                 WHERE t.tenant_id = %s
                 ORDER BY t.occurrences DESC
                """,
                (tenant,),
            )
            rows = cur.fetchall()

    out = []
    for r in rows:
        actioned = (r["confirmed_count"] or 0) + (r["dismissed_count"] or 0)
        out.append({
            "type_id": str(r["type_id"]),
            "slug": r["type_slug"],
            "display_name": r["display_name"],
            "description": r["description"],
            "family": r["family"],
            "origin": r["origin"],
            "status": r["status"],
            "default_severity": r["default_severity"],
            "occurrences": r["occurrences"],
            "confirmed": r["confirmed_count"],
            "dismissed": r["dismissed_count"],
            "open_now": r["open_now"],
            # None until there is enough signal to mean anything. Reporting
            # "100% precision" off two data points is worse than reporting nothing.
            "precision": (round(r["confirmed_count"] / actioned, 3)
                          if actioned >= 5 else None),
            "promoted_detector": r["promoted_detector"],
        })
    return {"types": out}
