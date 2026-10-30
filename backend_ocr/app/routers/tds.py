"""TDS Settings CRUD API — /api/settings/tds/...

Endpoints (24 total):
  Sections       GET/POST/PUT/{id}/DELETE/{id}
  Categories     GET/POST/PUT/{id}/DELETE/{id}
  Mapping Rules  GET/POST/PUT/{id}/PUT/reorder/DELETE/{id}
  Overrides      GET/POST/PUT/{id}/DELETE/{id}/POST/{id}/catchup-decision
  FY State       GET
  Preferences    GET/PUT
  Preview        POST
"""
from __future__ import annotations

import calendar
import json
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

import psycopg2
import psycopg2.extras
from ..core.jurisdiction_guard import require_pack
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..config import DB_URL, TENANT_ID
from ..security.auth import require_tenant

# Indian TDS is a rule pack (core/jurisdiction.py): writes and the preview are
# refused with NOT_APPLICABLE for a tenant outside India; reads still list
# what is on file.
router = APIRouter(prefix="/api/settings/tds", tags=["TDS Settings"],
                   dependencies=[Depends(require_pack("tds_india"))])

psycopg2.extras.register_uuid()


def _conn():
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def _tid(company_id: str | None = None) -> str:
    # Tenant is derived from the authenticated JWT; client company_id is ignored.
    return require_tenant()


def _assert_section_owned_by_tenant(cur, section_id: str | None, tenant_id: str) -> None:
    # Without this, a mapping rule or vendor override can be saved pointing at another
    # tenant's dim_tds_sections row (client-supplied ID, never checked) — that tenant's
    # rate/threshold config would then silently apply to this tenant's bills.
    if not section_id:
        return
    cur.execute("SELECT 1 FROM dim_tds_sections WHERE tds_section_id = %s AND tenant_id = %s",
                (section_id, tenant_id))
    if not cur.fetchone():
        raise HTTPException(400, "TDS section does not belong to this tenant")


# ── Pydantic models ───────────────────────────────────────────────────────────

class TdsSectionCreate(BaseModel):
    return_code: str
    old_section_label: Optional[str] = None
    new_section_label: Optional[str] = None
    nature_of_payment: str
    default_rate: float
    pan_missing_rate: float = 20.0
    threshold_type: str = "NONE"
    threshold_amount: Optional[float] = None
    threshold_amount_2: Optional[float] = None
    base_calculation: str = "TAXABLE"
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None


class TdsSectionUpdate(BaseModel):
    old_section_label: Optional[str] = None
    new_section_label: Optional[str] = None
    nature_of_payment: Optional[str] = None
    default_rate: Optional[float] = None
    pan_missing_rate: Optional[float] = None
    threshold_type: Optional[str] = None
    threshold_amount: Optional[float] = None
    threshold_amount_2: Optional[float] = None
    base_calculation: Optional[str] = None
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    is_active: Optional[bool] = None


class VendorCategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None
    display_order: int = 0


class VendorCategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


class MappingRuleCreate(BaseModel):
    tds_section_id: str
    vendor_category_id: Optional[str] = None
    priority: int
    rate_override: Optional[float] = None
    description: Optional[str] = None


class MappingRuleUpdate(BaseModel):
    tds_section_id: Optional[str] = None
    vendor_category_id: Optional[str] = None
    priority: Optional[int] = None
    rate_override: Optional[float] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class ReorderItem(BaseModel):
    rule_id: str
    priority: int


class VendorOverrideCreate(BaseModel):
    supplier_id: str
    tds_section_id: Optional[str] = None
    tds_mode: str = "cumulative"
    override_rate: Optional[float] = None
    certificate_no: Optional[str] = None
    effective_from: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    mode_reason: Optional[str] = None
    company_id: Optional[str] = None
    # Legacy fields
    override_section_id: Optional[str] = None
    category_id: Optional[str] = None
    entity_type: str = "OTHERS"
    lower_cert_rate: Optional[float] = None
    lower_cert_number: Optional[str] = None
    lower_cert_valid_from: Optional[str] = None
    lower_cert_valid_to: Optional[str] = None
    pan_verified: bool = False
    notes: Optional[str] = None


class VendorOverrideUpdate(BaseModel):
    tds_mode: Optional[str] = None
    override_rate: Optional[float] = None
    certificate_no: Optional[str] = None
    effective_from: Optional[str] = None
    valid_to: Optional[str] = None
    mode_reason: Optional[str] = None
    override_section_id: Optional[str] = None
    entity_type: Optional[str] = None
    lower_cert_rate: Optional[float] = None
    lower_cert_number: Optional[str] = None
    lower_cert_valid_from: Optional[str] = None
    lower_cert_valid_to: Optional[str] = None
    pan_verified: Optional[bool] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class CatchupDecision(BaseModel):
    decision: str  # 'recover' | 'absorb'


