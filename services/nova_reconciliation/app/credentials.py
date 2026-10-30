"""
Tenant Nova API key resolution — self-contained copy of backend_ocr's
resolve_tenant_nova_key(), read here instead of imported, so this service has
no cross-package dependency on backend_ocr (NOVA_RECONCILIATION_LOGIC.md §1:
independently deployed). Same table (int_nova_credentials), same encryption
(Fernet, NOVA_KEY_ENCRYPTION_KEY), same lookup order: brand-specific row
first, then the tenant-wide default (brand IS NULL).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

NOVA_BILLING_BASE_URL = os.getenv("NOVA_BILLING_BASE_URL", "https://api.lendix.ai")
NOVA_KEY_ENCRYPTION_KEY = os.getenv("NOVA_KEY_ENCRYPTION_KEY", "")


class NovaKeyNotConfigured(Exception):
    def __init__(self, tenant_id: str, brand: Optional[str]):
        self.tenant_id = tenant_id
        self.brand = brand
        scope = f"brand '{brand}'" if brand else "a tenant-wide default"
        super().__init__(f"Tenant {tenant_id} has no Nova API key configured for {scope} (Integrations > Nova)")


def _decrypt(ciphertext: str) -> str:
    from cryptography.fernet import Fernet
    if not NOVA_KEY_ENCRYPTION_KEY:
        raise RuntimeError("NOVA_KEY_ENCRYPTION_KEY is not set — cannot decrypt stored Nova API keys")
    return Fernet(NOVA_KEY_ENCRYPTION_KEY.encode()).decrypt(ciphertext.encode()).decode()


def resolve_tenant_nova_key(conn, tenant_id: str, brand: Optional[str] = None) -> dict:
    """Returns {"api_key": str, "base_url": str}. Raises NovaKeyNotConfigured — a
    normal, expected outcome for a tenant/brand that hasn't connected Nova yet, not
    a system error. `conn` is a psycopg2 connection borrowed from this service's own
    pool (config.get_conn()) — int_nova_credentials lives in the same shared
    platform DB this service already reads dim_tenants/dim_cost_centres from."""
    with conn.cursor() as cur:
        row = None
        if brand:
            cur.execute(
                "SELECT api_key_encrypted, base_url FROM int_nova_credentials "
                "WHERE tenant_id = %s AND brand = %s AND is_active = true",
                (tenant_id, brand),
            )
            row = cur.fetchone()
        if not row:
            cur.execute(
                "SELECT api_key_encrypted, base_url FROM int_nova_credentials "
                "WHERE tenant_id = %s AND brand IS NULL AND is_active = true",
                (tenant_id,),
            )
            row = cur.fetchone()

    if not row:
        raise NovaKeyNotConfigured(tenant_id, brand)

    api_key_encrypted, base_url = row
    return {"api_key": _decrypt(api_key_encrypted), "base_url": base_url or NOVA_BILLING_BASE_URL}
