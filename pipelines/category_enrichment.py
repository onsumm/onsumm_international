"""
Category Enrichment Pipeline
─────────────────────────────
Finds bills and invoices where `category` is NULL and backfills it using
the COA rules defined in the `coa_rules` table.

Resolution order (highest priority first):
  1. COA rule with matching vendor_name + a category value set
  2. Supplier default category (`dim_suppliers.category`)
  3. Keyword match on vendor name (built-in mappings)
  4. Leave NULL — never guess blindly

Once a category is resolved it is written to:
  - `fact_invoices.category`
  - `stg_bills.category`   (via stg_bill_id FK, if present)

Also backfills `dim_suppliers.category` so future bills from the same
supplier are categorised immediately at ingest time.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import asyncpg

import config
from db import get_pool
from pipelines.base import BaseWorker

logger = logging.getLogger(__name__)

# ── Built-in keyword → category map ───────────────────────────────────────────
# Ordered list: first match wins.  Keys are lowercase substrings of vendor name.

_KEYWORD_RULES: list[tuple[list[str], str]] = [
    # Software / SaaS
    (["aws", "amazon web", "azure", "google cloud", "gcp", "microsoft 365",
      "office 365", "gsuite", "github", "atlassian", "jira", "slack",
      "zoom", "dropbox", "hubspot", "salesforce", "zoho", "freshdesk",
      "apify", "twilio", "sendgrid", "cloudflare", "sentry", "datadog",
      "netlify", "vercel", "heroku", "digitalocean", "linode", "signzy",
      "digio", "razorpay", "stripe", "cashfree", "payu", "instamojo",
      "software", "saas", "subscription", "license"],
     "Software / SaaS"),

    # Rent
    (["rent", "lease", "office space", "coworking", "indiqube",
      "regus", "awfis", "wework", "91springboard"],
     "Rent"),

    # Food & Beverages
    (["swiggy", "zomato", "cafe", "canteen", "catering",
      "restaurant", "food", "beverage", "snack", "pantry"],
     "Food & Beverages"),

    # Travel & Expenses
    (["travel", "flight", "airline", "hotel", "accommodation",
      "cab", "uber", "ola", "rapido", "train", "railway",
      "irctc", "makemytrip", "cleartrip", "booking.com"],
     "Travel & Expenses"),

    # Utilities
    (["electricity", "power", "bses", "tata power", "adani electricity",
      "water", "internet", "broadband", "wifi", "jio", "airtel",
      "bsnl", "vodafone", "vi mobile", "tata tele", "starhub",
      "telecom", "utility", "gas", "lpg", "indane"],
     "Utilities"),

    # Reimbursement / Admin
    (["reimbursement", "salary", "payroll", "stipend",
      "advance", "petty cash", "admin", "miscellaneous"],
     "Reimbursement"),
]


def _keyword_category(vendor_name: str) -> str | None:
    v = vendor_name.lower()
    for keywords, category in _KEYWORD_RULES:
        if any(kw in v for kw in keywords):
            return category
    return None


# ── COA rules loader ───────────────────────────────────────────────────────────

async def _load_coa_rules(conn: asyncpg.Connection, tenant_id: str) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT vendor_name, category, exp_head, priority
        FROM   coa_rules
        WHERE  tenant_id = $1
          AND  vendor_name IS NOT NULL
          AND  category    IS NOT NULL
        ORDER  BY priority DESC
        """,
        tenant_id,
    )
    return [dict(r) for r in rows]


def _coa_category(vendor_name: str, rules: list[dict[str, Any]]) -> str | None:
    """Return the category from the first matching vendor COA rule."""
    v = vendor_name.lower()
    for r in rules:
        rv = (r["vendor_name"] or "").lower()
        if rv and rv in v:
            return r["category"]
    return None


# ── Worker ─────────────────────────────────────────────────────────────────────

class CategoryEnrichmentWorker(BaseWorker):
    """
    Backfills `category` on fact_invoices + stg_bills rows that are NULL.
    Also writes the resolved category back to dim_suppliers so future
    bills from the same vendor skip this step entirely.
    """

    name           = "category_enrichment"
    notify_channel = "ch_category_enrichment"

    async def process_batch(self) -> int:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # ── Fetch uncategorised bills ──────────────────────────────────
            tenant_ids = config.PIPELINE_TENANT_IDS
            if tenant_ids:
                rows = await conn.fetch(
                    """
                    SELECT
                        fi.invoice_id,
                        fi.stg_bill_id,
                        fi.tenant_id::text   AS tenant_id,
                        fi.supplier_id,
                        ds.name              AS vendor_name,
                        ds.category          AS supplier_category
                    FROM  fact_invoices fi
                    LEFT  JOIN dim_suppliers ds
                           ON  ds.supplier_id = fi.supplier_id
                          AND  ds.tenant_id   = fi.tenant_id
                    WHERE (fi.category IS NULL OR fi.category = '')
                      AND fi.tenant_id = ANY($2::uuid[])
                    LIMIT $1
                    """,
                    self.batch_size, tenant_ids,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT
                        fi.invoice_id,
                        fi.stg_bill_id,
                        fi.tenant_id::text   AS tenant_id,
                        fi.supplier_id,
                        ds.name              AS vendor_name,
                        ds.category          AS supplier_category
                    FROM  fact_invoices fi
                    LEFT  JOIN dim_suppliers ds
                           ON  ds.supplier_id = fi.supplier_id
                          AND  ds.tenant_id   = fi.tenant_id
                    WHERE (fi.category IS NULL OR fi.category = '')
                    LIMIT $1
                    """,
                    self.batch_size,
                )

            if not rows:
                return 0

            # Group by tenant so we load COA rules once per tenant per batch
            tenant_rules: dict[str, list[dict]] = {}
            for row in rows:
                tid = row["tenant_id"]
                if tid not in tenant_rules:
                    tenant_rules[tid] = await _load_coa_rules(conn, tid)

            updated = 0
            async with conn.transaction():
                for row in rows:
                    vendor = (row["vendor_name"] or "").strip()
                    tid    = row["tenant_id"]

                    # Resolution order
                    category: str | None = None

                    # 1 — COA rule: vendor match with category
                    if vendor:
                        category = _coa_category(vendor, tenant_rules[tid])

                    # 2 — Supplier default category
                    if not category and row["supplier_category"]:
                        category = row["supplier_category"]

                    # 3 — Keyword match on vendor name
                    if not category and vendor:
                        category = _keyword_category(vendor)

                    if not category:
                        continue  # nothing to set; skip

                    # ── Write to fact_invoices ─────────────────────────────
                    await conn.execute(
                        "UPDATE fact_invoices SET category = $1 WHERE invoice_id = $2",
                        category, row["invoice_id"],
                    )

                    # ── Write to stg_bills if linked ──────────────────────
                    if row["stg_bill_id"]:
                        await conn.execute(
                            "UPDATE stg_bills SET category = $1 WHERE stg_bill_id = $2",
                            category, row["stg_bill_id"],
                        )

                    # ── Backfill dim_suppliers.category so future bills
                    #    from this vendor are instantly categorised ─────────
                    if row["supplier_id"]:
                        await conn.execute(
                            """UPDATE dim_suppliers
                               SET category = $1
                               WHERE supplier_id = $2
                                 AND (category IS NULL OR category = '')""",
                            category, row["supplier_id"],
                        )

                    updated += 1
                    logger.info(
                        "[category_enrichment] invoice=%s vendor=%r → %s",
                        row["invoice_id"], vendor, category,
                    )

            logger.info("[category_enrichment] enriched %d / %d records", updated, len(rows))
            return updated