class TdsPreferences(BaseModel):
    company_id: Optional[str] = None
    deduct_on: str = "BOOKING"
    base_default: str = "GST_EXCLUSIVE"
    auto_apply: bool = True
    fy_start_month: int = 4
    pan_missing_rate: float = 20.0
    max_catchup_pct: int = 50
    max_backdate_days: int = 90


# ── TDS Sections ──────────────────────────────────────────────────────────────

@router.get("/sections")
def list_sections():
    with _conn() as db:
        with db.cursor() as cur:
            cur.execute(
                """SELECT tds_section_id, return_code, old_section_label, new_section_label,
                          nature_of_payment, default_rate, pan_missing_rate, threshold_type,
                          threshold_amount, threshold_amount_2, base_calculation,
                          effective_from, effective_to, is_active, created_at, updated_at
                   FROM dim_tds_sections
                   WHERE tenant_id = %s
                   ORDER BY display_order, return_code""",
                (_tid(),),
            )
            return [dict(r) for r in cur.fetchall()]


@router.post("/sections", status_code=201)
def create_section(body: TdsSectionCreate):
    with _conn() as db:
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO dim_tds_sections (
                       tenant_id, return_code, old_section_label, new_section_label,
                       nature_of_payment, default_rate, pan_missing_rate,
                       threshold_type, threshold_amount, threshold_amount_2,
                       base_calculation, effective_from, effective_to
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING tds_section_id""",
                (_tid(), body.return_code, body.old_section_label, body.new_section_label,
                 body.nature_of_payment, body.default_rate, body.pan_missing_rate,
                 body.threshold_type, body.threshold_amount, body.threshold_amount_2,
                 body.base_calculation, body.effective_from, body.effective_to),
            )
            row = cur.fetchone()
            db.commit()
            return {"tds_section_id": str(row["tds_section_id"])}


@router.put("/sections/{section_id}")
def update_section(section_id: str, body: TdsSectionUpdate):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "No fields to update")
    set_clause = ", ".join(f"{k} = %s" for k in fields) + ", updated_at = now()"
    with _conn() as db:
        with db.cursor() as cur:
            cur.execute(
                f"UPDATE dim_tds_sections SET {set_clause} WHERE tds_section_id = %s AND tenant_id = %s",
                (*fields.values(), section_id, _tid()),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "Section not found")
            db.commit()
    return {"ok": True}


@router.delete("/sections/{section_id}")
def delete_section(section_id: str):
    with _conn() as db:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE dim_tds_sections SET is_active = false, updated_at = now() "
                "WHERE tds_section_id = %s AND tenant_id = %s",
                (section_id, _tid()),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "Section not found")
            db.commit()
    return {"ok": True}


# ── Vendor Categories ─────────────────────────────────────────────────────────

@router.get("/vendor-categories")
def list_vendor_categories(company_id: Optional[str] = None):
    tid = company_id or _tid()
    with _conn() as db:
        with db.cursor() as cur:
            cur.execute(
                """SELECT vc.category_id, vc.name, vc.description,
                          vc.display_order, vc.is_active, vc.created_at,
                          COUNT(s.supplier_id) AS vendor_count,
                          best.tds_rate, best.section_code, best.section_label
                   FROM dim_vendor_categories vc
                   LEFT JOIN dim_suppliers s ON s.vendor_category_id = vc.category_id
                   LEFT JOIN LATERAL (
                       SELECT COALESCE(mr.rate_override, sec.default_rate) AS tds_rate,
                              sec.return_code AS section_code,
                              sec.old_section_label AS section_label
                       FROM dim_tds_mapping_rules mr
                       JOIN dim_tds_sections sec ON sec.tds_section_id = mr.tds_section_id
                       WHERE mr.vendor_category_id = vc.category_id
                         AND mr.tenant_id = %s AND mr.is_active = true
                       ORDER BY mr.priority LIMIT 1
                   ) best ON true
                   WHERE vc.tenant_id = %s
                   GROUP BY vc.category_id, best.tds_rate, best.section_code, best.section_label
                   ORDER BY vc.display_order, vc.name""",
                (tid, tid),
            )
            return [dict(r) for r in cur.fetchall()]


@router.post("/vendor-categories", status_code=201)
def create_vendor_category(body: VendorCategoryCreate):
    with _conn() as db:
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO dim_vendor_categories (tenant_id, name, description, display_order)
                   VALUES (%s, %s, %s, %s) RETURNING category_id""",
                (_tid(), body.name, body.description, body.display_order),
            )
            row = cur.fetchone()
            db.commit()
            return {"category_id": str(row["category_id"])}


@router.put("/vendor-categories/{category_id}")
def update_vendor_category(category_id: str, body: VendorCategoryUpdate):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "No fields to update")
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    with _conn() as db:
        with db.cursor() as cur:
            cur.execute(
                f"UPDATE dim_vendor_categories SET {set_clause} WHERE category_id = %s AND tenant_id = %s",
                (*fields.values(), category_id, _tid()),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "Category not found")
            db.commit()
    return {"ok": True}


