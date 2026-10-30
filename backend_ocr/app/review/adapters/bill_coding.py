"""Everything miscoded on one bill, as one thing to fix.

A bill can be wrong in four small ways at once — filed under the wrong
category, tagged to the wrong cost centre, lines left unmapped to a ledger,
and a GST split that does not match the states involved. Raised separately
those are four findings against one document, four drawers, four decisions,
and the same bill opened four times. Raised together they are one review:
here is what we think is wrong, here is what it should be, take the ones you
agree with.

So this adapter is the exception to the rule the rest of the package follows.
Everywhere else a finding is one condition; here it is one *subject* carrying
several independently acceptable corrections, because that is the unit of work
a person actually does.

What separates the four:

  · Category, cost centre and ledger are our own coding. We can put them
    right, and the proposal is the vendor's own established habit.
  · The GST split is the vendor's. Recording IGST here does not make the
    credit claimable — only a reissued invoice does — so the row says that
    plainly even though the fix is offered.

The finding closes itself when nothing is left to accept: `scan` yields the
current truth, so a bill whose proposals have all been taken (or fixed by hand
elsewhere) stops being asserted and the worker auto-resolves it.
"""
from __future__ import annotations

from .. import links
from ..routing import data_audience
from ..types import (
    FindingDraft, action, condition_hash, document_ref, proposals_evidence,
)
from .base import FindingAdapter, tenant_jurisdiction

# Only bills recent enough to still be worth recoding. An invoice from last
# year is filed, reported on and closed; reopening its category helps nobody.
_WINDOW_DAYS = 120

# How far back to look when deciding what a vendor's habit is. A little over a
# year, so a yearly cycle still reads as one pattern.
_HISTORY_DAYS = 400

# Below this there is no habit, only a coincidence.
_MIN_SAME = 3

# And the habit has to be the clear majority. At 0.7, three-and-one is a habit
# with one slip (0.75) and four-and-two is a vendor who genuinely uses both
# (0.67) and is left alone.
_DOMINANCE = 0.7


def _state(gstin: str | None) -> str | None:
    """The state code a GSTIN starts with. 27 is Maharashtra, 07 Delhi."""
    s = (gstin or "").strip()
    return s[:2] if len(s) >= 2 and s[:2].isdigit() else None


