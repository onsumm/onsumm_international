"""Verification of the hash-chained audit log.

The chain itself is written by the `wf_audit_hash_chain` trigger
(app/audit_migrations.py), so every writer of `wf_audit_log` is covered. This
module only *checks* it: for one tenant, walk the rows in `seq` order and
confirm each one links to the one before and still hashes to what was stored.

The text that was hashed comes from the same SQL function the trigger used,
`wf_audit_canonical(...)`, so this check and the trigger cannot disagree about
serialisation. An edited field, a deleted row or a re-ordered row all show up
as the first broken `seq`.
"""
from __future__ import annotations

import hashlib
from typing import Optional

ZERO_HASH = bytes(32)

CANONICAL_SQL = """wf_audit_canonical(tenant_id, seq, table_name, entity_id, action::text, user_id,
                       correlation_id, old_values_jsonb, new_values_jsonb, created_at)"""


def row_hash(prev_hash: bytes, canonical: str) -> bytes:
    """sha256(prev_hash || utf8(canonical)) — the trigger's formula, in Python."""
    return hashlib.sha256(bytes(prev_hash) + canonical.encode("utf-8")).digest()


def canonical_text(cur, log_id: str) -> Optional[str]:
    """The exact text that was hashed for one stored row."""
    cur.execute("SELECT wf_audit_canonical_text(%s::uuid) AS canonical", (log_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return row["canonical"] if isinstance(row, dict) else row[0]


def verify_rows(rows) -> dict:
    """Pure check over (seq, prev_hash, row_hash, canonical) rows in seq order."""
    expected_prev = ZERO_HASH
    expected_seq = 1
    checked = 0
    for seq, prev, stored, canonical in rows:
        prev = bytes(prev) if prev is not None else None
        stored = bytes(stored) if stored is not None else None
        if seq != expected_seq or prev != expected_prev or stored is None \
                or row_hash(prev, canonical) != stored:
            # A gap or re-order breaks the chain at the seq we expected next.
            return {"ok": False, "checked": checked, "first_broken_seq": expected_seq}
        checked += 1
        expected_prev = stored
        expected_seq += 1
    return {"ok": True, "checked": checked, "first_broken_seq": None}


def verify_chain(conn, tenant_id: str) -> dict:
    """{ok, checked, first_broken_seq, unhashed} for one tenant's audit chain."""
    with conn.cursor(name=f"verify_chain_{tenant_id.replace('-', '')[:16]}") as cur:
        cur.itersize = 2000
        cur.execute(
            f"""SELECT seq, prev_hash, row_hash, {CANONICAL_SQL} AS canonical
                  FROM wf_audit_log
                 WHERE tenant_id = %s AND seq IS NOT NULL
                 ORDER BY seq""",
            (tenant_id,),
        )
        result = verify_rows((r[0], r[1], r[2], r[3]) for r in cur)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM wf_audit_log WHERE tenant_id = %s AND row_hash IS NULL", (tenant_id,))
        result["unhashed"] = int(cur.fetchone()[0])
    return result