@router.delete("/vendor-categories/{category_id}")
def delete_vendor_category(category_id: str):
    with _conn() as db:
        with db.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM dim_suppliers WHERE vendor_category_id = %s", (category_id,))
            if cur.fetchone()["cnt"] > 0:
                raise HTTPException(409, "Cannot delete - vendors are assigned to this category")
            cur.execute(
                "UPDATE dim_vendor_categories SET is_active = false WHERE category_id = %s AND tenant_id = %s",
                (category_id, _tid()),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "Category not found")
            db.commit()
    return {"ok": True}


# ── Mapping Rules ─────────────────────────────────────────────────────────────

@router.get("/mapping-rules")
def list_mapping_rules():
    with _conn() as db:
        with db.cursor() as cur:
            cur.execute(
                """SELECT mr.rule_id, mr.priority, mr.rate_override, mr.description,
                          mr.is_active, mr.created_at,
                          mr.tds_section_id, sec.return_code AS section_return_code,
                          sec.old_section_label, sec.nature_of_payment AS section_nature,
                          mr.vendor_category_id, vc.name AS category_name
                   FROM dim_tds_mapping_rules mr
                   LEFT JOIN dim_tds_sections sec ON sec.tds_section_id = mr.tds_section_id
                   LEFT JOIN dim_vendor_categories vc ON vc.category_id = mr.vendor_category_id
                   WHERE mr.tenant_id = %s ORDER BY mr.priority""",
                (_tid(),),
            )
            return [dict(r) for r in cur.fetchall()]


@router.post("/mapping-rules", status_code=201)
def create_mapping_rule(body: MappingRuleCreate):
    tid = _tid()
    with _conn() as db:
        with db.cursor() as cur:
            _assert_section_owned_by_tenant(cur, body.tds_section_id, tid)
            cur.execute(
                """INSERT INTO dim_tds_mapping_rules (
                       tenant_id, tds_section_id, vendor_category_id, priority, rate_override, description
                   ) VALUES (%s, %s, %s, %s, %s, %s) RETURNING rule_id""",
                (tid, body.tds_section_id, body.vendor_category_id,
                 body.priority, body.rate_override, body.description),
            )
            row = cur.fetchone()
            db.commit()
            return {"rule_id": str(row["rule_id"])}


@router.put("/mapping-rules/reorder")
def reorder_mapping_rules(items: list[ReorderItem]):
    with _conn() as db:
        with db.cursor() as cur:
            for item in items:
                cur.execute(
                    "UPDATE dim_tds_mapping_rules SET priority = %s WHERE rule_id = %s AND tenant_id = %s",
                    (item.priority, item.rule_id, _tid()),
                )
            db.commit()
    return {"ok": True}


@router.put("/mapping-rules/{rule_id}")
def update_mapping_rule(rule_id: str, body: MappingRuleUpdate):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "No fields to update")
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    tid = _tid()
    with _conn() as db:
        with db.cursor() as cur:
            if "tds_section_id" in fields:
                _assert_section_owned_by_tenant(cur, fields["tds_section_id"], tid)
            cur.execute(
                f"UPDATE dim_tds_mapping_rules SET {set_clause} WHERE rule_id = %s AND tenant_id = %s",
                (*fields.values(), rule_id, tid),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "Rule not found")
            db.commit()
    return {"ok": True}


@router.delete("/mapping-rules/{rule_id}")
def delete_mapping_rule(rule_id: str):
    with _conn() as db:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE dim_tds_mapping_rules SET is_active = false WHERE rule_id = %s AND tenant_id = %s",
                (rule_id, _tid()),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "Rule not found")
            db.commit()
    return {"ok": True}


# ── Vendor TDS Overrides ──────────────────────────────────────────────────────

@router.get("/vendor-overrides")
def list_vendor_overrides(supplier_id: Optional[str] = None, company_id: Optional[str] = None):
    # Resolve tenant from supplier if needed
    if not company_id and supplier_id:
        with _conn() as db2:
            with db2.cursor() as cur2:
                cur2.execute("SELECT tenant_id FROM dim_suppliers WHERE supplier_id = %s", (supplier_id,))
                row2 = cur2.fetchone()
                if row2:
                    company_id = str(row2["tenant_id"])
    with _conn() as db:
        with db.cursor() as cur:
            base_sql = """
                SELECT vo.override_id, vo.supplier_id, vo.entity_type,
                       vo.override_section_id, vo.tds_section_id,
                       vo.tds_mode, vo.override_rate,
                       vo.certificate_no, vo.effective_from,
                       vo.lower_cert_rate, vo.lower_cert_number,
                       vo.lower_cert_valid_from, vo.lower_cert_valid_to,
                       vo.pan_verified, vo.notes, vo.mode_reason,
                       vo.mode_set_on, vo.is_active, vo.created_at, vo.updated_at,
                       s.name AS supplier_name,
                       sec.return_code AS section_return_code,
                       sec.old_section_label
                FROM dim_vendor_tds_overrides vo
                LEFT JOIN dim_suppliers s ON s.supplier_id = vo.supplier_id
                LEFT JOIN dim_tds_sections sec ON sec.tds_section_id = COALESCE(vo.tds_section_id, vo.override_section_id)
                WHERE vo.tenant_id = %s
            """
            params: list[Any] = [_tid(company_id)]
            if supplier_id:
                base_sql += " AND vo.supplier_id = %s"
                params.append(supplier_id)
            base_sql += " ORDER BY vo.created_at DESC"
            cur.execute(base_sql, params)
            return [dict(r) for r in cur.fetchall()]