# One row per recent bill, with everything four proposals need. The vendor's
# habit is computed in the same pass rather than per bill — see the note in
# base.py about a scan staying cheap.
_SQL = f"""
    WITH scope AS (
        SELECT fi.invoice_id, fi.invoice_number, fi.supplier_id, fi.tenant_id,
               fi.category, fi.department, fi.cost_center_id, fi.upload_id,
               fi.invoice_date, fi.due_date,
               COALESCE(fi.stg_bill_id, fi.archived_stg_bill_id) AS stg_bill_id,
               fi.created_at
          FROM fact_invoices fi
         WHERE fi.tenant_id      = $1::uuid
           AND fi.invoice_type   = 'AP'
           AND fi.supplier_id IS NOT NULL
           AND COALESCE(fi.soft_status, 'active') = 'active'
           AND fi.created_at > now() - ($3 || ' days')::interval
    ),
    -- The vendor's habit, over a longer window than the one being judged.
    cat AS (
        SELECT supplier_id, shown, same_n, total FROM (
            SELECT fi.supplier_id, lower(btrim(fi.category)) AS norm,
                   max(btrim(fi.category)) AS shown, count(*) AS same_n,
                   sum(count(*)) OVER (PARTITION BY fi.supplier_id) AS total,
                   row_number() OVER (PARTITION BY fi.supplier_id
                                          ORDER BY count(*) DESC) AS rn
              FROM fact_invoices fi
             WHERE fi.tenant_id    = $1::uuid
               AND fi.invoice_type = 'AP'
               AND COALESCE(fi.soft_status, 'active') = 'active'
               AND fi.created_at > now() - ($4 || ' days')::interval
               AND COALESCE(btrim(fi.category), '') <> ''
             GROUP BY fi.supplier_id, lower(btrim(fi.category))
        ) t WHERE rn = 1
    ),
    dept AS (
        SELECT supplier_id, shown, same_n, total FROM (
            SELECT fi.supplier_id, lower(btrim(fi.department)) AS norm,
                   max(btrim(fi.department)) AS shown, count(*) AS same_n,
                   sum(count(*)) OVER (PARTITION BY fi.supplier_id) AS total,
                   row_number() OVER (PARTITION BY fi.supplier_id
                                          ORDER BY count(*) DESC) AS rn
              FROM fact_invoices fi
             WHERE fi.tenant_id    = $1::uuid
               AND fi.invoice_type = 'AP'
               AND COALESCE(fi.soft_status, 'active') = 'active'
               AND fi.created_at > now() - ($4 || ' days')::interval
               AND COALESCE(btrim(fi.department), '') <> ''
             GROUP BY fi.supplier_id, lower(btrim(fi.department))
        ) t WHERE rn = 1
    ),
    cc AS (
        SELECT supplier_id, cost_center_id, same_n, total FROM (
            SELECT fi.supplier_id, fi.cost_center_id, count(*) AS same_n,
                   sum(count(*)) OVER (PARTITION BY fi.supplier_id) AS total,
                   row_number() OVER (PARTITION BY fi.supplier_id
                                          ORDER BY count(*) DESC) AS rn
              FROM fact_invoices fi
             WHERE fi.tenant_id    = $1::uuid
               AND fi.invoice_type = 'AP'
               AND COALESCE(fi.soft_status, 'active') = 'active'
               AND fi.created_at > now() - ($4 || ' days')::interval
               AND fi.cost_center_id IS NOT NULL
             GROUP BY fi.supplier_id, fi.cost_center_id
        ) t WHERE rn = 1
    )
    SELECT s.invoice_id, s.invoice_number, s.supplier_id, s.stg_bill_id, s.upload_id,
           s.category, s.department, s.cost_center_id,
           s.invoice_date, s.due_date, sup.payment_terms_days,
           sup.name AS supplier_name, sup.gstn AS vendor_gstn,
           dt.gstn  AS buyer_gstn,
           sb.cgst_amount, sb.sgst_amount, sb.igst_amount, sb.cess_amount,
           sb.cgst_rate, sb.sgst_rate, sb.igst_rate,
           sb.tax_amount, sb.place_of_supply,
           cat.shown  AS usual_category,
           cat.same_n AS cat_same_n, cat.total AS cat_total,
           dept.shown  AS usual_department,
           dept.same_n AS dept_same_n, dept.total AS dept_total,
           cc.cost_center_id AS usual_cc,
           cc.same_n AS cc_same_n, cc.total AS cc_total,
           ccu.name AS usual_cc_name, ccb.name AS bill_cc_name
      FROM scope s
      JOIN dim_suppliers sup ON sup.supplier_id = s.supplier_id
                            AND sup.tenant_id   = s.tenant_id
      JOIN dim_tenants  dt  ON dt.tenant_id     = s.tenant_id
      LEFT JOIN stg_bills sb ON sb.stg_bill_id  = s.stg_bill_id
      LEFT JOIN cat  ON cat.supplier_id  = s.supplier_id
      LEFT JOIN dept ON dept.supplier_id = s.supplier_id
      LEFT JOIN cc  ON cc.supplier_id  = s.supplier_id
      LEFT JOIN cost_centers ccu ON ccu.id = cc.cost_center_id
      LEFT JOIN cost_centers ccb ON ccb.id = s.cost_center_id
     ORDER BY s.created_at DESC
     LIMIT $2
"""

# Lines with no ledger, plus what this vendor's history says they should be.
# Tier 1 of routers/ledgers.py::ledger_suggest, as a set operation.
_LEDGER_SQL = """
    SELECT li.stg_bill_id, li.line_number, li.product_name,
           sug.account_id, sug.account_name, sug.uses
      FROM stg_bill_line_items li
      JOIN LATERAL (
            SELECT a.account_id, a.account_name, count(*) AS uses
              FROM ledger_mapping_log l
              JOIN dim_ledger_accounts a ON a.account_id = l.account_id
             WHERE l.tenant_id   = $1::uuid
               AND l.supplier_id = $3::uuid
               AND a.is_active
               AND l.description ILIKE '%' || left(li.product_name, 40) || '%'
             GROUP BY a.account_id, a.account_name
             ORDER BY count(*) DESC, max(l.created_at) DESC
             LIMIT 1
      ) sug ON TRUE
     WHERE li.tenant_id    = $1::uuid
       AND li.stg_bill_id  = $2::uuid
       AND li.account_id IS NULL
       AND COALESCE(btrim(li.product_name), '') <> ''
     ORDER BY li.line_number
"""


