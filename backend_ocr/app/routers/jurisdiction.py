"""Tenant jurisdiction settings: which country's rules apply (core/jurisdiction.py).

GET  /api/tenant/jurisdiction   the resolved profile, plus what is overridden
PUT  /api/tenant/jurisdiction   change overrides (admin only)

The profile itself (india / international), the country and the books currency
are set at onboarding and are not edited here: changing them changes which law
applies to every past bill. What a tenant admin can tune is the rule packs —
e.g. an international tenant with Indian vendors switching GST and PAN checks
back on — and presentation: locale, fiscal-year start, tax labels.

Overrides live in dim_tenants.settings_jsonb -> 'jurisdiction'. Sending a pack
as null removes its override, returning it to the profile default.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel, Field

from ..config import get_conn, put_conn
from ..core.jurisdiction import PACKS, build, invalidate
from ..security.auth import current_username, require_role, require_tenant

router = APIRouter(prefix="/api", tags=["Jurisdiction"])


class JurisdictionUpdate(BaseModel):
    packs: Optional[dict[str, Optional[bool]]] = None
    locale: Optional[str] = Field(default=None, max_length=16)
    fiscal_year_start_month: Optional[int] = Field(default=None, ge=1, le=12)
    tax_id_label: Optional[str] = Field(default=None, max_length=40)
    indirect_tax_label: Optional[str] = Field(default=None, max_length=40)


def _view(row: dict) -> dict:
    settings = row.get("settings_jsonb") or {}
    overrides = settings.get("jurisdiction") or {}
    default = build({**row, "settings_jsonb": {}})
    return {
        "jurisdiction": build(row).to_dict(),
        "defaults": default.to_dict(),
        "overrides": overrides,
        "editable_packs": list(PACKS),
    }


def _load(cur, tenant_id: str) -> dict:
    cur.execute("SELECT * FROM dim_tenants WHERE tenant_id = %s", (tenant_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return dict(row)


@router.get("/tenant/jurisdiction")
def get_tenant_jurisdiction():
    tenant_id = require_tenant()
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            return _view(_load(cur, tenant_id))
    finally:
        put_conn(conn)


@router.put("/tenant/jurisdiction")
def put_tenant_jurisdiction(body: JurisdictionUpdate):
    tenant_id = require_tenant()
    require_role("admin")
    unknown = sorted(set((body.packs or {}).keys()) - set(PACKS))
    if unknown:
        raise HTTPException(status_code=400, detail={"error": "UNKNOWN_PACK", "packs": unknown})

    conn = get_conn()
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            row = _load(cur, tenant_id)
            settings = dict(row.get("settings_jsonb") or {})
            old = dict(settings.get("jurisdiction") or {})
            new = json.loads(json.dumps(old))
            if body.packs is not None:
                packs = dict(new.get("packs") or {})
                for pack, value in body.packs.items():
                    if value is None:
                        packs.pop(pack, None)
                    else:
                        packs[pack] = bool(value)
                new["packs"] = packs
            for key in ("locale", "fiscal_year_start_month", "tax_id_label", "indirect_tax_label"):
                value = getattr(body, key)
                if value is not None:
                    if value == "":
                        new.pop(key, None)
                    else:
                        new[key] = value
            settings["jurisdiction"] = new
            cur.execute("UPDATE dim_tenants SET settings_jsonb = %s::jsonb WHERE tenant_id = %s",
                        (json.dumps(settings), tenant_id))
            cur.execute(
                """INSERT INTO wf_audit_log (tenant_id, table_name, entity_id, action, old_values_jsonb, new_values_jsonb)
                   VALUES (%s, 'dim_tenants', %s, 'UPDATE', %s, %s)""",
                (tenant_id, tenant_id, json.dumps({"jurisdiction": old}),
                 json.dumps({"jurisdiction": new, "actor": current_username()})),
            )
            conn.commit()
            row["settings_jsonb"] = settings
        invalidate(tenant_id)
        return _view(row)
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
