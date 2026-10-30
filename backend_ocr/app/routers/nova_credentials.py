"""
nova_credentials_api.py — tenant-configurable Nova API key, one per brand.

Lets a tenant save the API key their Nova account issued them, so backend
services (services/nova_reconciliation/, or whatever schedules the
GET {NOVA_BILLING_BASE_URL}/billing/monthly pull) can call the Nova API on
their behalf, instead of every tenant sharing one operator-owned key.

A tenant with multiple brands can hold a DIFFERENT Nova key per brand (e.g.
Avinash Capital Markets Ltd has separate keys for its Brightloans and
Kreditbuddha brands) — `brand IS NULL` is the tenant-wide default, used when
no brand-specific row exists for the brand being reconciled.

The key is encrypted at rest (Fernet, NOVA_KEY_ENCRYPTION_KEY) and never
returned in plaintext once saved — GET responses carry a masked preview only,
the same one-way-display convention as a payment card on file.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..security.auth import require_tenant
from ..config import DB_URL, NOVA_KEY_ENCRYPTION_KEY

# Same convention billing_api.py already used for this value — not imported from the
# root config.py, just re-read locally so this module has no cross-package dependency.
NOVA_BILLING_BASE_URL = os.getenv("NOVA_BILLING_BASE_URL", "https://api.lendix.ai")

router = APIRouter(prefix="/api/nova", tags=["nova-credentials"])


def _get_conn():
    import psycopg2
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    return conn


def _fernet():
    from cryptography.fernet import Fernet
    if not NOVA_KEY_ENCRYPTION_KEY:
        raise HTTPException(
            status_code=503,
            detail="Nova key storage is not configured on this server (NOVA_KEY_ENCRYPTION_KEY unset)",
        )
    return Fernet(NOVA_KEY_ENCRYPTION_KEY.encode())


def _encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


def _mask(plaintext: str) -> str:
    if len(plaintext) <= 8:
        return "•" * len(plaintext)
    return f"{plaintext[:4]}{'•' * 8}{plaintext[-4:]}"


class NovaKeyNotConfigured(Exception):
    """Raised by resolve_tenant_nova_key() when a tenant has no active Nova API key on
    file for the requested brand (or as a tenant-wide default). Callers must surface
    this as an actionable prompt ("connect your Nova API key under Integrations"),
    never silently fall back to a shared/env-var key — each tenant's own key is the
    only key this service is willing to call Nova with."""

    def __init__(self, tenant_id: str, brand: Optional[str]):
        self.tenant_id = tenant_id
        self.brand = brand
        scope = f"brand '{brand}'" if brand else "a tenant-wide default"
        super().__init__(
            f"Tenant {tenant_id} has no Nova API key configured for {scope} (Integrations > Nova)"
        )


def resolve_tenant_nova_key(tenant_id: str, brand: Optional[str] = None) -> dict:
    """The single source of truth for "which Nova API key do we call on behalf of this
    tenant/brand" — always the tenant's own key from int_nova_credentials (entered via
    Integrations > Nova, see IntegrationsPage.tsx), never a shared operator key or an
    environment variable. Whatever eventually schedules the
    GET {NOVA_BILLING_BASE_URL}/billing/monthly pull that feeds
    services/nova_reconciliation's POST /v1/reconcile should call this first.

    Lookup order when `brand` is given: the brand-specific row first, then the
    tenant-wide default (brand IS NULL). Without `brand`, only the default is tried.

    Returns {"api_key": str, "base_url": str} — base_url falls back to the platform
    default (NOVA_BILLING_BASE_URL) when neither row has a per-tenant override.
    Raises NovaKeyNotConfigured if nothing matches — a normal, expected outcome for a
    tenant/brand who hasn't connected Nova yet, not a system error; catch it and
    prompt them to configure one.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            if brand:
                cur.execute(
                    "SELECT api_key_encrypted, base_url FROM int_nova_credentials "
                    "WHERE tenant_id = %s AND brand = %s AND is_active = true",
                    (tenant_id, brand),
                )
                row = cur.fetchone()
            else:
                row = None
            if not row:
                cur.execute(
                    "SELECT api_key_encrypted, base_url FROM int_nova_credentials "
                    "WHERE tenant_id = %s AND brand IS NULL AND is_active = true",
                    (tenant_id,),
                )
                row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        raise NovaKeyNotConfigured(tenant_id, brand)

    api_key_encrypted, base_url = row
    return {"api_key": _decrypt(api_key_encrypted), "base_url": base_url or NOVA_BILLING_BASE_URL}


