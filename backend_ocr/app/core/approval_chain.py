"""The configured approval chain, the statuses it generates, and who may act at each step.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

from psycopg2.extras import RealDictCursor
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn
from ..domain.tenant_defaults import DEFAULT_APPROVAL_CHAIN

_STEP_STATUS_NAMES = ["PENDING_APPROVER", "PENDING_ACCOUNTANT", "PENDING_FC"]

def _step_idx_to_status(idx: int) -> str:
    """0-indexed position in approval steps → unique pending status string."""
    return _STEP_STATUS_NAMES[idx] if idx < len(_STEP_STATUS_NAMES) else f"PENDING_STEP_{idx + 1}"

def _chain_statuses(chain: list[dict]) -> list[str]:
    """Ordered list of pending statuses — one unique string per non-locked step."""
    approval_steps = [s for s in chain if not s.get("locked")]
    return [_step_idx_to_status(i) for i in range(len(approval_steps))]

def _next_status_from_chain(chain: list[dict], current_status: str) -> str:
    """Return the next pending status after current_status, or APPROVED at the end."""
    statuses = _chain_statuses(chain)
    if current_status in statuses:
        idx = statuses.index(current_status)
        if idx + 1 < len(statuses):
            return statuses[idx + 1]
    return "APPROVED"

# Keep for legacy callers
_PENDING_STATUSES = _STEP_STATUS_NAMES[:]

_chain_cache: dict = {}          # key → (chain, expires_at)

_CHAIN_TTL = 300                 # 5 minutes — chains change rarely

def _invalidate_chain_cache(tenant_id: str, department: str | None = None):
    """Call after any chain config write so the cache is cleared immediately."""
    _chain_cache.pop(f"{tenant_id}:{department}", None)
    _chain_cache.pop(f"{tenant_id}:None", None)   # also clear default chain

def _load_chain(tenant_id: str, department: str | None = None) -> list[dict]:
    """Load approval chain from DB (cached for _CHAIN_TTL seconds)."""
    import time as _t
    _key = f"{tenant_id}:{department}"
    _cached = _chain_cache.get(_key)
    if _cached and _t.monotonic() < _cached[1]:
        return _cached[0]

    # Shared with tenant onboarding, so a newly provisioned tenant's saved chain
    # and this fallback can never drift apart.
    default = [dict(step) for step in DEFAULT_APPROVAL_CHAIN]
    def _store(result):
        import time as _t
        _chain_cache[_key] = (result, _t.monotonic() + _CHAIN_TTL)
        return result

    conn = None
    try:
        conn = get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if department:
                cur.execute(
                    "SELECT step_order, label, role, locked, assignees FROM tenant_chain_config WHERE tenant_id=%s AND department=%s AND is_snapshot=false ORDER BY step_order",
                    (tenant_id, department),
                )
                rows = cur.fetchall()
                if rows:
                    put_conn(conn)
                    return _store([dict(r) for r in rows])
            cur.execute(
                "SELECT step_order, label, role, locked, assignees FROM tenant_chain_config WHERE tenant_id=%s AND department IS NULL AND is_snapshot=false ORDER BY step_order",
                (tenant_id,),
            )
            rows = cur.fetchall()
        put_conn(conn)
        return _store([dict(r) for r in rows] if rows else default)
    except Exception:
        if conn: put_conn(conn, close=True)
        return default

def _build_status_role_map(tenant_id: str, department: str | None = None) -> dict[str, set[str]]:
    """Build {pending_status -> allowed_roles} — index-based so duplicate roles are unique."""
    chain = _load_chain(tenant_id, department)
    result: dict[str, set[str]] = {}
    idx = 0
    for step in chain:
        if step.get("locked"):
            continue
        status = _step_idx_to_status(idx)
        result[status] = {step["role"], "admin"}
        idx += 1
    return result

def _build_status_name_map(tenant_id: str, department: str | None = None) -> dict[str, list[str] | None]:
    """Build {pending_status -> allowed_names_or_None} — index-based, no overwrites."""
    chain = _load_chain(tenant_id, department)
    result: dict[str, list[str] | None] = {}
    idx = 0
    for step in chain:
        if step.get("locked"):
            continue
        status = _step_idx_to_status(idx)
        assignees = step.get("assignees")
        # Normalise stored assignee names: strip whitespace/control chars (e.g. a stray
        # '\x0b' or trailing space) so the exact-match assignee gate below can't 403 a
        # correctly-named actor. Drop entries that are empty after stripping.
        result[status] = [n.strip() for n in assignees if n and n.strip()] if assignees else None
        idx += 1
    return result

def _get_status_label(tenant_id: str, department: str | None, status: str) -> str | None:
    """Return the human-readable chain step label for a pending status, or None."""
    chain = _load_chain(tenant_id, department)
    idx = 0
    for step in chain:
        if step.get("locked"):
            continue
        if _step_idx_to_status(idx) == status:
            return step.get("label")
        idx += 1
    return None

def _get_next_role_for_status(tenant_id: str, department: str | None, status: str) -> str | None:
    """Return the role that needs to act for the given pending status, from chain config."""
    role_map = _build_status_role_map(tenant_id, department)
    roles = role_map.get(status, set()) - {"admin"}
    return next(iter(roles), None) if roles else None