class BillCodingAdapter(FindingAdapter):
    detector = "adapter:bill_coding"
    family = "data_quality"

    async def scan(self, conn, tenant_id: str, limit: int):
        aud = data_audience()
        # The CGST/SGST vs IGST check is Indian GST (core/jurisdiction.py); the
        # category / department / cost-centre habits below apply everywhere.
        gst_on = (await tenant_jurisdiction(conn, tenant_id)).has("gst")
        rows = await conn.fetch(
            _SQL, tenant_id, limit, str(_WINDOW_DAYS), str(_HISTORY_DAYS),
        )

        for r in rows:
            proposals: list[dict] = []

            # ── 1. The GST split against the states involved ────────────────
            gst = _gst_proposal(r) if gst_on else None
            if gst:
                proposals.append(gst)

            # ── 2. Category, against this vendor's habit ────────────────────
            if _is_habit(r["cat_same_n"], r["cat_total"]):
                usual = r["usual_category"]
                current = (r["category"] or "").strip()
                if usual and current.lower() != usual.lower():
                    proposals.append({
                        "id": "category",
                        "group": "Coding",
                        "label": "Category",
                        "current": current or None,
                        "proposed": usual,
                        # Say what the habit *is*, not just how often: "4 of
                        # this vendor's last 5 bills" alone leaves the user to
                        # guess what those bills had.
                        "why": f"{r['cat_same_n']} of this vendor's last {r['cat_total']} bills "
                               f"had category “{usual}”",
                        "writable": True,
                    })

            # ── 3. Department, the same way ────────────────────────────────
            if _is_habit(r["dept_same_n"], r["dept_total"]):
                usual = r["usual_department"]
                current = (r["department"] or "").strip()
                if usual and current.lower() != usual.lower():
                    proposals.append({
                        "id": "department",
                        "group": "Coding",
                        "label": "Department",
                        "current": current or None,
                        "proposed": usual,
                        "why": f"{r['dept_same_n']} of this vendor's last {r['dept_total']} bills "
                               f"went to the {usual} department",
                        "writable": True,
                    })

            # ── 4. Cost centre, the same way ───────────────────────────────
            if _is_habit(r["cc_same_n"], r["cc_total"]) and r["usual_cc"]:
                if str(r["cost_center_id"] or "") != str(r["usual_cc"]):
                    proposals.append({
                        "id": "cost_centre",
                        "group": "Coding",
                        "label": "Cost centre",
                        "current": r["bill_cc_name"],
                        "proposed": r["usual_cc_name"],
                        "why": f"{r['cc_same_n']} of this vendor's last {r['cc_total']} bills "
                               f"were tagged to cost centre “{r['usual_cc_name']}”",
                        "writable": True,
                    })

            # ── 5. Payment terms, against what was agreed with the vendor ──
            terms = _terms_proposal(r)
            if terms:
                proposals.append(terms)

            # ── 6. Unmapped lines, against the ledger history ──────────────
            if r["stg_bill_id"]:
                lines = await conn.fetch(
                    _LEDGER_SQL, tenant_id, r["stg_bill_id"], r["supplier_id"],
                )
                for li in lines:
                    proposals.append({
                        "id": f"ledger:{li['line_number']}",
                        "group": "Ledger mapping",
                        "label": f"Line {li['line_number']} — {li['product_name']}",
                        "current": None,
                        "proposed": li["account_name"],
                        "why": f"mapped this way {li['uses']}× for this vendor",
                        "writable": True,
                        "account_id": str(li["account_id"]),
                    })

            if not proposals:
                continue

            invoice_id = str(r["invoice_id"])
            n = len(proposals)
            yield FindingDraft(
                tenant_id=tenant_id,
                finding_type="bill_coding_corrections",
                family=self.family,
                subject_type="bill",
                subject_id=invoice_id,
                # A wrong GST split is an unclaimable credit, not a filing
                # nuisance, so it lifts the whole finding.
                severity="HIGH" if gst else "MEDIUM",
                title=(
                    f"{n} thing{'s' if n != 1 else ''} to correct on this bill"
                ),
                detail=(
                    "Each one can be accepted on its own — take what you agree "
                    "with and leave the rest."
                ),
                deep_link=links.bill(invoice_id),
                # Re-raises as a changed condition when the set of proposals
                # changes, and stays quiet while it is the same set.
                condition_hash=condition_hash(
                    *[f"{p['id']}={p['proposed']}" for p in proposals]
                ),
                detector=self.detector,
                source_service=self.source_service,
                evidence=proposals_evidence(
                    proposals,
                    note=f"{n} proposed correction{'s' if n != 1 else ''}",
                    document=document_ref(
                        r["upload_id"], label=r["invoice_number"] or "the bill",
                    ),
                ),
                entities=(
                    ("bill", invoice_id, "subject", r["invoice_number"]),
                    ("vendor", str(r["supplier_id"]), "related", r["supplier_name"]),
                ),
                audience_roles=aud.roles,
                required_capability="edit",
                # No apply action is declared here, and that is deliberate.
                #
                # The drawer renders `actions` as buttons, and the proposals
                # panel already carries its own Apply — so declaring one put
                # two Apply buttons on the same screen. The declared one could
                # not have worked either: it would post with no selection,
                # because which corrections to take is a decision the panel
                # holds and an action body cannot.
                actions=[
                    action("open_bill", "Open the bill", kind="navigate",
                           href=links.bill(invoice_id)),
                ],
            )


def _is_habit(same_n, total) -> bool:
    """A floor, and a clear majority. Below either there is no habit to break."""
    if not same_n or not total:
        return False
    return int(same_n) >= _MIN_SAME and int(same_n) / int(total) >= _DOMINANCE



