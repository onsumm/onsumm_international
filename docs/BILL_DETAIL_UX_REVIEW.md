# Bill detail panel: details section, UX review

**Scope:** the scrolling body of the bill panel on the Bills page, from the amount at the top through Vendor Details, Buyer Details, Bill Summary, Tax Breakdown, Line Items, TDS, Coding and Supporting Documents.
**Code:** `frontend/src/pages/BillsPage.tsx` (~1336–1700), `components/BillSection.tsx`, `components/GstBreakdown.tsx`, `pages/bills.css` (`.bm__grid`, `.bm__kv`, `.bm__lbl`, `.bm__val`, `.bm__section`).
**Date:** 2026-10-25. Measurements come from the screenshot of bill `26-27/9` (FullStop Entertainment LLP) and from the CSS.

---

## The short version

The panel answers an approver's three questions: **how much, to whom, is it right?** But it spreads those answers over about 850px of uniform, label-above-value cells, so the person has to scroll and read everything to find the few facts that matter. Four problems cause most of this:

1. **Every fact costs a full row.** Each field is an uppercase label on one line with its value below it, about 56px per field, in a 3-column grid that is often half empty.
2. **The same numbers repeat.** ₹64,900 appears three times (hero, Total Amount, Outstanding) and ₹55,000 twice (Subtotal, Taxable Amt).
3. **Empty slots are shown on purpose.** An inter-state bill shows four dashed CGST/SGST cells plus a dashed Cess.
4. **Nothing has a different weight.** Section titles and field labels are both small, uppercase and letter-spaced, so the eye can't tell a heading from a label, and money looks like metadata.

