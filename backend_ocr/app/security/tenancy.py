"""Tenant-ownership primitives for row lookups.

The rule this module exists to enforce
──────────────────────────────────────
A handler must never learn the tenant from the row it is about to act on.

``auth.py`` pins the authenticated tenant for the request, but enforcement was
opt-in per handler, and 18 handlers never opted in. They looked a row up by its
UUID alone and then read ``tenant_id`` *out of the row they had just fetched* —
so the tenant of the record became the tenant of the operation, and any logged-in
user of any tenant could act on any record whose id they held. The worst case was
``POST /api/bills/{invoice_id}/action``: approve, reject or send back any bill in
the database.

Two things follow from that, and both are load-bearing:

  * The tenant predicate belongs **in the query**, alongside the primary key —
    never in a Python comparison after the fetch, which is easy to omit and
    invisible in review.
  * A row that exists but belongs to someone else must be indistinguishable from
    a row that does not exist. Both raise 404, never 403 — a 403 would confirm
    the record is real and turn these endpoints into an existence oracle for
    other tenants' invoice and vendor ids.

Where a handler already runs a ``SELECT`` on the record, add ``AND tenant_id = %s``
to that statement rather than calling a helper here — one round trip, and the
predicate sits where a reviewer reads the table name. The helpers below are for
the cases with no natural select: a bare ``UPDATE``/``DELETE`` by id, or a child
table that carries no ``tenant_id`` of its own and has to be scoped through its
parent.

``tests/test_tenant_route_map.py`` is the backstop: it fails when a route reaches
neither ``require_tenant()`` nor anything in this module, so the next handler
cannot silently repeat the mistake.
"""
from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import Depends, HTTPException

from ..security.auth import require_tenant

__all__ = [
    "Tenant",
    "assert_owned",
    "owned_bill",
    "owned_vendor",
    "owned_attachment",
]


# Signature-level injection: `def handler(..., tenant: Tenant)` both documents
# the scoping and gives FastAPI the 401 for free, replacing the 82 hand-copied
# `tenant = require_tenant()  # client company_id ignored` lines.
Tenant = Annotated[str, Depends(require_tenant)]


# Table and column names are interpolated into SQL below, so they are restricted
# to bare identifiers and must only ever come from literals in our own source —
# never from a request. Values always travel as bound parameters.
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


def assert_owned(
    cur,
    table: str,
    pk_col: str,
    pk_val: Any,
    *,
    label: str = "Record",
    tenant: str | None = None,
) -> str:
    """Confirm one row of a tenant-owned table belongs to the caller's tenant.

    Returns the authenticated tenant id, so callers can use it for the writes
    that follow instead of re-deriving it. Raises 404 when the row is missing
    **or** owned by another tenant — see the module docstring on why these are
    deliberately the same answer.
    """
    tenant = tenant or require_tenant()
    cur.execute(
        f"SELECT 1 FROM {_ident(table)} WHERE {_ident(pk_col)} = %s AND tenant_id = %s",
        (pk_val, tenant),
    )
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return tenant


def owned_bill(cur, invoice_id: Any, *, tenant: str | None = None) -> str:
    """Assert the bill is the caller's. Returns the authenticated tenant id."""
    return assert_owned(
        cur, "fact_invoices", "invoice_id", invoice_id, label="Bill", tenant=tenant
    )


def owned_vendor(cur, supplier_id: Any, *, tenant: str | None = None) -> str:
    """Assert the vendor is the caller's. Returns the authenticated tenant id."""
    return assert_owned(
        cur, "dim_suppliers", "supplier_id", supplier_id, label="Vendor", tenant=tenant
    )


def owned_attachment(
    cur, invoice_id: Any, attachment_id: Any, *, tenant: str | None = None
) -> str:
    """Assert an attachment belongs to the caller's tenant *and* to this bill.

    ``bill_attachments`` carries its own ``tenant_id``; its child
    ``bill_attachment_edits`` does not, which is why edits have to be scoped by
    joining back to this row rather than filtered directly.
    """
    tenant = tenant or require_tenant()
    cur.execute(
        """SELECT 1 FROM bill_attachments
           WHERE id = %s AND invoice_id = %s AND tenant_id = %s AND deleted_at IS NULL""",
        (attachment_id, invoice_id, tenant),
    )
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return tenant