def _terms_proposal(r) -> dict | None:
    """The bill's own terms against the vendor's agreed ones.

    A bill has no payment-terms column: its real term is the gap between its
    dates, which is how the Bills screen computes the "Net 30 days" it shows
    (see routers/bills.py — the vendor default is only the fallback when a date
    is missing). So the comparison is a derived number against a stored one.

    Unlike category or department this is not read off a habit. The vendor
    master is an agreement someone made, which makes a disagreement worth more
    than a majority vote: an invoice billing Net 30 against agreed Net 45 is
    the vendor asking to be paid fifteen days early.
    """
    agreed = r["payment_terms_days"]
    if agreed is None:
        return None                      # no agreement on file to disagree with
    if not r["invoice_date"] or not r["due_date"]:
        return None                      # the bill's own term is not knowable

    on_bill = max((r["due_date"] - r["invoice_date"]).days, 0)
    agreed = int(agreed)
    if on_bill == agreed:
        return None

    def _say(n: int) -> str:
        return "Due on receipt" if n == 0 else f"Net {n} days"

    return {
        "id": "payment_terms",
        "group": "Terms",
        "label": "Payment terms",
        "current": _say(on_bill),
        "proposed": _say(agreed),
        "why": f"this vendor's agreed terms are {_say(agreed).lower()}",
        "writable": True,
        # Worth spelling out: this is the one proposal that changes WHEN money
        # leaves. The others change how a bill is filed.
        "caveat": (
            f"The invoice asks for {_say(on_bill).lower()}. Accepting this moves "
            f"the due date to {agreed} days after the bill date, which changes "
            f"when it falls due and when it counts as overdue."
        ),
        "days": agreed,
    }


def _gst_proposal(r) -> dict | None:
    """CGST+SGST within one state, IGST across two. Anything else is wrong.

    The state pair is read from the two GSTINs rather than from the addresses,
    because the GSTIN *is* the registration the tax attaches to. Place of
    supply overrides the buyer's own state when the invoice prints one: goods
    delivered to another state are taxed where they land, which is exactly the
    case a buyer-address rule gets wrong.
    """
    vendor_state = _state(r["vendor_gstn"])
    buyer_state = _state(r["buyer_gstn"])
    if not vendor_state or not buyer_state:
        return None

    # "Haryana (06)" / "07 Delhi" — the two printed shapes.
    pos = (r["place_of_supply"] or "").strip()
    pos_digits = "".join(c for c in pos if c.isdigit())[:2]
    supply_state = pos_digits if len(pos_digits) == 2 else buyer_state

    cgst = float(r["cgst_amount"] or 0)
    sgst = float(r["sgst_amount"] or 0)
    igst = float(r["igst_amount"] or 0)
    if cgst + sgst + igst <= 0:
        return None          # no GST charged, nothing to be wrong about

    interstate = vendor_state != supply_state
    charged_split = cgst > 0 or sgst > 0
    if interstate == (igst > 0 and not charged_split):
        return None          # what was charged matches the states

    where = f"{vendor_state} → {supply_state}"
    if interstate:
        rate = float(r["cgst_rate"] or 0) + float(r["sgst_rate"] or 0)
        total = cgst + sgst
        return {
            "id": "gst_split",
            "group": "GST",
            "label": "Tax charged",
            "current": f"CGST + SGST  ₹{total:,.2f}",
            "proposed": (f"IGST @ {rate:g}%  ₹{total:,.2f}" if rate
                         else f"IGST  ₹{total:,.2f}"),
            "why": f"inter-state supply ({where}) — IGST is due, not CGST+SGST",
            "writable": True,
            # Said plainly, because accepting this corrects our record and not
            # the problem: the credit stays stuck until the vendor reissues.
            "caveat": (
                "The invoice itself charges CGST+SGST. Recording IGST here "
                "fixes our books; the input credit needs a revised invoice "
                "from the vendor."
            ),
            "rate": rate or None,
            "amount": total,
        }

    half = igst / 2
    rate = float(r["igst_rate"] or 0) / 2
    return {
        "id": "gst_split",
        "group": "GST",
        "label": "Tax charged",
        "current": f"IGST  ₹{igst:,.2f}",
        "proposed": (f"CGST + SGST @ {rate:g}% each  ₹{igst:,.2f}" if rate
                     else f"CGST + SGST  ₹{igst:,.2f}"),
        "why": f"supply within one state ({where}) — CGST+SGST is due, not IGST",
        "writable": True,
        "caveat": (
            "The invoice itself charges IGST. Recording CGST+SGST here fixes "
            "our books; the input credit needs a revised invoice from the vendor."
        ),
        "rate": rate or None,
        "amount": igst,
        "half": half,
    }