The fix keeps **every section and every breakdown in a fixed place** (approver first, see below). It saves space only inside sections, with label-left rows and no empty slots, and adds what an approver needs but can't see today: a TDS section that's always present (saying so when there's none), net payable, and the bank account the money goes to.

---

## What it looks like today

```
REQUESTED AMOUNT (INCL. GST)
₹64,900.00                                   ← #1

VENDOR DETAILS ───────────────────────────────────────────
VENDOR NAME
FullStop Entertainment LLP                   (whole row, 2 cells empty)
VENDOR GST / PAN          VENDOR EMAIL
27AADFF0493P1Z0 ✓         accounts@fullstopent.in        (1 cell empty)

BUYER DETAILS ────────────────────────────────────────────
BUYER NAME                BRAND NAME          BUYER GSTIN
AVINASH CAPITAL MARKETS…  Acme group          07AACCA2775M1ZF ✓

BILL SUMMARY ─────────────────────────────────────────────
BILL NUMBER
26-27/9                                      (whole row, 2 cells empty)
BILL DATE                 DUE DATE            PAYMENT TERMS
13 Apr 2026               13 Apr 2026 (red)   Due on receipt
SUBTOTAL                  GST AMOUNT          TOTAL AMOUNT
₹55,000.00                ₹9,900.00           ₹64,900.00     ← #2
AMOUNT PAID               OUTSTANDING
₹0.00                     ₹64,900.00 (red)                   ← #3

TAX BREAKDOWN ───────────────────────────── [INTER-STATE]
TAXABLE AMT               CGST RATE %         CGST AMOUNT
₹55,000.00                —                   —
SGST RATE %               SGST AMOUNT         IGST RATE %
—                         —                   18
IGST AMOUNT               CESS AMOUNT         TOTAL TAX
₹9,900.00                 —                   ₹9,900.00
PLACE OF SUPPLY
Maharashtra (27)
```

That's 4 section headers and 21 cells, of which **7 are dashes or blank** and **5 repeat a number shown elsewhere**.

---

## Findings

Severity: **High** means it slows down or misleads an approver. **Medium** costs space or effort. **Low** is polish.

### A. Space and layout

| # | Sev | Finding | Where |
|---|---|---|---|
| A1 | High | **Label-above-value cells cost about 56px per fact.** An 11px uppercase label, a gap, then a 14px value. With 21+ cells the section is about 850px before Line Items even start, so on a laptop the approver never sees the amount, vendor and line items together. | `.bm__kv`, `.bm__lbl`, `.bm__val` in `bills.css` |
| A2 | High | **Whole rows spent on single values.** `Vendor Name` and `Bill Number` are marked `wide` (`gridColumn: 1 / -1`), so each uses a full row for one short value. | `Field … wide` (BillsPage ~1356, ~1370) |
| A3 | Medium | **A ragged 3-column grid.** Vendor has 3 fields across 2 rows, Amount Paid/Outstanding leave the third column empty, and Place of Supply sits alone. Empty grid cells read as missing data. | `.bm__grid { grid-template-columns: repeat(3, 1fr) }` |
| A4 | Medium | **Heavy section dividers.** Each section is an uppercase title, a full-width rule and top margin, about 50px each, ×4 before line items. Bold titles plus rules on every group give more structure than this small amount of data needs. | `.bm__section` |
| A5 | Medium | **Buyer Details takes a full section for facts the approver already knows.** The buyer is the approver's own company. It only matters when it's *wrong* (a bill addressed to another entity or GSTIN). | `BillSection "Buyer Details"` |
| A6 | Low | **The panel width is barely used for money.** Amounts are left-aligned in three different columns, so the eye can't run down a column of figures to check the sum. | Bill Summary cells |

### B. Repetition and noise

| # | Sev | Finding | Where |
|---|---|---|---|
| B1 | High | **Total shown three times while the bill is unpaid.** Hero "Requested Amount", "Total Amount" in Bill Summary, and "Outstanding" all read ₹64,900. Outstanding only *repeats* until a payment is recorded. After a part payment it's a different, and the most important, number. So the fix isn't to drop Outstanding, it's to show it when it says something new (see the payment-state table below). | hero ~1339, summary ~1459, ~1527 |
| B2 | High | **Tax Breakdown shows slots for taxes that don't apply.** An inter-state bill shows CGST rate, CGST amount, SGST rate, SGST amount and Cess as dashes: 5 of 10 cells. The code comment says this was deliberate ("where the field sits says as much as what is in it"). That logic holds on the *editable* review screen, where you might fill a slot. On a read-only approval screen a dash is just noise. | `GstBreakdown.tsx` (Cells for CGST/SGST/Cess) |
| B3 | Medium | **Taxable Amt repeats Subtotal**, and **Total Tax repeats GST Amount**, a few rows apart. | GstBreakdown vs Bill Summary |
| B4 | Medium | **"Amount Paid ₹0.00" plus "Outstanding ₹64,900.00" on an unpaid bill.** While nothing is paid, two rows say what one word, "Unpaid", would. Both rows earn their place once a payment exists. | ~1526–1527 |
| B5 | Low | **"Payment Terms: Due on receipt" next to a due date equal to the bill date.** One explains the other. Show one, with the other as a hint. | ~1455–1456 |
| B7 | High | **The vendor name and GSTIN appear twice.** The panel header (`.bm__vendor`, `.bm__gstn`) already shows both, and Vendor Details shows them again a screen lower. Keep them in the header (with the honest GSTIN tag and a copy button there) and drop them from the body. | `bm__hdr` vs `BillSection "Vendor Details"` |
| B6 | Low | **The "Requested Amount (incl. GST)" label is long.** "incl. GST" matters, but it can go in small grey text after the figure. | hero label |

### C. Hierarchy and readability

| # | Sev | Finding | Where |
|---|---|---|---|
| C1 | High | **Headings and labels look the same.** Section titles (`.bm__section`: 2xs, bold, uppercase, 0.07em) and field labels (`.bm__lbl`: 2xs, semibold, uppercase, 0.05em) differ only slightly in weight. With about 25 uppercase strings stacked, the page reads as a form, not a summary. | `bills.css` |
| C2 | Medium | **Money isn't styled as money.** Totals use an inline `fontSize: 15, color: '#111827'` instead of a token. Amounts aren't right-aligned or set in tabular figures in the summary, so ₹55,000.00 and ₹9,900.00 don't line up. | `Field "Total Amount"` inline style |
| C3 | Medium | **GSTIN set in a different typeface and size.** Monospace at 12px next to 14px values makes the row look broken, and `fontFamily: 'monospace'` bypasses the app's `--font-mono`. | `Field mono` → inline `{ fontFamily: 'monospace', fontSize: 12 }` |
| C4 | Low | **Long names in capitals** ("AVINASH CAPITAL MARKETS PVT LTD") are shown as stored. Names extracted in caps are harder to read and wrap sooner. Consider display-casing, keeping the stored value. | Buyer Name |

### D. Accuracy and trust (UX that could mislead)

| # | Sev | Finding | Where |
|---|---|---|---|
| D1 | **High** | **The green tick next to a GSTIN only means the checksum is valid.** Its label says "Valid GSTIN checksum", but a filled green tick reads as "verified with the GST portal". A GSTIN can pass the checksum and still be cancelled or belong to someone else. Approvers will trust it more than they should. | `Field checksum` → `bm__badge--inline` |
| D2 | High | **"Vendor GST / PAN" shows only the GSTIN.** The PAN is inside it (characters 3–12), but the label promises two things and shows one. | `Field label="Vendor GST / PAN"` |
| D3 | Medium | **Overdue is shown by colour alone.** The due date turns red with no "overdue" text, so it fails for colour-blind users and on a quick glance. It also doesn't say *how* overdue. | `Field "Due Date"` (`isOverdue ? '#ef4444'`) |
| D4 | Medium | **Outstanding is red even when nothing is wrong.** An unpaid bill inside its terms is normal, but red signals a problem. Red should mean overdue, not unpaid. | `Field "Outstanding"` |
| D5 | Medium | **Net payable after TDS is far from the total.** The TDS block sits below Line Items, so the figure that actually gets paid is the last thing seen. An approver checking "what will we pay?" has to scroll past everything. | `TdsBlockWithFlag` placement (~1660) |
| D6 | Low | **Paise are always shown.** ₹55,000.00 everywhere. That's acceptable for finance, but `.00` on every round figure adds visual noise. Show paise only when they're non-zero, or at least on the summary rows. | `fmtAmt` |

### E. Interaction

| # | Sev | Finding | Where |
|---|---|---|---|
| E1 | Medium | **No copy button on the GSTIN or bill number.** These are the two values approvers paste into the GST portal or an email. The vendor card has copy buttons; the bill panel doesn't. | `Field` |
| E2 | Medium | **The vendor name isn't a link.** You can't reach the vendor card (bank, contracts, history) from the bill. | `Field "Vendor Name"` |
| E3 | Low | **Hard-coded colours** (`#ef4444`, `#059669`, `#111827`, `#eef2ff`, inter-state chip colours) bypass the design tokens, so they won't adapt to theme changes or dark mode and drift from the rest of the app. | throughout |
| E4 | Low | **Edit mode and view mode lay out differently.** Edit uses `EField` in a different order (Discount appears, no Payment Terms), so things move when you click Edit. | ~1350–1460 |

---

## Guiding principle: approver first

An approver has to find each fact **in the same labelled place, every time**, and see every breakdown in full: GST split, TDS, net payable, payment. A layout that saves space by merging sections or folding a breakdown into one line makes approval slower and makes things like TDS easy to miss. So space is saved **within** sections only:

- compact label-left rows, two per line
- no empty slots
- no repeated values
- absent things stated explicitly ("No TDS applied") rather than hidden

An earlier version of this proposal merged everything into one "receipt". It was rejected for exactly this reason.

## Proposed layout

Prototype: [`bill-detail-proposed.html`](bill-detail-proposed.html), in the real panel frame (PDF pane, header, approval chain, decision bar) with the design-system tokens. Fixed sections, in the order an approver checks a bill:

```
BILL AMOUNT (INCL. GST)
₹64,900            ● Unpaid · ● Overdue 195 days
Net payable after TDS ₹63,800                      (when TDS applies)

[ Bill · Vendor · Buyer · Amounts · TDS · Payment · Line items · Documents ]  ← sticky jump links

BILL ─────────────────────────────────────────────────────────────
Bill no.    26-27/9 ⧉            Payment terms   Due on receipt
Bill date   13 Apr 2026          Due date        13 Apr 2026  195 days overdue

VENDOR ───────────────────────────────────────────────────────────
Name        FullStop Ent. LLP    Email           accounts@fullstopent.in
GSTIN       27AADFF0493P1Z0 [Format OK · not portal-verified]
PAN         AADFF0493P           Pays to         HDFC ···· 3456  Verified

BUYER ─────────────────────────────────────── Matches your GSTIN
Name  Avinash Capital Markets   Brand  Acme group   GSTIN  07AACCA2775M1ZF

AMOUNT BREAKDOWN ───────────── [INTER-STATE] · Place of supply Maharashtra (27)
Subtotal                                               ₹55,000
Discount                                                    ₹0
Taxable value                                          ₹55,000
IGST                                  18%               ₹9,900
CGST + SGST                           not applicable         —
Total GST                                               ₹9,900
Bill total                                             ₹64,900

TDS ───────────────────────────────────────────────── 194J · 2%
Section (Professional / technical services)               194J
TDS base (taxable value, excl. GST)                    ₹55,000
TDS deducted                          2%               −₹1,100
Net payable to vendor                                  ₹63,800
   — or, when none: "No TDS applied. Check whether 194C/194J applies"  [Apply TDS]

PAYMENT ──────────────────────────────────────────────────────────
Status      Unpaid               Due             13 Apr 2026  195 days overdue
Amount paid ₹0                   Outstanding     ₹64,900
(+ Reference / Paid to once a payment exists)

LINE ITEMS ─────────────────────────────── 2 lines · matches subtotal
SUPPORTING DOCUMENTS ─────────────────────────────────────────────
```

What changed against today, and why it's still faster to approve:

- **Every section kept, in a fixed order, with a clear title.** Sticky jump links reach any section in one click, and a missing TDS is flagged in the links ("TDS · none").
- **Label-left rows, two per line,** instead of a label line above a value line. That roughly halves the height *without* removing anything. No `wide` single-value rows.
- **The amount breakdown is a right-aligned table.** Each figure can be checked line by line against the invoice. The tax type that doesn't apply is one muted "not applicable" row, so the approver sees both were considered (instead of five dashed boxes).
- **TDS is its own section and always present.** It shows section, base, rate, amount and net payable. With no TDS it says so, with an Apply action. Net payable also appears under the bill amount at the top.
- **Payment is always present** with fixed positions: status, due, paid and outstanding. Reference and paid-to appear once a payment exists, and outstanding reflects TDS.
- **Vendor adds "Pays to"** (the masked bank account and its verification), the one vendor fact an approver most needs.
- **The GSTIN tag says what was checked:** "Format OK · not portal-verified" versus "Verified on GST portal".

## Recommendations, in priority order

| Priority | Change | Fixes | Effort |
|---|---|---|---|
| 1 | Make the GSTIN tick honest: grey "format OK" for a checksum pass, green "Verified" only when the GST portal confirms it (the vendor's `status = VERIFIED`) | D1 | S |
| 2 | Keep Amount breakdown as its own section, but as a right-aligned table: subtotal → discount → taxable → each GST component with rate (the non-applicable type as one muted row) → total GST → bill total | B2, B3, A6, C2 | M |
| 3 | Inside every section, switch to label-left rows two per line; drop the `wide` single-value rows; add sticky jump links to the sections | A1–A3 | M |
| 4 | Put status in the hero ("Unpaid" / "Part paid" / "Paid on …" / "Overdue N days", in words); show Paid and Outstanding rows whenever a payment exists, with the hero switching to the outstanding amount; red only for overdue | B1, B4, D3, D4 | S |
| 5 | Make TDS its own always-present section right after the amount breakdown (section, base, rate, amount, net payable; "No TDS applied" plus Apply when none), and show net payable under the hero | D5 | S |
| 6 | Keep Buyer as a compact one-row section with a "Matches your GSTIN" / "Not your GSTIN" marker | A5 | S |
| 7 | Lighter section headings: whitespace instead of rule + caps title, and labels in sentence case | A4, C1 | S |
| 8 | Copy buttons on the GSTIN and bill number; vendor name links to the vendor card | E1, E2 | S |
| 9 | Fix "GST / PAN": show the PAN under the GSTIN, or relabel it "GSTIN" | D2 | S |
| 10 | Tokens instead of hex colours; use `--font-mono`; show paise only when non-zero | E3, C3, D6 | S |
| 11 | Same field order in edit and view mode | E4 | M |

**Rough space gain:** label-left rows alone take Bill, Vendor and Buyer from about 420px to about 220px. Removing the dashed tax slots takes the tax section from about 250px to about 230px, while now covering the full computation down to net payable. Overall it's about 30% shorter than today, with *more* information (bank, TDS and payment are always shown) and every section in a fixed place.

## Things to keep

- **Mobile folding** (`BillSection` `closedOnPhone`) is right. Keep it with the new layout.
- **The Inter-state / Intra-state chip.** It states something that otherwise has to be inferred.
- **The reconciliation warning** in `GstBreakdown` ("This split is what the invoice stated…") is good, honest copy. Keep it under the receipt when `reconciles === false`.
- **Showing Discount and Service month only when they exist.** Extend the same rule to Paid, Cess and Taxable amount.

## Open questions for product

1. Should the GSTIN tick show portal verification at all on bills? That needs the vendor's verification status in the bill payload (it's on the vendor record today).
2. Is Buyer ever *wrong* in practice? If bills addressed to the wrong entity are common, the mismatch warning (recommendation 6) is worth more than the space it saves.
3. Should paise be hidden on round amounts, or does finance want `.00` everywhere for consistency with exports?