@router.post("/vendor-overrides", status_code=201)
def create_vendor_override(body: VendorOverrideCreate):
    # Resolve tenant from supplier if company_id not provided
    resolved_tid = body.company_id
    if not resolved_tid and body.supplier_id:
        with _conn() as db2:
            with db2.cursor() as cur2:
                cur2.execute("SELECT tenant_id FROM dim_suppliers WHERE supplier_id = %s", (body.supplier_id,))
                row2 = cur2.fetchone()
                if row2:
                    resolved_tid = str(row2["tenant_id"])
    tid = _tid(resolved_tid)
    eff_from = body.effective_from or body.valid_from or str(date.today())
    sect_id = body.tds_section_id or body.override_section_id
    # calculate_tds() (utils/tds_engine.py) only ever checks lower_cert_valid_to to decide
    # whether an override has expired, so the simple "valid to" date from the override form
    # has to be persisted there too — otherwise it's silently accepted but never enforced.
    valid_to = body.lower_cert_valid_to or body.valid_to

    with _conn() as db:
        with db.cursor() as cur:
            _assert_section_owned_by_tenant(cur, sect_id, tid)
            cur.execute(
                """INSERT INTO dim_vendor_tds_overrides (
                       tenant_id, supplier_id, tds_section_id, override_section_id,
                       category_id, entity_type, tds_mode,
                       override_rate, certificate_no, effective_from,
                       lower_cert_rate, lower_cert_number,
                       lower_cert_valid_from, lower_cert_valid_to,
                       pan_verified, notes, mode_reason, mode_set_on, is_active
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), true)
                   ON CONFLICT (tenant_id, supplier_id) DO UPDATE SET
                       tds_section_id = EXCLUDED.tds_section_id,
                       override_section_id = EXCLUDED.override_section_id,
                       tds_mode = EXCLUDED.tds_mode,
                       override_rate = EXCLUDED.override_rate,
                       certificate_no = EXCLUDED.certificate_no,
                       effective_from = EXCLUDED.effective_from,
                       lower_cert_rate = EXCLUDED.lower_cert_rate,
                       lower_cert_number = EXCLUDED.lower_cert_number,
                       lower_cert_valid_from = EXCLUDED.lower_cert_valid_from,
                       lower_cert_valid_to = EXCLUDED.lower_cert_valid_to,
                       notes = EXCLUDED.notes,
                       mode_reason = EXCLUDED.mode_reason,
                       mode_set_on = now(),
                       is_active = true,
                       updated_at = now()
                   RETURNING override_id""",
                (tid, body.supplier_id, sect_id, body.override_section_id,
                 body.category_id, body.entity_type, body.tds_mode,
                 body.override_rate, body.certificate_no, eff_from,
                 body.lower_cert_rate, body.lower_cert_number,
                 body.lower_cert_valid_from, valid_to,
                 body.pan_verified, body.notes or body.mode_reason, body.mode_reason),
            )
            row = cur.fetchone()
            override_id = str(row["override_id"])

            # Compute catch-up if mode is always_deduct or per_bill_threshold
            catchup_summary = None
            if body.tds_mode in ("always_deduct", "per_bill_threshold") and sect_id:
                catchup_summary = _compute_catchup_sync(
                    cur, tid, body.supplier_id, sect_id,
                    body.tds_mode, eff_from,
                )

            # Reset all affected bills to PENDING so pipeline recalculates with new override.
            # calculate_tds() always adds a bill's amount on top of the stored FY cumulative
            # total rather than replacing it, so every NA/CALCULATED bill for this vendor is
            # about to be re-summed from scratch — wipe its FY state first or reprocessing
            # will double-count them on top of what's already there.
            cur.execute(
                """SELECT 1 FROM stg_bills WHERE tenant_id = %s AND supplier_id = %s
                       AND tds_status IN ('NA', 'CALCULATED') LIMIT 1""",
                (tid, body.supplier_id),
            )
            if cur.fetchone():
                cur.execute(
                    "DELETE FROM vendor_tds_fy_state WHERE tenant_id = %s AND supplier_id = %s",
                    (tid, body.supplier_id),
                )
            cur.execute(
                """UPDATE stg_bills SET tds_status = 'PENDING'
                   WHERE tenant_id = %s AND supplier_id = %s
                     AND tds_status IN ('NA', 'CALCULATED')""",
                (tid, body.supplier_id),
            )
            bills_reset = cur.rowcount
            if bills_reset > 0:
                cur.execute(
                    """UPDATE fact_bills SET tds_status = 'PENDING'
                       WHERE tenant_id = %s AND supplier_id = %s
                         AND tds_status IN ('NA', 'CALCULATED')""",
                    (tid, body.supplier_id),
                )

            db.commit()
            result = {"override_id": override_id, "bills_reset": bills_reset}
            if catchup_summary:
                result["catchup"] = catchup_summary
            return result


