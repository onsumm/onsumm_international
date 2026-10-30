"""Folding one vendor record into another, in one place.

Extracted from routers/vendors.py:merge_vendor so the Review Hub can offer the
same operation without owning a second copy of it. The rule the hub works to is
that a fix it performs must call the logic that already owns the operation —
a triage queue must never become a slightly-different second path to a write.

The guardrails stay with the *callers*, deliberately, because they differ:

  * `merge_vendor` is the PAN-duplicate flow, so it refuses unless both records
    derive the same PAN.
  * The Review Hub's misread-GSTIN fix cannot use that test — the PANs differ
    precisely *because* the GSTIN was misread, which is the whole finding. It
    substitutes a narrower one: identical name, same state code, one record
    verified and the other not, and only a handful of characters between them.

Both end up here for the part that actually moves data.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def repoint_and_retire(cur, source_id: str, target_id: str, *, source_gstn: str | None,
                       source_pan: str | None) -> dict[str, int]:
    """Move every supplier_id reference from source to target, then deactivate source.

    Discovers the tables to update from information_schema rather than a hardcoded
    list, so a new table carrying supplier_id is covered the day it is added
    instead of quietly orphaning rows.

    Each table gets its own savepoint: where the source's row would collide with
    an equivalent row the target already has (a duplicate GSTIN in
    supplier_gstns, say), the source's is redundant by definition and is dropped
    rather than failing the whole merge. A negative count in the result means
    "deleted as redundant".
    """
    cur.execute(
        """SELECT table_name FROM information_schema.columns
            WHERE column_name = 'supplier_id' AND table_schema = 'public'
              AND table_name <> 'dim_suppliers'"""
    )
    # Both cursor flavours reach here: routers/vendors.py passes a plain cursor
    # (tuple rows) and routers/review_hub.py a RealDictCursor (dict rows). Reading
    # the column by position works for one and raises KeyError on the other, so
    # this stays agnostic rather than dictating the caller's cursor type.
    tables = [
        r[0] if isinstance(r, (tuple, list)) else r["table_name"]
        for r in cur.fetchall()
    ]

    moved: dict[str, int] = {}
    for t in tables:
        cur.execute("SAVEPOINT sp_merge")
        try:
            cur.execute(
                f"UPDATE {t} SET supplier_id = %s WHERE supplier_id = %s",
                (target_id, source_id),
            )
            moved[t] = cur.rowcount
            cur.execute("RELEASE SAVEPOINT sp_merge")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT sp_merge")
            # The redundant source row cannot always be deleted either: another
            # table may hold a foreign key to it (vendor_tds_config_log
            # references dim_vendor_tds_overrides, for one). That delete used to
            # raise straight out of this handler and abort the entire merge, so
            # it gets its own savepoint. If it fails too, the row is left where
            # it is — it belongs to a supplier that is about to be deactivated,
            # so leaving it is inert, and losing the merge is not.
            cur.execute("SAVEPOINT sp_merge_del")
            try:
                cur.execute(f"DELETE FROM {t} WHERE supplier_id = %s", (source_id,))
                moved[t] = -cur.rowcount
                cur.execute("RELEASE SAVEPOINT sp_merge_del")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT sp_merge_del")
                cur.execute("RELEASE SAVEPOINT sp_merge_del")
                logger.warning(
                    "[vendor_merge] %s: could not repoint or delete rows for retired "
                    "supplier %s; left in place", t, source_id)
                moved[t] = 0
            cur.execute("RELEASE SAVEPOINT sp_merge")

    # Backfill the survivor only where it was missing something.
    cur.execute(
        """UPDATE dim_suppliers
              SET pan  = COALESCE(NULLIF(pan, ''), %s),
                  gstn = COALESCE(gstn, %s::char(15))
            WHERE supplier_id = %s""",
        (source_pan, source_gstn if source_gstn and len(source_gstn) == 15 else None, target_id),
    )
    # Retire rather than delete: the row stays for audit, hidden from the list.
    cur.execute(
        "UPDATE dim_suppliers SET is_active = false, pan_dup_deferred = false "
        " WHERE supplier_id = %s",
        (source_id,),
    )
    cur.execute(
        "UPDATE dim_suppliers SET pan_dup_deferred = false WHERE supplier_id = %s",
        (target_id,),
    )
    return {k: v for k, v in moved.items() if v}