class SaveCredentialsRequest(BaseModel):
    api_key: str = Field(min_length=8, max_length=512)
    base_url: Optional[str] = None
    brand: Optional[str] = Field(default=None, description="Omit for a tenant-wide default key; set to scope this key to one brand.")


class CredentialsStatus(BaseModel):
    configured: bool
    brand: Optional[str] = None
    masked_key: Optional[str] = None
    base_url: Optional[str] = None
    is_active: bool = False
    updated_at: Optional[str] = None


class CredentialsList(BaseModel):
    credentials: list[CredentialsStatus]


@router.get("/credentials", response_model=CredentialsList)
def get_credentials():
    """All of this tenant's configured keys — the tenant-wide default (brand: null),
    if any, plus one entry per brand-specific override."""
    tenant = require_tenant()
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT brand, api_key_encrypted, base_url, is_active, updated_at "
                "FROM int_nova_credentials WHERE tenant_id = %s ORDER BY brand NULLS FIRST",
                (tenant,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return CredentialsList(credentials=[
        CredentialsStatus(
            configured=True,
            brand=brand,
            masked_key=_mask(_decrypt(api_key_encrypted)),
            base_url=base_url,
            is_active=is_active,
            updated_at=updated_at.isoformat() if updated_at else None,
        )
        for brand, api_key_encrypted, base_url, is_active, updated_at in rows
    ])


@router.post("/credentials", response_model=CredentialsStatus)
def save_credentials(body: SaveCredentialsRequest):
    tenant = require_tenant()
    encrypted = _encrypt(body.api_key)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            if body.brand:
                cur.execute(
                    """
                    INSERT INTO int_nova_credentials (tenant_id, brand, api_key_encrypted, base_url, is_active, updated_at)
                    VALUES (%s, %s, %s, %s, true, now())
                    ON CONFLICT (tenant_id, brand) WHERE brand IS NOT NULL DO UPDATE
                        SET api_key_encrypted = EXCLUDED.api_key_encrypted,
                            base_url = EXCLUDED.base_url,
                            is_active = true,
                            updated_at = now()
                    RETURNING base_url, is_active, updated_at
                    """,
                    (tenant, body.brand, encrypted, body.base_url),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO int_nova_credentials (tenant_id, brand, api_key_encrypted, base_url, is_active, updated_at)
                    VALUES (%s, NULL, %s, %s, true, now())
                    ON CONFLICT (tenant_id) WHERE brand IS NULL DO UPDATE
                        SET api_key_encrypted = EXCLUDED.api_key_encrypted,
                            base_url = EXCLUDED.base_url,
                            is_active = true,
                            updated_at = now()
                    RETURNING base_url, is_active, updated_at
                    """,
                    (tenant, encrypted, body.base_url),
                )
            base_url, is_active, updated_at = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    return CredentialsStatus(
        configured=True,
        brand=body.brand,
        masked_key=_mask(body.api_key),
        base_url=base_url,
        is_active=is_active,
        updated_at=updated_at.isoformat() if updated_at else None,
    )


@router.delete("/credentials", response_model=CredentialsStatus)
def delete_credentials(brand: Optional[str] = Query(default=None)):
    tenant = require_tenant()
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            if brand:
                cur.execute("DELETE FROM int_nova_credentials WHERE tenant_id = %s AND brand = %s", (tenant, brand))
            else:
                cur.execute("DELETE FROM int_nova_credentials WHERE tenant_id = %s AND brand IS NULL", (tenant,))
        conn.commit()
    finally:
        conn.close()
    return CredentialsStatus(configured=False, brand=brand)