@router.put("/vendor-overrides/{override_id}")
def update_vendor_override(override_id: str, body: VendorOverrideUpdate):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "No fields to update")
    # "valid_to" isn't a real column — calculate_tds() only checks lower_cert_valid_to
    # to decide whether an override has expired, so route it there (see create_vendor_override).
    if "valid_to" in fields:
        fields["lower_cert_valid_to"] = fields.pop("valid_to")

    # Log mode changes
    old_mode = None
    if "tds_mode" in fields:
        with _conn() as db:
            with db.cursor() as cur:
                cur.execute(
                    "SELECT tds_mode FROM dim_vendor_tds_overrides WHERE override_id = %s",
                    (override_id,),
                )
                row = cur.fetchone()
                if row:
                    old_mode = row["tds_mode"]

    if "tds_mode" in fields:
        fields["mode_set_on"] = datetime.utcnow()

    set_clause = ", ".join(f"{k} = %s" for k in fields) + ", updated_at = now()"
    tid = _tid()
    with _conn() as db:
        with db.cursor() as cur:
            if "override_section_id" in fields:
                _assert_section_owned_by_tenant(cur, fields["override_section_id"], tid)
            cur.execute(
                f"UPDATE dim_vendor_tds_overrides SET {set_clause} "
                "WHERE override_id = %s AND tenant_id = %s",
                (*fields.values(), override_id, tid),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "Override not found")

            if old_mode and old_mode != fields.get("tds_mode"):
                cur.execute(
                    """INSERT INTO vendor_tds_config_log
                           (tenant_id, override_id, field_changed, old_value, new_value, reason)
                       VALUES (%s, %s, 'tds_mode', %s, %s, %s)""",
                    (_tid(), override_id, old_mode, fields["tds_mode"],
                     fields.get("mode_reason", "")),
                )

            db.commit()
    return {"ok": True}


@router.delete("/vendor-overrides/{override_id}")
def delete_vendor_override(override_id: str):
    with _conn() as db:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE dim_vendor_tds_overrides SET is_active = false, updated_at = now() "
                "WHERE override_id = %s AND tenant_id = %s",
                (override_id, _tid()),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "Override not found")
            db.commit()
    return {"ok": True}


@router.post("/vendor-overrides/{override_id}/catchup-decision")
def catchup_decision(override_id: str, body: CatchupDecision):
    if body.decision not in ("recover", "absorb"):
        raise HTTPException(400, "Decision must be 'recover' or 'absorb'")

    with _conn() as db:
        with db.cursor() as cur:
            cur.execute(
                "SELECT supplier_id, tds_section_id FROM dim_vendor_tds_overrides WHERE override_id = %s",
                (override_id,),
            )
            ov = cur.fetchone()
            if not ov:
                raise HTTPException(404, "Override not found")

            fy = _current_fy()
            cur.execute(
                """UPDATE vendor_tds_fy_state SET
                       catchup_decision = %s,
                       catchup_decided_on = now(),
                       updated_at = now()
                   WHERE tenant_id = %s AND supplier_id = %s
                     AND tds_section_id = %s::uuid AND fy = %s
                     AND catchup_decision = 'pending'""",
                (body.decision, _tid(), ov["supplier_id"],
                 str(ov["tds_section_id"]), fy),
            )

            cur.execute(
                """INSERT INTO vendor_tds_config_log
                       (tenant_id, override_id, field_changed, new_value, reason)
                   VALUES (%s, %s, 'catchup_decision', %s, %s)""",
                (_tid(), override_id, body.decision,
                 f"Catch-up {body.decision} for FY {fy}"),
            )

            db.commit()
    return {"ok": True, "decision": body.decision}


# ── FY State (read-only) ─────────────────────────────────────────────────────

