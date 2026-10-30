# TDS — Engine Fixes, Catalogue, and Data Repair

> Commits: `e756278`, `d82b6e9`, `e8856c8`, `9630423` — October 1, 2026
> Tenant worked: `d0e00000-0000-0000-0000-000000000003` (BrightLoans / ACM)
> Also in this session: `98c4fe1` (HDFC IMPS status codes), `29bd5c8` (disbursement demo flag)

---

## Why this session happened

The TDS subsystem computed the wrong amount in several ways at once, and every one
of them was silent — a bill reported `CALCULATED`, carried a rate, and withheld
the wrong figure with nothing on screen to say so. The test suite passed
throughout, because it did not exercise any of the affected paths.

Net effect on the worked tenant: total TDS moved from **₹13,24,442 to ₹11,08,262**.
That is not a discount — it is ₹1,78,288 of over-deduction removed (a stale 20%
penalty), ₹7,421 of under-deduction recovered, and threshold decisions corrected
against repaired cumulative totals.

---

## 1. Bugs fixed

All verified against the real engine. "Silent" means the bill looked correctly
calculated; nothing surfaced the error.

| # | Area | Bug | Effect | Fix |
|---|---|---|---|---|
| 1 | `utils/tds_engine.py` | Aggregate-threshold **arrears never booked**. Once a FY threshold is crossed, TDS is due on the whole year's aggregate; the engine taxed only the bill that crossed it | Silent under-deduction on every vendor that crossed a threshold mid-year | Arrears computed into `catchup_pending` on the crossing bill, via `_untaxed_prior_base()` |
| 2 | `utils/tds_engine.py` | A bill whose `taxable_amount` was never extracted (OCR missed the pre-GST line) computed TDS on a **zero base** | ₹0 withheld on live bills — ADZASIA ₹76,038 and ₹43,478 among them. Indistinguishable from a legitimate below-threshold result | Missing taxable derived as `total − GST` inside the engine, so no caller can omit it |
| 3 | `utils/tds_engine.py` | Unrecognised **`tds_mode`** fell through every branch with `tds_base` at 0 while `tds_applicable` stayed `True` | Misconfigured override ⇒ `CALCULATED`, rate shown, nothing withheld | Returns `NA` naming the bad value |
| 4 | `utils/tds_engine.py` | Unrecognised **`threshold_type`** — same failure as #3 | Misconfigured section ⇒ silent zero TDS | Returns `NA` naming the bad value |
| 5 | `utils/tds_engine.py` | **DUAL with `threshold_amount_2` unset** — the unset threshold arrives as `0`, and `cum_billed >= 0` always holds | Deducted on *every* bill regardless of either threshold | Each leg now requires its own threshold to be configured |
| 6 | `utils/tds_engine.py` | `_fy_bounds` named month `fy_start_month − 1`, i.e. **month 0 for a January FY** | `ValueError` — a calendar-year tenant could not compute TDS at all | End derived as the day before the next FY starts |
| 7 | `pipelines/tds_processing.py` | `FOR UPDATE SKIP LOCKED` was taken on a connection **released before the work ran** | Two workers could process one bill, adding its amount to `cumulative_billed` twice — the drift `fix_tds_cumulative_state.py` exists to repair | Row re-claimed inside the writing transaction |
| 8 | `utils/tds_catchup.py` | `replay_fy_state` wrote `tds_rate` but **not `tds_rate_percent`** — and the bills API serves `tds_rate_percent` as the rate the UI renders | Every repaired bill would show its pre-repair rate beside a new TDS amount | Writes both, plus `tds_section_code` |
| 9 | `utils/tds_catchup.py` | `replay_fy_state` reset `threshold_crossed` but **not `catchup_pending`**, which the engine adds to | Each replay stacked another copy of the same arrears — the same accumulate-instead-of-replace bug the script repairs | Resets it; `catchup_recovered` left intact (money already withheld) |
| 10 | `scripts/fix_tds_cumulative_state.py` | **No tenant filter** — rewrote FY state for every tenant in the database | Blast radius far beyond the tenant being repaired | `--tenant=<uuid>` |
| 11 | `backend_ocr/app/domain/vendor_merge.py` | `repoint_and_retire`'s fallback `DELETE` ran inside the `except` with no savepoint; a child FK (`vendor_tds_config_log` → `dim_vendor_tds_overrides`) made it raise | **Aborted the entire merge.** Latent for `routers/vendors.py` and the Review Hub too | Own savepoint; leaves the row if it cannot move (its supplier is being deactivated) |
| 12 | `frontend/.../SettingsPage.tsx` | Payment-code placeholder read `"Code e.g. 194J"` — telling users to type the old section number into the 4-digit `return_code` field | A plausible source of the junk codes already in the data (`9999`, `u/s 194J`) | Reads `"Payment code e.g. 1027"`, with tooltips |
| 13 | `frontend/.../SettingsPage.tsx` | `new_section_label` accepted by the API on create *and* update, but no input existed and save hardcoded `null` | Income Tax Act 2025 numbering could never be set; NULL on all rows | Input + table column added |
| 14 | `frontend/.../SettingsPage.tsx` | `base_calculation` likewise unreachable; the optimistic row after create claimed `TAXABLE` regardless of what the server stored | Could not choose whether TDS applies pre- or post-GST | Labelled picker; `TAXABLE` kept as a legacy synonym |
| 15 | `utils/tds_engine.py` | `has_split_rate` / `rate_individual_huf` / `rate_others` are read **nowhere** in the codebase | Setting them has no effect, silently | Warns, naming the working alternative |
| 16 | Data (26 vendor groups) | **Duplicate vendor records** — the PAN-less copy drew the 20% s.206AA penalty while the verified copy held the PAN; uncategorised copies deducted nothing | Error in both directions simultaneously | 42 groups merged, 52 records retired |
| 17 | Data (72 FY-state rows) | `cumulative_billed` **inflated 2× and 4×** by historic reprocessing | Thresholds evaluated against inflated totals — bills deducted that were not due, thresholds read as crossed early | Replayed from bills; 72 → 0 |

