# Nova Dashboard — User Guide

What the Nova dashboard (`frontend/src/components/NovaOwnershipPanel.tsx`, served at `/nova`)
shows, how to read every number on it, and what each status means. For how reconciliation is
computed under the hood, see `docs/NOVA_RECONCILIATION_LOGIC.md`; this doc is the dashboard-facing
companion to it.

## What's on the page

### Filters

- **Vendor** — restricts everything below to one Nova vendor (Signzy, Digitap, KYCKart, …).
  Populated only from vendors that actually have a reconciled bill on file — no dead options.
- **Service month** — restricts to one billing month, or "Latest per vendor" (the default) to see
  the most recent reconciled month for every vendor at once.
- **clear filters** — resets both.

Both filters are driven by real data already returned from the first, unfiltered fetch, so the
dropdowns never show a vendor/month combination with nothing behind it.

### KPI tiles

Everything in this row is **scoped to services actually on the bill(s) matching your current
filter** — not every Nova module the vendor happened to return that month. A bill with 2 real
invoice line items shows KPI counts against those 2 services, never against the 10+ modules Nova's
full monthly response includes for services this document never claimed. Two tiles are the
deliberate exception (see below).

| Tile | Meaning |
|---|---|
| **Certified** | Line items on the bill for a service that's in the certified rate card for this cost centre, and whose Nova mapping has been confirmed. |
| **Uncertified** | Line items on the bill for a service that simply isn't authorized for this brand/cost-centre at all. A real compliance flag — this vendor is charging for something nobody certified. |
| **Ambiguous** | A service certified in more than one scope at once (e.g. both Vimanotech's own catalog and the owning RE's) — never auto-resolved, always needs a human call. |
| **Unresolved module** | Couldn't match this line's description (or Nova's module slug) to *any* known canonical service, on either side. |
| **Pending lookup** | Vimanotech-only (Flow B). This line is outside Vimanotech's own scope, its owning cost centre is known, but the second, cost-centre-scoped Nova pull hasn't happened yet. Not wrong — just incomplete. |
| **Nova logic missing** | The rate card *names* this service for this brand, but nobody has confirmed which (if any) live Nova module actually represents it. Different from Uncertified: this isn't "not authorized," it's "we haven't finished mapping it yet." See the worked example below. |
| **Rate mismatches** | Nova's own per-operation rate (`costPerOperationRupees`) is outside tolerance of the certified rate card. |
| **Qty mismatches (2% tol.)** | The bill's quantity is outside tolerance of Nova's `billableOperations` for that service — 2% by default, 20% for Account Aggregator (its billing depends on statement volume, not a flat per-hit rate). This is the *only* remaining quantity/amount check — see "Why there's no separate Cost tile" below. |
| **Missing in invoice** | *(full-scope, not bill-scoped)* Nova billed usage for this service, but it's nowhere on this document. Could mean it's billed on a different invoice, or genuinely never billed — worth checking. |
| **Extra in invoice** | *(bill-scoped)* This service is on the bill, but Nova reports no usage for it at all that month. |
| **Nova self-inconsistent** | Nova's own arithmetic doesn't add up — `billableOperations × costPerOperationRupees` doesn't match its own reported `totalCostRupees`. A defect in Nova's data, not in the bill. |
| **Variance** | Net ₹ difference between what was billed and what Nova's ledger says, summed across every *matched* service on the bill. |

**Why "Missing in invoice" doesn't follow the bill-scoping rule above:** by definition, a line in
that bucket has no invoice line at all — scoping it to "services on the bill" would make it always
read zero, which defeats the entire point of the tile. It's the one place on this dashboard that
deliberately shows the full picture of what Nova billed regardless of what's on this document.

**Why there's no separate "Cost mismatch" tile.** Cost is quantity × rate, and both of those
already get their own independent check (the Qty tile, and the rate check feeding into "Rate
mismatches"). An earlier version of this dashboard checked amount too, which meant one real
discrepancy could show up as two or three separate "mismatches" for the same underlying cause.
Now: if quantity and rate are both correct, cost is necessarily correct too, so it isn't checked a
third time.

### The vendor/month table

One row per `(vendor, service month)` that has a reconciled bill — always the **latest**
reconciliation for that combination, never a stale or superseded one. Click a row to open its
detail view below. The status chip is worst-status-wins across every line on that bill:

- **RED** — at least one Ambiguous, Uncertified, Rate mismatch, Qty mismatch, or Nova
  self-inconsistent line.
- **YELLOW** — nothing outright wrong, but something incomplete: Unresolved module, Pending
  lookup, Nova logic missing, or a Missing-in-invoice/Extra-in-invoice line.
- **GREEN** — everything on the bill certified, matched, and within tolerance.

### The bill detail view

Opens below the table when you click a bill. Three parts:

**1. Summary strip** — `Nova total (services on this bill)`, `Invoice total (same services)`, and
`Variance`. Both totals are computed only from the services that are both certified *and* matched
to a Nova module — a true like-for-like comparison, not Nova's full-month total for every module
it billed that vendor for (which is usually many times larger, and mostly irrelevant to this
specific document).

**2. Every invoice line item, unconditionally.** Nothing on the bill is hidden here, regardless of
outcome — a line only stays out of this table if it has *no invoice line at all* (a pure
Nova-side "Missing in invoice" entry, already covered by its own KPI tile above). Columns:

| Column | What it shows |
|---|---|
| Line item | The description as it appears on the actual invoice. |
| Nova module | The raw module slug from Nova's API response, if one matched. |
| Canonical service | The resolved service name both sides are compared under. |
| Cost centre | Which brand/cost-centre this line was reconciled against, when applicable. |
| Certification | `CERTIFIED` / `UNCERTIFIED` / `AMBIGUOUS` / `UNRESOLVED_MODULE` / `PENDING_EXTENDED_LOOKUP` / `NOVA_LOGIC_MISSING`, with a plain-text reason underneath whenever it isn't a clean `CERTIFIED`. |
| Match | `MATCHED` / `MISSING_IN_INVOICE` / `EXTRA_IN_INVOICE`. |
| Rate | `RATE_OK` / `RATE_MISMATCH` (with the variance %) / `ZERO_VOLUME` / `NOT_AUTO_COMPARABLE`. |
| Qty (2% tol.) | `QTY_OK` / `QTY_MISMATCH` (with the variance %). When a sibling-brand recheck ran (see below), its result appears here too. |
| Bill value / Nova value | The two raw numbers being compared. |

## Worked example — the screenshot above

Bill `771c59b3-3e33-4cce-843e-7941cae2d4ed`, Signzy, July 2026, Brightloans (Avinash Capital
Markets). Two real invoice lines:

**"Pan Extensive Premium" → PAN Premium API — `CERTIFIED`, `MATCHED`, `RATE OK`, `QTY OK`.**
On Brightloans' own Nova usage alone, this line's quantity was actually 47.51% off (a real,
large-looking mismatch). The **sibling-brand recheck** — visible in the small note under the Qty
badge — caught that Signzy bills Brightloans *and* its sibling brand Kreditbuddha's combined PAN
usage on this one document, while Nova tracks them separately. Combining both brands' Nova usage
(8,04,014 operations) against the invoice's quantity (8,06,901) brings the variance down to 0.36%
— well within the 2% tolerance, so the line correctly reads `QTY_OK`. This recheck only ever
*confirms* a genuine match; if the combined figure hadn't cleared tolerance, the line would have
stayed flagged.

**"Phone KYC - Phone to Prefill V2" → Phone-to-Prefill — `NOVA LOGIC MISSING`, `EXTRA IN INVOICE`.**
Phone-to-Prefill is in Brightloans' rate card, but nobody has confirmed which live Nova module (if
any) represents it — so instead of guessing (an earlier, incorrect version of this mapping briefly
aliased it to Nova's unrelated `eKYC` module) or calling it a flat `UNCERTIFIED` (which would
wrongly imply the service isn't authorized at all), it's flagged as a data gap: the rate card names
it, the Nova-side mapping doesn't exist yet.

**Why the KPI tiles read "Certified: 1" and "Nova logic missing: 1"** rather than the much larger
numbers you'd get from Nova's full July response for Brightloans (11 modules total that month):
the tiles are scoped to the 2 services genuinely on this invoice, exactly as described above — 1
of them certified and confirmed, 1 of them a confirmed-but-unmapped rate-card entry. The other 9
Nova modules that month (eSign, eKYC, email-verification, facematch, image-liveliness,
bank-aggregator, aadhaar-ocr, pan-ocr, name-match) never appear on this specific bill at all —
they're what's driving the full-scope "Missing in invoice: 10" tile, not the certification tiles.

## Where the numbers come from

`GET /v1/dashboard/summary` (vendor/month table + KPI tiles) and `GET /v1/reconciliation?bill_id=`
(detail view) — both served by `services/nova_reconciliation`, which also runs an internal
orchestrator that discovers newly-uploaded mapped-vendor bills, calls the live Nova API, and
reconciles them automatically (no manual trigger needed for a normal bill upload). Full mechanics
in `docs/NOVA_RECONCILIATION_LOGIC.md`.