@router.get("/fy-state")
def get_fy_state(supplier_id: Optional[str] = None, fy: Optional[str] = None, company_id: Optional[str] = None):
    with _conn() as db:
        with db.cursor() as cur:
            sql = """
                SELECT fs.state_id, fs.supplier_id, fs.tds_section_id, fs.fy,
                       fs.cumulative_billed, fs.cumulative_tds_deducted,
                       fs.threshold_crossed, fs.catchup_pending, fs.catchup_recovered,
                       fs.catchup_decision, fs.updated_at,
                       s.name AS supplier_name,
                       sec.return_code, sec.old_section_label, sec.base_calculation, sec.default_rate
                FROM vendor_tds_fy_state fs
                LEFT JOIN dim_suppliers s ON s.supplier_id = fs.supplier_id
                LEFT JOIN dim_tds_sections sec ON sec.tds_section_id = fs.tds_section_id
                WHERE fs.tenant_id = %s
            """
            params: list[Any] = [_tid(company_id)]
            if supplier_id:
                sql += " AND fs.supplier_id = %s"
                params.append(supplier_id)
            if fy:
                sql += " AND fs.fy = %s"
                params.append(fy)
            sql += " ORDER BY fs.fy DESC, s.name"
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


# ── Preferences ──────────────────────────────────────────────────────────────

_TDS_PREF_DEFAULTS: dict = {
    "deduct_on": "BOOKING",
    "base_default": "GST_EXCLUSIVE",
    "auto_apply": True,
    "fy_start_month": 4,
    "pan_missing_rate": 20.0,
    "max_catchup_pct": 50,
    "max_backdate_days": 90,
}


