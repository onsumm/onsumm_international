"""Bills that are not moving through the approval chain.

Two problems live here, and getting the *shape* right matters more than either
of the queries:

  * **A step with no valid assignee** is a fault in the chain configuration, not
    in any one bill. The first run of this adapter against real data produced 271
    CRITICAL findings that all said the same sentence, because it raised one per
    stuck bill — which is precisely the alert fatigue this whole feature exists
    to avoid. It is now one finding per (department, step), carrying the count
    and the money queued behind it, and it deep-links to the approval chain
    settings where the fix actually is.

  * **A stall with a valid assignee** genuinely is per-bill: each one is a
    separate decision by a real person, and they are addressed to that person.

Audience comes from the chain config via routing.audience_for_bill_status, which
mirrors what bill_approvals.py enforces. If the hub routes on anything else it
will eventually tell someone to approve a bill the API then 403s them for, and
the user concludes the hub is wrong rather than that the config is.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .. import links
from ..routing import (
    audience_for_bill_status, load_chain, status_name_map, step_idx_to_status,
)
from ..types import (
    FindingDraft, action, condition_hash, table_evidence, timeline_evidence,
)
from .base import FindingAdapter

STALL_DAYS = 3
# Above this, a per-bill stall finding stops being information and starts being
# wallpaper. The oldest and largest are the ones worth a card.
MAX_PER_BILL_STALLS = 25


class ApprovalStallsAdapter(FindingAdapter):
    detector = "adapter:approval_stalls"
    family = "approval"
    source_service = "approvals"

    async def scan(self, conn, tenant_id: str, limit: int):
        rows = await conn.fetch(
            """
            SELECT fi.invoice_id, fi.invoice_number, fi.status::text AS status,
                   fi.total_amount, fi.due_date, fi.department, fi.supplier_id,
                   fi.source_updated_at, fi.currency_code,
                   s.name AS supplier_name,
                   EXTRACT(EPOCH FROM (now() - fi.source_updated_at)) / 86400.0 AS days_waiting
              FROM fact_invoices fi
              LEFT JOIN dim_suppliers s ON s.supplier_id = fi.supplier_id
                                       AND s.tenant_id   = fi.tenant_id
             WHERE fi.tenant_id    = $1::uuid
               AND fi.soft_status  = 'active'
               AND fi.status::text LIKE 'PENDING_%'
               AND fi.source_updated_at < now() - ($3 || ' days')::interval
             ORDER BY fi.total_amount DESC NULLS LAST
             LIMIT $2
            """,
            tenant_id, limit, str(STALL_DAYS),
        )
        if not rows:
            return

        # Exact names, matching how bill_approvals.py gates an approver. A
        # near-miss ("Praveen Pandey" against a user called "Praveen Raj") is
        # exactly the failure being reported, so it must not be fuzzy-matched away.
        active_names = {
            (r["name"] or "").strip()
            for r in await conn.fetch(
                "SELECT name FROM users WHERE company_id = $1::uuid AND name IS NOT NULL",
                tenant_id,
            )
        }

        # Group by the thing that is actually broken: a step of a chain.
        blocked: dict[tuple[str | None, str], list] = defaultdict(list)
        stalled: list = []
        chains: dict[str | None, list] = {}

        for r in rows:
            dept = r["department"]
            if dept not in chains:
                chains[dept] = await load_chain(conn, tenant_id, dept)
            assignees = status_name_map(chains[dept]).get(r["status"]) or []
            named = [a for a in assignees if a]
            if named and not (set(named) & active_names):
                blocked[(dept, r["status"])].append(r)
            else:
                stalled.append(r)

        # ── One finding per broken step ───────────────────────────────────────
        for (dept, status), bills in blocked.items():
            chain = chains[dept]
            assignees = [a for a in (status_name_map(chain).get(status) or []) if a]
            aud = await audience_for_bill_status(conn, tenant_id, status, dept)
            total = sum(Decimal(str(b["total_amount"] or 0)) for b in bills)
            oldest = max(float(b["days_waiting"] or 0) for b in bills)
            step_label = next(
                (s["label"] for i, s in enumerate(
                    [x for x in chain if not x.get("locked")])
                 if step_idx_to_status(i) == status),
                status.replace("_", " ").title(),
            )
            where = f"{dept} · {step_label}" if dept else step_label

            yield FindingDraft(
                tenant_id=tenant_id,
                finding_type="approval_step_has_no_valid_assignee",
                family=self.family,
                subject_type="tenant",
                subject_id=None,
                # Every approval finding is P2 (store.FAMILY_SEVERITY enforces it).
                severity="MEDIUM",
                title="An approval step is assigned to someone who is not a user",
                detail=(
                    f"{len(bills)} bill{'s' if len(bills) != 1 else ''} queued behind "
                    f"{', '.join(assignees)}, who is not an active user — waiting will not "
                    f"clear {'them' if len(bills) != 1 else 'it'}."
                ),
                deep_link=links.settings("approval-chain", focus=f"chain.{dept or 'default'}"),
                condition_hash=condition_hash(
                    "orphaned", dept, status, tuple(sorted(assignees)), len(bills)
                ),
                detector=self.detector,
                source_service=self.source_service,
                money_at_risk=total or None,
                money_at_risk_inr=total or None,
                evidence=table_evidence(
                    ["Bill", "Vendor", "Amount", "Waiting"],
                    [[b["invoice_number"] or "—", b["supplier_name"] or "—",
                      str(b["total_amount"] or "—"), f"{float(b['days_waiting'] or 0):.0f}d"]
                     for b in sorted(bills, key=lambda x: x["total_amount"] or 0,
                                     reverse=True)[:20]],
                    note=f"Assigned to: {', '.join(assignees)} — none of whom is an active user.",
                ),
                # A step is a configuration object, so the qualifier is what makes
                # two broken steps two findings rather than one overwriting the other.
                key_qualifier=f"{dept or 'default'}:{status}",
                audience_roles=("admin", "finance_controller"),
                required_capability="admin",
                actions=[
                    action("open_chain", "Open the approval chain", kind="navigate",
                           href=links.settings("approval-chain")),
                ],
            )

        # ── Per-bill stalls, capped ───────────────────────────────────────────
        stalled.sort(key=lambda r: (r["total_amount"] or 0), reverse=True)
        for r in stalled[:MAX_PER_BILL_STALLS]:
            iid = str(r["invoice_id"])
            status, dept = r["status"], r["department"]
            days = float(r["days_waiting"] or 0)
            chain = chains[dept]
            aud = await audience_for_bill_status(conn, tenant_id, status, dept)
            assignees = [a for a in (status_name_map(chain).get(status) or []) if a]

            unlocked = [s for s in chain if not s.get("locked")]
            here = next((i for i in range(len(unlocked))
                         if step_idx_to_status(i) == status), -1)
            steps, idx = [], 0
            for step in chain:
                if step.get("locked"):
                    steps.append({"label": step["label"], "state": "done", "actor": None})
                    continue
                state = "done" if idx < here else ("current" if idx == here else "pending")
                steps.append({
                    "label": step["label"], "state": state,
                    "actor": ", ".join(step.get("assignees") or []) or step["role"],
                })
                idx += 1

            yield FindingDraft(
                tenant_id=tenant_id,
                finding_type="approval_stalled",
                family=self.family,
                subject_type="bill",
                subject_id=iid,
                # P2 regardless of wait — every approval finding is (see
                # store.FAMILY_SEVERITY).
                severity="MEDIUM",
                title="Bill has been waiting on the same approval step",
                detail=(
                    f"Waiting {days:.0f} days at {status.replace('_', ' ').title()}."
                    + (f" Assigned to {', '.join(assignees)}." if assignees else "")
                ),
                deep_link=links.bill(iid, focus="chain"),
                condition_hash=condition_hash(status, int(days // STALL_DAYS)),
                detector=self.detector,
                source_service=self.source_service,
                money_at_risk=r["total_amount"],
                money_at_risk_inr=r["total_amount"] if r["currency_code"] == "INR" else None,
                money_currency=r["currency_code"] or "INR",
                due_at=r["due_date"],
                evidence=timeline_evidence(steps, note=f"Waiting {days:.0f} days at this step."),
                entities=tuple(e for e in [
                    ("bill", iid, "subject", r["invoice_number"]),
                    ("vendor", str(r["supplier_id"]), "counterparty", r["supplier_name"])
                    if r["supplier_id"] else None,
                ] if e),
                audience_roles=aud.roles,
                audience_users=aud.users,
                audience_departments=aud.departments,
                required_capability="approve",
                actions=[action("open_bill", "Open the bill", kind="navigate",
                                href=links.bill(iid, focus="chain"))],
            )