### Non-TDS, same session

| # | Area | Change |
|---|---|---|
| 18 | `backend_ocr/app/integrations/hdfc_status_codes.{py,json}` | HDFC IMPS status/error mapping — 95 codes, 142 reasons. `TXSETT` is ambiguous (`Settled` interim vs `Completed` terminal) so it refuses to resolve from the code alone rather than marking a payment paid early; `TXCOMP` is named "Payment Completed" but HDFC classifies it interim |
| 19 | `backend_ocr/app/routers/disbursement.py` | `DISBURSEMENT_FORCE_SUCCESS` — temporary, off by default, for demoing Pay Now while the Lendix API is failing. Tagged in the gateway-call audit log. **Remove when Lendix recovers** |

---

## 2. TDS sections configured

17 active sections for the worked tenant, each with a vendor category and mapping
rule. Rates and thresholds are the supplied FY 2026-27 chart.

| Code | Section | Nature of payment | Rate | Threshold | No-PAN | Status |
|---|---|---|---|---|---|---|
| 1027 | 194JB | Professional services | 10% | ₹50,000 / yr | 20% | existing |
| 1026 | 194JA | Technical services | 2% | ₹50,000 / yr | 20% | existing |
| 1028 | 194JB | Director fees | 10% | none | 20% | **corrected** — carried a ₹50,000 threshold the chart says does not exist |
| 1023 | 194C | Contractor payments | 1% / 2% | ₹30,000 per bill **or** ₹1,00,000 / yr | 20% | existing — split via two categories |
| 1006 | 194H | Commission or brokerage | 2% | ₹20,000 / yr | 20% | existing |
| 1005 | 194D | Insurance commission | 2% / 10% | ₹20,000 / yr | 20% | **added** — split via two categories |
| 1008 | 194I(a) | Rent — plant, machinery, equipment | 2% | ₹50,000 / month | 20% | **added** |
| 1009 | 194I(b) | Rent — building, land, furniture | 10% | ₹50,000 / month | 20% | existing |
| 1031 | 194Q | Purchase of goods | 0.1% | on amount above ₹50,00,000 / yr | **5%** | **corrected** — no-PAN was 20%; s.206AA caps 194-Q at 5% |
| 1010 | 194IA | Purchase of immovable property | 1% | ₹50,00,000 per transaction | 20% | **added** |
| `194IC` | 194IC | Joint development agreement | 10% | none | 20% | **added** — ⚠ payment code unconfirmed, see note |
| 1037 | 194S | Virtual digital assets | 1% | ₹50,000 / yr | 20% | **added** |
| 1035 | 194O | E-commerce seller — Individual/HUF | 0.1% | ₹5,00,000 / yr | 5% | **added** |
| 1035 | 194O | E-commerce seller — company / other | 0.1% | none | 5% | **added** — separate section, see note |
| 1022 | 194A | Interest — general (non-bank) | 10% | ₹10,000 / yr | 20% | existing |
| 1033 | 194R | Perquisite / benefit to a business associate | 10% | ₹20,000 / yr | 20% | **added** |
| 1067 | 194T | Partner remuneration, commission, interest | 10% | ₹20,000 / yr | 20% | **added** |

