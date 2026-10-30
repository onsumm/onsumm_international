"""The service registry — the canonical list of things a tenant can be licensed for.

This module is the source of truth. `service_catalog` in Postgres is a *mirror*,
upserted from here on every boot, so that `tenant_service.service_key` can carry a
real foreign key and the admin panel can render display names without a second
copy of this list living in the frontend.

Why code and not the DB:

  * The enforcement layer references keys statically — ``require_service("fx")``,
    the route map in ``licensing.py``, the pipeline gates. If the registry lived
    only in the DB, every typo would become a silent open gate or a runtime 403.
    Here they are greppable and show up in a diff.
  * The repo already uses this idiom one layer up: ``frontend/src/routes/paths.ts``
    is a registry constant guarded by a totality test. ``tests/test_license_route_map.py``
    is the same thing for services.

Adding a service:
  1. Add a ``ServiceDef`` below.
  2. Add a rule to ``_RULES`` in ``licensing.py`` mapping its routes to the key
     (or ``UNLICENSED`` if it has no HTTP surface, like ``anomaly_ai``).
  3. Run the guard test. It fails if any route is unmapped or any key is unknown.

Never delete a ServiceDef. Retiring a service means dropping it from this tuple,
which makes the startup sync stamp ``service_catalog.retired_at``; the historical
``tenant_service`` rows stay for audit.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceDef:
    key: str
    display_name: str
    category: str            # core | compliance | payments | ai | integration
    is_core: bool            # granted at onboarding; disabling needs ?force=true
    default_enabled: bool    # what a tenant with no explicit row inherits
    metered_unit: str | None  # None => not metered
    description: str


# ── Category display order for the admin panel ────────────────────────────────
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("core", "Core AP"),
    ("compliance", "Tax & Compliance"),
    ("payments", "Payments"),
    ("ai", "AI & Automation"),
    ("integration", "Integrations"),
)


SERVICES: tuple[ServiceDef, ...] = (
    # ── Core AP ───────────────────────────────────────────────────────────────
    # These are still rows in tenant_service rather than an implicit always-on
    # set, so entitlement resolution has exactly one code path and ops can revoke
    # a module for a non-paying tenant without a special case.
    ServiceDef("bills", "Bills & Invoices", "core", True, True, "bills",
               "The bill list, bill detail, approvals, attachments and edits."),
    ServiceDef("vendors", "Vendor Master", "core", True, True, "vendors",
               "Vendor records, contacts, bank accounts, GSTINs and documents."),
    ServiceDef("upload_ocr", "Upload & OCR Extraction", "core", True, True, "pages",
               "Invoice upload and OCR extraction into a reviewable draft bill."),
    ServiceDef("approvals", "Approval Workflow", "core", True, True, None,
               "Department approval chains, email approve links and escalations."),
    ServiceDef("reports", "Reports & Dashboards", "core", True, True, None,
               "In-app dashboards, expense analytics and notification counts."),
    ServiceDef("ledger", "Ledger, COA & Categories", "core", True, True, None,
               "Chart of accounts rules, categories, cost centres and charges."),
    ServiceDef("brands", "Brands", "core", True, True, None,
               "Multiple billing entities under one tenant, with logos."),
    ServiceDef("users", "Users, Roles & Team", "core", True, True, "seats",
               "Tenant user accounts and role assignment."),
    ServiceDef("settings", "Tenant Settings", "core", True, True, None,
               "Company profile, approval chain configuration and preferences."),

    # ── Tax & compliance ──────────────────────────────────────────────────────
    ServiceDef("tds", "TDS Engine", "compliance", False, False, None,
               "Automatic TDS section mapping, deduction and FY threshold tracking."),
    ServiceDef("fx", "FX / Multi-Currency", "compliance", False, False, None,
               "Foreign-currency bills, live rates, exposure reporting and alerts."),
    ServiceDef("gst_tax_verification", "GST Tax-Type Verification", "compliance", False, False, None,
               "Flags CGST/SGST vs IGST mismatches against the place of supply."),
    ServiceDef("kyc_verification", "Vendor KYB Verification", "compliance", False, False, "checks",
               "GSTIN and bank-account verification of vendors via the KYB provider."),

    # ── Payments ──────────────────────────────────────────────────────────────
    ServiceDef("disbursement", "Vendor Payments", "payments", False, False, "payouts",
               "Initiating vendor payments over IMPS, with OTP authorisation."),
    ServiceDef("nova_billing", "Nova Billing Reconciliation", "payments", False, False, None,
               "Reconciles vendor API usage billing against received invoices."),
    ServiceDef("demo_payments", "Demo Payments", "payments", False, False, None,
               "Pay is simulated: no bank call, the bill settles as paid, and payment receipts go "
               "only to one demo POC email instead of the vendor."),
    ServiceDef("crypto_payouts", "Crypto Payouts (USDC)", "payments", False, False, "payouts",
               "Pays approved bills in USDC from the client's own Safe, with on-chain reconciliation. "
               "Never available to India-resident tenants."),
    ServiceDef("crypto_tds", "Crypto Withholding", "compliance", False, False, None,
               "Works out, holds and records tax withheld from USDC payouts, per jurisdiction rule pack."),
    ServiceDef("audit_anchor", "On-chain Audit Anchor", "compliance", False, False, None,
               "Hash-chains the audit log and anchors a daily Merkle root on a public chain."),

    # ── AI & automation ───────────────────────────────────────────────────────
    ServiceDef("agent_hub", "Agent Hub", "ai", False, False, "runs",
               "The email-to-bill agent: watches a mailbox and drafts bills."),
    ServiceDef("fact_graph", "Fact Graph", "ai", False, False, None,
               "Per-tenant knowledge graph over vendors, bills and payments."),
    ServiceDef("anomaly_ai", "Anomaly & Duplicate Detection", "ai", False, False, None,
               "Duplicate-bill detection, risk scores and payment predictions."),
    ServiceDef("sap_matching", "SAP Item-Code Matching", "ai", False, False, "matches",
               "Maps invoice line items to the tenant's SAP material master."),

    # The hub itself is core and default-on: it surfaces stalled approvals,
    # failed payments and OCR errors, which every tenant has whether or not they
    # bought an AI add-on. `anomaly_ai` gates only the agentic detector, and each
    # finding carries the service it came from so an unlicensed module's findings
    # are filtered out in the handler.
    ServiceDef("review_hub", "Review Hub", "core", True, True, None,
               "One queue for everything that needs correcting, across bills, vendors, "
               "payments, tax and reconciliation, routed to whoever can fix it."),

    # ── Integrations ──────────────────────────────────────────────────────────
    ServiceDef("data_extraction", "Bulk Data Extraction", "integration", False, False, "exports",
               "Bulk XLSX/CSV export of bills and the vendor bulk-upload sheet."),
    ServiceDef("tally_connector", "Tally Prime Connector", "integration", False, False, None,
               "The on-premise Tally agent: pairing, sync and ingest."),
    ServiceDef("accounting_api_sync", "SAP / ERP Connector", "integration", False, False, None,
               "SAP agent pairing and accounting-system synchronisation."),
    ServiceDef("email_sending", "Outbound Email", "integration", False, False, "emails",
               "Sending vendor emails from the user's own connected mailbox."),
)


SERVICE_KEYS: frozenset[str] = frozenset(s.key for s in SERVICES)
CORE_KEYS: frozenset[str] = frozenset(s.key for s in SERVICES if s.is_core)
BY_KEY: dict[str, ServiceDef] = {s.key: s for s in SERVICES}

# Services that carry a usage meter, and the metric name they count in.
METERED: dict[str, str] = {s.key: s.metered_unit for s in SERVICES if s.metered_unit}


def sort_order(key: str) -> int:
    """Stable display order: category order, then declaration order within it."""
    svc = BY_KEY[key]
    cat_index = next(i for i, (c, _) in enumerate(CATEGORIES) if c == svc.category)
    return cat_index * 100 + SERVICES.index(svc)