@router.get("/preferences")
def get_preferences(company_id: Optional[str] = Query(default=None)):
    tid = _tid(company_id)
    with _conn() as db:
        with db.cursor() as cur:
            cur.execute("SELECT settings_jsonb FROM dim_tenants WHERE tenant_id = %s", (tid,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Tenant not found")
            settings = row["settings_jsonb"] or {}
            return {**_TDS_PREF_DEFAULTS, **(settings.get("tds") or {})}


@router.put("/preferences")
def update_preferences(body: TdsPreferences):
    tid = _tid(body.company_id)
    prefs = body.model_dump(exclude={"company_id"})
    with _conn() as db:
        with db.cursor() as cur:
            cur.execute(
                """UPDATE dim_tenants SET settings_jsonb = jsonb_set(
                       COALESCE(settings_jsonb, '{}'), '{tds}', %s::jsonb
                   ) WHERE tenant_id = %s""",
                (json.dumps(prefs), tid),
            )
            db.commit()
    return {"ok": True, "preferences": prefs}


# ── TDS Preview ──────────────────────────────────────────────────────────────

class TdsPreviewRequest(BaseModel):
    company_id: Optional[str] = None
    vendor_name: Optional[str] = None
    vendor_gstin: Optional[str] = None
    vendor_pan: Optional[str] = None
    subtotal: Optional[float] = None
    tax_amount: Optional[float] = None
    total_amount: Optional[float] = None
    bill_date: Optional[str] = None


def _fy_bounds(d: date) -> tuple[date, date]:
    year = d.year if d.month >= 4 else d.year - 1
    return date(year, 4, 1), date(year + 1, 3, 31)


def _month_bounds(d: date) -> tuple[date, date]:
    last = calendar.monthrange(d.year, d.month)[1]
    return d.replace(day=1), d.replace(day=last)


def _gstn_clean(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    s = re.sub(r"\s+", "", str(v)).upper()
    return s if len(s) == 15 else None


def _current_fy() -> str:
    today = date.today()
    year = today.year if today.month >= 4 else today.year - 1
    return f"{year}-{str(year + 1)[-2:]}"


def _cumulative(cur, tenant_id: str, supplier_id: str, section_id: str,
                start: date, end: date) -> Decimal:
    cur.execute(
        """SELECT COALESCE(SUM(subtotal), 0) AS s FROM fact_bills
           WHERE tenant_id = %s AND supplier_id = %s
             AND tds_section_id = %s::uuid AND bill_date BETWEEN %s AND %s""",
        (tenant_id, supplier_id, section_id, start, end),
    )
    row = cur.fetchone()
    return Decimal(str((row["s"] if row else 0) or 0))


def _compute_catchup_sync(cur, tenant_id, supplier_id, section_id, new_mode, effective_from_str):
    """Synchronous catch-up computation for the API layer."""
    today = date.today()
    fy_year = today.year if today.month >= 4 else today.year - 1
    fy_start = date(fy_year, 4, 1)
    fy = f"{fy_year}-{str(fy_year + 1)[-2:]}"

    try:
        eff_from = datetime.strptime(str(effective_from_str), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        eff_from = today

    cur.execute(
        """SELECT tds_base_amount, tds_amount FROM stg_bills
           WHERE tenant_id = %s AND supplier_id = %s
             AND tds_section_id = %s::uuid
             AND bill_date >= %s AND bill_date < %s
             AND tds_status != 'REVIEW_REQUIRED'""",
        (tenant_id, supplier_id, section_id, fy_start, eff_from),
    )
    prior_bills = cur.fetchall()
    if not prior_bills:
        return None

    cur.execute(
        "SELECT default_rate, threshold_amount FROM dim_tds_sections WHERE tds_section_id = %s::uuid",
        (section_id,),
    )
    sec = cur.fetchone()
    if not sec:
        return None

    rate = Decimal(str(sec["default_rate"]))
    threshold = Decimal(str(sec["threshold_amount"] or 0))

    theoretical_tds = Decimal(0)
    already_deducted = Decimal(0)
    total_amount = Decimal(0)

    for bill in prior_bills:
        base = Decimal(str(bill["tds_base_amount"] or 0))
        total_amount += base
        if new_mode == "always_deduct":
            theoretical_tds += (base * rate / 100).quantize(Decimal("1"))
        elif new_mode == "per_bill_threshold" and base >= threshold:
            theoretical_tds += (base * rate / 100).quantize(Decimal("1"))
        already_deducted += Decimal(str(bill["tds_amount"] or 0))

    catchup = max(Decimal(0), theoretical_tds - already_deducted)
    if catchup <= 0:
        return None

    # Upsert FY state with catch-up
    cur.execute(
        """INSERT INTO vendor_tds_fy_state
               (tenant_id, supplier_id, tds_section_id, fy,
                catchup_pending, catchup_decision)
           VALUES (%s, %s, %s::uuid, %s, %s, 'pending')
           ON CONFLICT (tenant_id, supplier_id, tds_section_id, fy) DO UPDATE SET
               catchup_pending = %s, catchup_decision = 'pending', updated_at = now()""",
        (tenant_id, supplier_id, section_id, fy, float(catchup), float(catchup)),
    )

    return {
        "catchup_pending": float(catchup),
        "affected_bill_count": len(prior_bills),
        "bills_total_amount": float(total_amount),
        "fy": fy,
        "decision_required": True,
    }


@router.post("/preview")
def preview_tds(body: TdsPreviewRequest):
    tenant = body.company_id or _tid()
    total_amt = Decimal(str(body.total_amount or 0))
    subtotal  = Decimal(str(body.subtotal or 0))
    tax_amt   = Decimal(str(body.tax_amount or 0))
    if subtotal == 0 and total_amt > 0 and tax_amt > 0:
        subtotal = total_amt - tax_amt

    try:
        bill_dt = datetime.strptime(body.bill_date, "%Y-%m-%d").date() if body.bill_date else date.today()
    except (ValueError, TypeError):
        bill_dt = date.today()

    base_resp: dict[str, Any] = {
        "status": "PENDING", "section_code": None, "return_code": None,
        "rate": None, "base_amount": float(subtotal or total_amt),
        "tax_amount": float(tax_amt), "total_amount": float(total_amt),
        "tds_amount": 0.0, "net_payable": float(total_amt),
        "base_calculation": "GST_EXCLUSIVE", "pan_status": None, "reason": "",
    }

    gstn = _gstn_clean(body.vendor_gstin or body.vendor_pan)
    name = (body.vendor_name or "").strip()

    with _conn() as db:
        with db.cursor() as cur:
            sup = None
            if gstn:
                cur.execute(
                    """SELECT s.supplier_id, s.vendor_category_id, s.pan, s.status
                       FROM dim_suppliers s
                       WHERE s.tenant_id = %s AND s.gstn = %s::char(15) AND s.is_active = true
                       UNION
                       SELECT s.supplier_id, s.vendor_category_id, s.pan, s.status
                       FROM supplier_gstns sg
                       JOIN dim_suppliers s ON s.supplier_id = sg.supplier_id
                       WHERE sg.tenant_id = %s AND sg.gstn = %s::char(15)
                       LIMIT 1""",
                    (tenant, gstn, tenant, gstn),
                )
                sup = cur.fetchone()
            if not sup and name:
                cur.execute(
                    """SELECT supplier_id, vendor_category_id, pan, status
                       FROM dim_suppliers
                       WHERE tenant_id = %s AND is_active = true AND lower(name) = lower(%s)
                       LIMIT 1""",
                    (tenant, name),
                )
                sup = cur.fetchone()

            if not sup:
                base_resp["reason"] = "Vendor not yet recognized - TDS will be computed after ingestion."
                return base_resp
            if not sup["vendor_category_id"]:
                base_resp["reason"] = "Vendor has no TDS category assigned yet."
                return base_resp

            # Check for vendor override
            sid = str(sup["supplier_id"])
            cur.execute(
                """SELECT tds_mode, tds_section_id, override_rate
                   FROM dim_vendor_tds_overrides
                   WHERE tenant_id = %s AND supplier_id = %s AND is_active = true
                     AND effective_from <= %s
                   ORDER BY created_at DESC LIMIT 1""",
                (tenant, sid, bill_dt),
            )
            vendor_override = cur.fetchone()
            tds_mode = vendor_override["tds_mode"] if vendor_override else "cumulative"

            cur.execute(
                """SELECT mr.tds_section_id, mr.rate_override,
                          sec.return_code, sec.old_section_label, sec.threshold_type,
                          sec.threshold_amount, sec.threshold_amount_2,
                          sec.default_rate, sec.pan_missing_rate, sec.base_calculation
                   FROM dim_tds_mapping_rules mr
                   JOIN dim_tds_sections sec ON sec.tds_section_id = mr.tds_section_id AND sec.is_active = true
                   WHERE mr.tenant_id = %s AND mr.is_active = true
                     AND mr.vendor_category_id = %s
                   ORDER BY mr.priority ASC LIMIT 1""",
                (tenant, sup["vendor_category_id"]),
            )
            rule = cur.fetchone()
            if not rule:
                base_resp["status"] = "NA"
                base_resp["reason"] = "No TDS mapping rule for this vendor category."
                return base_resp

            section_id   = str(rule["tds_section_id"])
            return_code  = rule["return_code"]
            section_code = rule["old_section_label"] or return_code
            ttype        = rule["threshold_type"] or "NONE"
            t1 = Decimal(str(rule["threshold_amount"])) if rule["threshold_amount"] else Decimal(0)
            t2 = Decimal(str(rule["threshold_amount_2"])) if rule["threshold_amount_2"] else Decimal(0)
            rate_override = Decimal(str(rule["rate_override"])) if rule["rate_override"] else None
            if vendor_override and vendor_override["override_rate"] is not None:
                rate_override = Decimal(str(vendor_override["override_rate"]))
            base_calc = rule["base_calculation"] or "TAXABLE"

            base_resp.update({"section_code": section_code, "return_code": return_code,
                              "base_calculation": base_calc})

            base_amount = subtotal if (base_calc in ("GST_EXCLUSIVE", "TAXABLE") and tax_amt > 0) else total_amt
            base_resp["base_amount"] = float(base_amount)

            tds_applicable = True
            exempt_reason = ""

            if tds_mode == "always_deduct":
                pass
            elif tds_mode == "per_bill_threshold":
                if base_amount < t1:
                    tds_applicable = False
                    exempt_reason = f"Per-bill: amount below threshold {t1:,.0f}"
            else:
                if ttype == "AGGREGATE_FY":
                    fs, fe = _fy_bounds(bill_dt)
                    cum = _cumulative(cur, tenant, sid, section_id, fs, fe)
                    if cum < t1 and cum + base_amount < t1:
                        tds_applicable = False
                        exempt_reason = f"Below FY threshold {t1:,.0f} (cumulative {cum:,.0f})"
                elif ttype == "PER_TXN":
                    if base_amount < t1:
                        tds_applicable = False
                        exempt_reason = f"Below per-txn threshold {t1:,.0f}"
                elif ttype == "PER_MONTH":
                    ms, me = _month_bounds(bill_dt)
                    mon = _cumulative(cur, tenant, sid, section_id, ms, me)
                    if mon < t1 and mon + base_amount < t1:
                        tds_applicable = False
                        exempt_reason = f"Below monthly threshold {t1:,.0f} (month {mon:,.0f})"
                elif ttype == "EXCESS_OF":
                    fs, fe = _fy_bounds(bill_dt)
                    cum = _cumulative(cur, tenant, sid, section_id, fs, fe)
                    prev_excess = max(Decimal(0), cum - t1)
                    new_excess  = max(Decimal(0), cum + base_amount - t1)
                    taxable_excess = new_excess - prev_excess
                    if taxable_excess <= 0:
                        tds_applicable = False
                        exempt_reason = f"Under excess-of threshold {t1:,.0f}"
                    else:
                        base_amount = taxable_excess
                        base_resp["base_amount"] = float(base_amount)
                elif ttype == "DUAL":
                    if base_amount < t1:
                        fs, fe = _fy_bounds(bill_dt)
                        cum = _cumulative(cur, tenant, sid, section_id, fs, fe)
                        if not (cum >= t2 or cum + base_amount >= t2):
                            tds_applicable = False
                            exempt_reason = f"DUAL: txn {base_amount:,.0f}<{t1:,.0f} AND FY {cum:,.0f}<{t2:,.0f}"

            if not tds_applicable:
                base_resp["status"] = "NA"
                base_resp["reason"] = exempt_reason
                return base_resp

            pan = (sup["pan"] or "").strip().upper()
            is_pan_available = bool(pan)
            is_pan_operative = is_pan_available and (sup["status"] == "VERIFIED")
            if not is_pan_available or not is_pan_operative:
                rate = Decimal(str(rule["pan_missing_rate"] or 20))
                base_resp["pan_status"] = "MISSING_OR_INOPERATIVE"
            elif rate_override is not None:
                rate = rate_override
            else:
                rate = Decimal(str(rule["default_rate"]))

            tds_amount = (base_amount * rate / 100).quantize(Decimal("1"))
            net_payable = total_amt - tds_amount

            base_resp.update({
                "status": "CALCULATED", "rate": float(rate),
                "base_amount": float(base_amount), "tds_amount": float(tds_amount),
                "net_payable": float(net_payable), "tds_mode": tds_mode,
                "reason": f"rate={rate}% base={base_amount:,.0f} mode={tds_mode}",
            })
            return base_resp