**Retired:** 14 duplicate `u/s …` sections, plus `194A` Non-Senior Citizen (₹50,000)
and `194A` Senior Citizen (₹1,00,000). Those two are the s.194A(3)(i) **bank
deposit** thresholds, which apply when the deductor is a bank, co-op or post
office. An NBFC paying interest falls under the ₹10,000 figure already covered by
Interest — General. Deactivated, not deleted.

Three modelling notes, each forced by the schema:

- **Split rates are two categories, not one section.** `has_split_rate` and friends
  are dead columns (bug #15), so 194C's 1%/2% and 194D's 2%/10% are two vendor
  categories mapped to one section with a `rate_override` each.
- **194-O needs two sections.** Its threshold differs by payee type, and a
  threshold is a property of a section, not a category.
- **194-IC's 4-digit code is unconfirmed** and the column is `NOT NULL`, so it
  carries the literal `194IC` — deliberately not four digits, so it cannot be
  mistaken for a filing code. Calculation is unaffected; the engine reads
  `old_section_label`. **Replace before filing.**

---

## 3. Data repair, by the numbers

| Measure | Before | After |
|---|---|---|
| Total TDS | ₹13,24,442 | **₹11,08,262** |
| Bills at the 20% no-PAN penalty | 3 (₹1,78,288) | **0** |
| FY-state rows with inflated `cumulative_billed` | 72 of 119 | **0 of 90** |
| `cumulative_tds` vs sum of bills | ₹21,50,470 vs ₹13,24,442 | **₹12,60,462 vs ₹12,59,902** |
| Duplicate PAN groups | 26 | **0** |
| Active supplier records | 229 | 179 (52 retired, kept for audit) |
| Bills | 351 | 351 — none lost |
| `tds_rate` vs `tds_rate_percent` divergence | — | **0** |
| Arrears surfaced (`catchup_pending`) | ₹0 on 46 crossed thresholds | ₹989 on 29 |
| Active sections / categories / rules | 10 / 21 / 21 | 17 / 19 / 19 |

### Deduplication classification

Grouped by how certain the match is, because a wrong merge is hard to unpick:

| Bucket | Rule | Groups | Action |
|---|---|---|---|
| PAN | Same non-null PAN — the PAN is the legal identity | 23 | merged |
| NAME | Same name, at most one distinct PAN | 6 | merged |
| MISREAD-GSTIN | Same name and state code, PANs an anagram or ≤2 edits apart | 6 | merged |
| PAN-RENAME | Same PAN, different trade name (proprietorships) | 7 | merged — **payee name changes** |
| CONFLICT | Two or more genuinely distinct PANs | 1 | **refused** |

The misread bucket was the discovery. Deriving the PAN from GSTIN characters 3–12
exposed OCR transpositions masquerading as different entities — `AAKCP4645B` vs
`AAKCP6454B` (PRUDIGITAL), `ATOPK8355M` vs `AOTPK8355M` (SAFKON), `AAKCK5710G`
vs `AACKC5710G` (KASHTI). `vendor_merge.py`'s own docstring describes this case;
the test it specifies is what is implemented. Several GSTINs under one PAN is
ordinary — a company registers per state — and is *not* treated as a conflict.

Refused: **PERFORMANCE LAUNCHPAD**, `AACCA2775M` vs `AAKCP7694Q` — unrelated PANs,
two real entities.

---

## 4. Testing report

| Suite | Scope | Result |
|---|---|---|
| `scripts/tds_test_suite.py` | Real engine + real API, throwaway data, self-cleaning | **34 / 34 passed**, cleanup verified 0 stray rows |
| ├ existing groups | AGGREGATE_FY, DUAL, EXCESS_OF, PER_MONTH, PAN-missing, backdate guard, base-amount math, double-count regression, cross-tenant rejection | 22 / 22 |
| ├ `group_misconfiguration_guards` | DUAL with `threshold_amount_2` unset; unknown `threshold_type`; unknown `tds_mode` | 4 / 4 (new) |
| ├ `group_zero_taxable_base` | `subtotal=0` with real total — the ADZASIA shape | 1 / 1 (new) |
| ├ `group_arrears` | AGGREGATE_FY crossing; booked once; DUAL already-taxed must not re-charge | 3 / 3 (new) |
| └ `group_fy_bounds` | Fiscal years starting month 1, 4, 12 | 4 / 4 (new) |
| `pytest tests/` | Whole repo | 717 passed, **21 failed — all pre-existing** |

The 21 failures are identical with every change stashed, and are route-map
snapshot tests plus `test_fx.py::TestTdsOnForeignBill` (`KeyError: 'rate_id'` at
`staging_promotion.py:157`, an FX mock missing a key). None are TDS.

### Arrears scenarios in detail

| Scenario | Expected | Result |
|---|---|---|
| AGGREGATE_FY ₹50,000 @ 10%, bills 30k → 30k → 10k | ₹3,000 arrears on the untaxed first ₹30,000, booked once | PASS |
| DUAL ₹30k/bill or ₹1L/yr, bills 40k → 70k | **₹0** — the first bill was already taxed by the per-txn leg | PASS |
| DUAL, bills 25k → 25k → 60k | ₹1,000 arrears on the untaxed ₹50,000 | PASS |

The middle case is the one that matters: the first implementation booked ₹5,714
of arrears across 7 vendors, and checking the arithmetic showed Convertup had
₹1,91,740 billed against ₹3,834 deducted — the full amount already taxed at 2% —
yet still carried ₹1,628 of arrears. Prior bills taxed by the per-transaction leg
were being charged twice. Correct figure after the fix: **₹48 on one vendor**,
verifying exactly as untaxed ₹2,400 × 2%.

### Coverage

The suite previously passed 22/22 **without exercising a single bug in section 1**
— which is how they survived this long. Twelve checks were added to close that,
taking it to 34.

The additions were checked against the pre-fix engine, not just the fixed one: a
test that passes after a fix but would also have passed before it proves nothing.
**Seven of the twelve fail on the pre-fix engine**, which is the behaviour that
matters. The rest guard adjacent paths that were already correct (the per-txn leg
still firing, arrears not re-added on a later bill).

Still not covered: the worker's re-claim under genuine concurrency (bug #7) — it
needs two workers racing the same bill, which this suite's single-connection
model cannot stage.

---

## 5. Open items

| Item | Detail |
|---|---|
| **60 vendors, ₹17.31 crore, deducting ₹0** | No `vendor_category_id`, so `calculate_tds` exits at "No mapping rule — vendor not categorised" before any rate applies. Deduplication moved 13 of the original 73. Needs a per-vendor decision on what each supplies — not resolvable from the data. Largest: VIMANOTECH ₹15.2 cr, Unifinz ₹94.9 L, Crescendo ₹52.9 L |
| **7 payee names changed** | The PAN-RENAME merges, e.g. "RANA ENTERPRISES" now displays as "RAJ KUMAR". TDS-correct (thresholds aggregate per PAN) but review if the trade name matters operationally. Reversible individually |
| **194-IC payment code** | Placeholder `194IC`. Needs the real 4-digit code before filing |
| **`new_section_label` is NULL on all 17 sections** | The field is now settable, but the Act 2025 references are unverified. Confirmed for only 6 payments, from secondary sources |
| **Concurrency not covered by tests** | Bug #7's re-claim needs two workers racing one bill, which the suite's single-connection model cannot stage. The other 11 fixes are covered |
| **Dead schema columns** | `has_split_rate`, `rate_individual_huf`, `rate_others` warn but still exist. Consider dropping them |
| **`DISBURSEMENT_FORCE_SUCCESS`** | Temporary demo bypass. Remove once the Lendix API recovers |

### Scripts added

| Script | Purpose |
|---|---|
| `scripts/seed_tds_b2b_catalogue.py` | Install the B2B catalogue for a tenant. Idempotent, `--dry-run`. Also fixes the two tenants that have no sections at all |
| `scripts/resolve_vendor_duplicates.py` | Classify and merge duplicate vendors, then recompute their TDS. `--dry-run` by default |
| `scripts/fix_tds_cumulative_state.py` | Existing repair, now tenant-scoped |
