"""Buyer identity and vendor identity must come from our own records, not the page.

Two rules, both about not letting a document assert who somebody is:

  * **Buyer** is always this tenant. An AP bill is billed to us by definition, so
    a misread "Bill To" block must never put another company's name or GSTIN on
    our payable — and the buyer GSTIN's state code feeds the inter-state/IGST
    check, so a wrong one is not merely cosmetic.
  * **Vendor** is matched on PAN, not GSTIN, because a company holds one PAN but
    a GSTIN per state. A branch invoice therefore arrives with a GSTIN we have
    never seen for a vendor we already know. KYB verification alone is enough to
    copy that vendor's identity onto the bill; serving its BANK account is a
    second, stricter gate, since invoice bank blocks are the standard
    payment-redirection vector.

The GSTIN itself is the deliberate exception: on a PAN match the bill keeps the
GSTIN it was printed with, so the state code stays truthful.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend_ocr"))

TENANT = "d0e00000-0000-0000-0000-000000000001"

# Same PAN (AABCT1332L), two state registrations: 29 = Karnataka, 27 = Maharashtra.
GSTIN_KA = "29AABCT1332L1ZU"
GSTIN_MH = "27AABCT1332L1Z5"
PAN = "AABCT1332L"


def _mods():
    """The buyer/vendor-master helpers, and the ingest module.

    These helpers used to live in app.main. They moved to the invoices router
    when main.py was split into app/routers/ — they belong to the enrichment
    endpoint that uses them. The local name stays `main` so the ~30 assertions
    below read unchanged.
    """
    try:
        from app.domain import ingest as ingest_mod
        from app.routers import invoices as main_mod
    except Exception as exc:                    # noqa: BLE001
        pytest.skip(f"backend not importable here: {exc}")
    return main_mod, ingest_mod


class _Cur:
    """psycopg2-ish cursor returning a queued row per fetchone(), recording SQL."""

    def __init__(self, *rows):
        self._rows = list(rows)
        self.sql: list[str] = []
        self.params: list[tuple | None] = []

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        self.params.append(params)

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None


# ── Buyer: always the tenant ───────────────────────────────────────────────────

BRAND = "b0000000-0000-0000-0000-000000000009"


class TestTenantBuyerBlock:
    def test_falls_back_to_tenant_legal_name_without_a_brand(self):
        main, _ = _mods()
        cur = _Cur(("Acme Industries Private Limited", GSTIN_KA))
        assert main._tenant_buyer_block(cur, TENANT) == {
            "name": "Acme Industries Private Limited",
            "brand": "",
            "gstin": GSTIN_KA,
            "pan": PAN,
        }

    def test_the_selected_brand_supplies_the_buyer_name(self):
        # A tenant bills under whichever brand the user is working in, and that
        # is the name that belongs on the bill — not the tenant's legal name.
        main, _ = _mods()
        cur = _Cur(("Acme Industries Private Limited", GSTIN_KA), ("Brightloans Private Limited",))
        got = main._tenant_buyer_block(cur, TENANT, BRAND)
        assert got["name"] == "Brightloans Private Limited"
        assert got["brand"] == "Brightloans Private Limited"
        assert got["gstin"] == GSTIN_KA        # brands carry no GSTIN of their own

    def test_brand_lookup_is_scoped_to_the_tenant(self):
        # Otherwise a caller could name another tenant's brand and print it on a bill.
        main, _ = _mods()
        cur = _Cur(("Acme Industries", GSTIN_KA), ("Brightloans Private Limited",))
        main._tenant_buyer_block(cur, TENANT, BRAND)
        assert "tenant_id = %s::uuid" in cur.sql[1]
        assert cur.params[1] == (BRAND, TENANT)

    def test_unknown_brand_falls_back_rather_than_blanking_the_name(self):
        main, _ = _mods()
        cur = _Cur(("Acme Industries", GSTIN_KA), None)
        assert main._tenant_buyer_block(cur, TENANT, BRAND)["name"] == "Acme Industries"

    def test_blank_legal_name_falls_back_to_company_name(self):
        # COALESCE(NULLIF(TRIM(legal_name),''), company_name) is done in SQL, so
        # what this pins is that the query asks for the fallback at all.
        main, _ = _mods()
        cur = _Cur(("Acme Trading", GSTIN_KA))
        assert main._tenant_buyer_block(cur, TENANT)["name"] == "Acme Trading"
        assert "COALESCE(NULLIF(TRIM(legal_name)" in cur.sql[0]

    def test_tenant_without_gstin_yields_empty_strings_not_none(self):
        # The React field binds to this value directly; None would render "None".
        main, _ = _mods()
        cur = _Cur(("Acme Trading", None))
        assert main._tenant_buyer_block(cur, TENANT) == {
            "name": "Acme Trading", "brand": "", "gstin": "", "pan": "",
        }

    def test_missing_tenant_row_returns_empty_dict(self):
        # The caller reads {} as "could not source the buyer" and leaves the
        # review screen's buyer fields editable rather than mislabelling OCR data.
        main, _ = _mods()
        assert main._tenant_buyer_block(_Cur(None), TENANT) == {}

    def test_missing_tenant_row_does_not_go_on_to_query_the_brand(self):
        main, _ = _mods()
        cur = _Cur(None)
        main._tenant_buyer_block(cur, TENANT, BRAND)
        assert len(cur.sql) == 1


# ── Vendor: PAN identity, verified-only ────────────────────────────────────────

def _master_row(gstn=GSTIN_KA, sba=True, legacy_bank_verified=True):
    """One dim_suppliers row joined to its verified bank account (or none).

    `legacy_bank_verified` is dim_suppliers.bank_verified: an account sitting in the
    legacy columns is only servable when that flag is set, so a row with sba=False
    and the flag clear is the KYB-verified-but-no-verified-bank case.
    """
    base = ("11111111-1111-1111-1111-111111111111", "Acme Supplies Pvt Ltd",
            gstn, "ap@acme.example", 45,
            "LEGACY0001", "LEGA0000001", "Legacy Bank", legacy_bank_verified)
    return base + (("SBA0009999", "SBIN0000123", "State Bank", "ACME SUPPLIES PVT LTD")
                   if sba else (None, None, None, None))


class TestVerifiedVendorByPan:
    def test_no_gstin_and_no_pan_short_circuits_without_querying(self):
        main, _ = _mods()
        cur = _Cur()
        assert main._verified_vendor_by_pan(cur, TENANT, None, None) is None
        assert cur.sql == []

    def test_no_verified_vendor_on_that_pan_returns_none(self):
        # Caller then leaves the document's own vendor/bank values standing.
        main, _ = _mods()
        assert main._verified_vendor_by_pan(_Cur(None), TENANT, GSTIN_KA, None) is None

    def test_queries_by_pan_extracted_from_the_gstin(self):
        main, _ = _mods()
        cur = _Cur(_master_row())
        main._verified_vendor_by_pan(cur, TENANT, GSTIN_KA, None)
        assert cur.params[0] == (TENANT, PAN)

    def test_gate_is_kyb_verification_alone(self):
        # Bank verification is a SEPARATE gate — see TestVendorVerifiedWithoutBank.
        # Requiring both here meant a KYB-verified vendor with no verified account
        # returned None, so the AP team retyped an email and PAN we already held.
        main, _ = _mods()
        cur = _Cur(_master_row())
        main._verified_vendor_by_pan(cur, TENANT, GSTIN_KA, None)
        sql = cur.sql[0]
        assert "s.status = 'VERIFIED'" in sql
        assert "s.is_active = true" in sql
        # The bank condition must no longer gate the row.
        assert "AND (sba.account_number IS NOT NULL" not in sql

    def test_different_state_gstin_same_pan_matches_on_pan(self):
        # The bill is from the Maharashtra branch; our record is the Karnataka one.
        main, _ = _mods()
        got = main._verified_vendor_by_pan(_Cur(_master_row(gstn=GSTIN_KA)),
                                           TENANT, GSTIN_MH, None)
        assert got["matched_on"] == "PAN"
        assert got["pan"] == PAN
        assert got["master_gstin"] == GSTIN_KA

    def test_same_gstin_reports_a_gstin_match(self):
        main, _ = _mods()
        got = main._verified_vendor_by_pan(_Cur(_master_row(gstn=GSTIN_KA)),
                                           TENANT, GSTIN_KA, None)
        assert got["matched_on"] == "GSTIN"

    def test_gstin_comparison_ignores_case_and_padding(self):
        main, _ = _mods()
        got = main._verified_vendor_by_pan(_Cur(_master_row(gstn=GSTIN_KA)),
                                           TENANT, f"  {GSTIN_KA.lower()} ", None)
        assert got["matched_on"] == "GSTIN"

    def test_bank_comes_from_supplier_bank_accounts_with_name_at_bank(self):
        main, _ = _mods()
        got = main._verified_vendor_by_pan(_Cur(_master_row(sba=True)),
                                           TENANT, GSTIN_MH, None)
        assert got["bank_details"] == {
            "account_no": "SBA0009999", "ifsc": "SBIN0000123",
            "bank_name": "State Bank", "account_name": "ACME SUPPLIES PVT LTD",
        }

    def test_falls_back_to_legacy_columns_when_no_bank_account_row(self):
        # Legacy dim_suppliers.bank_* has no beneficiary name, so the vendor's
        # own name stands in — same substitution disbursement_api makes.
        main, _ = _mods()
        got = main._verified_vendor_by_pan(_Cur(_master_row(sba=False)),
                                           TENANT, GSTIN_MH, None)
        assert got["bank_details"] == {
            "account_no": "LEGACY0001", "ifsc": "LEGA0000001",
            "bank_name": "Legacy Bank", "account_name": "Acme Supplies Pvt Ltd",
        }

    def test_pan_column_used_when_the_document_has_no_gstin(self):
        main, _ = _mods()
        cur = _Cur(_master_row())
        got = main._verified_vendor_by_pan(cur, TENANT, None, " aabct1332l ")
        assert cur.params[0] == (TENANT, PAN)
        assert got["matched_on"] == "PAN"


# ── Ingest: a branch GSTIN must not fork the vendor ────────────────────────────

class TestUpsertSupplierPanTier:
    def test_new_gstin_on_a_verified_pan_reuses_that_vendor(self):
        _, ingest = _mods()
        cur = _Cur(None,                                        # tier 1: GSTN miss
                   ("11111111-1111-1111-1111-111111111111",))   # tier 1b: PAN hit
        got = ingest._upsert_supplier(
            cur, {"name": "Acme Supplies - Mumbai", "gstin": GSTIN_MH}, TENANT,
            {"account_no": "SPOOFED123", "ifsc": "FAKE0000001"},
        )
        assert got == "11111111-1111-1111-1111-111111111111"

    def test_the_new_gstin_is_registered_against_the_survivor(self):
        _, ingest = _mods()
        cur = _Cur(None, ("11111111-1111-1111-1111-111111111111",))
        ingest._upsert_supplier(cur, {"name": "Acme - Mumbai", "gstin": GSTIN_MH},
                                TENANT, {})
        inserts = [s for s in cur.sql if "INSERT INTO supplier_gstns" in s]
        assert len(inserts) == 1
        assert cur.params[-1] == (TENANT, "11111111-1111-1111-1111-111111111111", GSTIN_MH)

    def test_the_verified_master_is_not_overwritten_by_the_document(self):
        # The whole point of the gate: a verified vendor's name and bank details
        # were checked by a human or a KYB call, the invoice's were not.
        _, ingest = _mods()
        cur = _Cur(None, ("11111111-1111-1111-1111-111111111111",))
        ingest._upsert_supplier(
            cur, {"name": "ACME SUPPLIES (MUMBAI BR)", "gstin": GSTIN_MH}, TENANT,
            {"account_no": "SPOOFED123", "ifsc": "FAKE0000001", "bank_name": "Fake Bank"},
        )
        assert not [s for s in cur.sql if "UPDATE dim_suppliers" in s]
        assert "SPOOFED123" not in str(cur.params)

    def test_pan_tier_only_adopts_verified_vendors(self):
        _, ingest = _mods()
        cur = _Cur(None, ("11111111-1111-1111-1111-111111111111",))
        ingest._upsert_supplier(cur, {"name": "Acme", "gstin": GSTIN_MH}, TENANT, {})
        assert "status = 'VERIFIED'" in cur.sql[1]

    def test_no_pan_match_falls_through_to_the_existing_tiers(self):
        _, ingest = _mods()
        cur = _Cur(None,                                        # tier 1: GSTN miss
                   None,                                        # tier 1b: PAN miss
                   None,                                        # tier 2: no UPLOAD row
                   ("22222222-2222-2222-2222-222222222222",))   # tier 3: created
        got = ingest._upsert_supplier(cur, {"name": "Brand New Vendor", "gstin": GSTIN_MH},
                                      TENANT, {})
        assert got == "22222222-2222-2222-2222-222222222222"
        assert [s for s in cur.sql if "INSERT INTO dim_suppliers" in s]

    def test_a_vendor_without_a_gstin_skips_the_pan_tier(self):
        # Nothing to derive a PAN from — going to tier 2 on name is the old path.
        _, ingest = _mods()
        cur = _Cur(None, ("33333333-3333-3333-3333-333333333333",))
        got = ingest._upsert_supplier(cur, {"name": "Cash Vendor"}, TENANT, {})
        assert got == "33333333-3333-3333-3333-333333333333"
        assert not [s for s in cur.sql if "status = 'VERIFIED'" in s]


# ── Category: inherited from the vendor's last bill ────────────────────────────

SUPPLIER = "55555555-5555-5555-5555-555555555555"


class TestLastBillCategory:
    """A vendor bills for the same thing every month, so its previous bill's category
    is a better default than re-deriving one from the line items. The identity used to
    find that history is the point: a verified vendor is keyed on supplier_id, because
    matching on the printed GSTIN misses a branch invoice and the name fallback needs a
    character-exact OCR read."""

    def test_verified_vendor_matches_history_on_supplier_id(self):
        main, _ = _mods()
        cur = _Cur(("Software / SaaS",))
        assert main._last_bill_category(cur, TENANT, SUPPLIER, GSTIN_MH, "Ignosi") == "Software / SaaS"
        assert "fi.supplier_id = %s" in cur.sql[0]
        assert cur.params[0] == (TENANT, SUPPLIER)

    def test_supplier_id_path_never_joins_on_gstin_or_name(self):
        # The whole point: a branch invoice's unseen GSTIN must not gate the lookup.
        main, _ = _mods()
        cur = _Cur(("Rent",))
        main._last_bill_category(cur, TENANT, SUPPLIER, "27NEWGSTIN0000Z", "Wrong Name Ltd")
        assert "dim_suppliers" not in cur.sql[0]
        assert "s.gstn" not in cur.sql[0]

    def test_only_ap_bills_count_towards_a_vendor_category(self):
        main, _ = _mods()
        cur = _Cur(("Utilities",))
        main._last_bill_category(cur, TENANT, SUPPLIER, "", "")
        assert "fi.invoice_type = 'AP'" in cur.sql[0]

    def test_most_recent_bill_wins(self):
        main, _ = _mods()
        cur = _Cur(("Travel & Expenses",))
        main._last_bill_category(cur, TENANT, SUPPLIER, "", "")
        assert "ORDER BY fi.created_at DESC" in cur.sql[0]
        assert "LIMIT 1" in cur.sql[0]

    def test_blank_and_whitespace_only_categories_are_not_inherited(self):
        main, _ = _mods()
        cur = _Cur(("Rent",))
        main._last_bill_category(cur, TENANT, SUPPLIER, "", "")
        assert "COALESCE(TRIM(fi.category), '') != ''" in cur.sql[0]

    def test_history_is_scoped_to_the_tenant(self):
        main, _ = _mods()
        cur = _Cur(("Rent",))
        main._last_bill_category(cur, TENANT, SUPPLIER, "", "")
        assert "fi.tenant_id = %s" in cur.sql[0]
        assert cur.params[0][0] == TENANT

    def test_vendor_with_no_prior_bill_returns_none(self):
        main, _ = _mods()
        cur = _Cur(None)
        assert main._last_bill_category(cur, TENANT, SUPPLIER, GSTIN_MH, "Ignosi") is None

    def test_unverified_vendor_keeps_the_gstin_and_name_lookup(self):
        main, _ = _mods()
        cur = _Cur(("Food & Beverages",))
        got = main._last_bill_category(cur, TENANT, None, GSTIN_MH, "Chai Point")
        assert got == "Food & Beverages"
        assert "JOIN dim_suppliers" in cur.sql[0]
        assert cur.params[0] == (TENANT, GSTIN_MH, GSTIN_MH, "Chai Point")

    def test_no_identity_at_all_does_not_query(self):
        main, _ = _mods()
        cur = _Cur(("Rent",))
        assert main._last_bill_category(cur, TENANT, None, "", "") is None
        assert cur.sql == []


# ── Vendor verified, bank not: two independent gates ──────────────────────────

class TestVendorVerifiedWithoutBank:
    """A KYB-verified vendor with no verified account still supplies its identity.

    These are different risks. A name or an email is not a payment instruction, so
    KYB verification is enough to copy it. A bank account IS a payment instruction,
    and invoice bank blocks are the standard redirection vector, so serving one needs
    that account to have been verified. Conflating them cost us the whole record: a
    vendor missing a verified bank returned None, and the AP team retyped an email
    and PAN we already held.
    """

    def test_identity_is_served_without_any_verified_bank(self):
        main, _ = _mods()
        got = main._verified_vendor_by_pan(
            _Cur(_master_row(sba=False, legacy_bank_verified=False)),
            TENANT, GSTIN_KA, None)
        assert got is not None                      # used to be None — the bug
        assert got["name"] == "Acme Supplies Pvt Ltd"
        assert got["email"] == "ap@acme.example"
        assert got["pan"] == PAN
        assert got["payment_terms_days"] == 45

    def test_unverified_bank_is_never_served(self):
        # Legacy columns hold an account but bank_verified is false: offering it
        # would present an unvouched account as though we stood behind it.
        main, _ = _mods()
        got = main._verified_vendor_by_pan(
            _Cur(_master_row(sba=False, legacy_bank_verified=False)),
            TENANT, GSTIN_KA, None)
        assert got["bank_details"] is None
        assert got["bank_verified"] is False

    def test_bank_verified_true_when_a_verified_account_row_exists(self):
        main, _ = _mods()
        got = main._verified_vendor_by_pan(_Cur(_master_row(sba=True)), TENANT, GSTIN_KA, None)
        assert got["bank_verified"] is True
        assert got["bank_details"]["account_no"] == "SBA0009999"

    def test_legacy_account_counts_only_when_its_flag_is_set(self):
        main, _ = _mods()
        got = main._verified_vendor_by_pan(
            _Cur(_master_row(sba=False, legacy_bank_verified=True)),
            TENANT, GSTIN_KA, None)
        assert got["bank_verified"] is True
        assert got["bank_details"]["account_no"] == "LEGACY0001"

    def test_a_verified_account_is_preferred_over_an_unverified_row(self):
        # Ordering matters: two rows can share a PAN, and the one we can actually
        # pay should win rather than whichever was created first.
        main, _ = _mods()
        cur = _Cur(_master_row())
        main._verified_vendor_by_pan(cur, TENANT, GSTIN_KA, None)
        sql = cur.sql[0]
        assert "ORDER BY (sba.account_number IS NOT NULL) DESC" in sql
        assert "(s.bank_verified AND s.bank_account IS NOT NULL) DESC" in sql
