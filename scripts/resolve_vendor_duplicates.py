"""Fold duplicate vendor records together and recompute their TDS.

    python scripts/resolve_vendor_duplicates.py <tenant_id> [--dry-run] [--apply]

Duplicate vendor records break TDS in both directions at once: a copy without
the PAN draws the 20% s.206AA penalty while the verified copy holds the PAN, and
a copy with no vendor_category_id deducts nothing at all because calculate_tds
exits at "No mapping rule - vendor not categorised". Merging fixes both.

Grouping is deliberately split by how certain the match is:

  PAN      — same non-null PAN. The PAN *is* the legal identity, so these are
             the same vendor. Merged.
  NAME     — identical name where the group holds at most one distinct PAN and
             at most one distinct GSTIN. The PAN-less copies are the same
             vendor as the one that has it. Merged into the PAN holder.
  CONFLICT — identical name but two or more distinct PANs. Two different legal
             entities that happen to share a name (common with proprietorships).
             Reported, never merged.

The survivor is chosen on what makes TDS work, not on age: a categorised,
verified, PAN-bearing record beats one missing any of those. `repoint_and_retire`
backfills pan/gstn onto the survivor but NOT vendor_category_id, so this does
that itself — otherwise merging into an uncategorised survivor would leave the
whole vendor deducting nothing.

After each merge the survivor's FY state is rebuilt from scratch, because bills
arriving from the retired record were never counted in its cumulative totals.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2
import psycopg2.extras


def _norm(name: str) -> str:
    return " ".join((name or "").split()).upper()


def _lev(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _is_misread(pans: set[str]) -> bool:
    """Whether a set of PANs looks like one PAN read wrong rather than several.

    This is the case vendor_merge.py's docstring describes: the PANs differ
    *because* the GSTIN was misread, so a same-PAN test can never catch it. A
    transposition leaves the characters intact (an anagram), and a single
    mis-scanned digit leaves everything within an edit or two. Anything further
    apart is treated as two real entities.
    """
    ps = sorted(pans)
    return all(
        sorted(a) == sorted(b) or _lev(a, b) <= 2
        for i, a in enumerate(ps) for b in ps[i + 1:]
    )


def _pick_survivor(rows: list[dict]) -> dict:
    """Highest score wins: a record that can actually compute TDS."""
    def score(r):
        return (
            1 if r["vendor_category_id"] else 0,   # without this, no TDS at all
            1 if r["status"] == "VERIFIED" else 0,
            1 if r["pan"] else 0,                  # without this, 20% penalty
            1 if r["gstn"] else 0,
            r["bills"],                            # fewest rows to repoint
        )
    return max(rows, key=score)


def _groups(cur, tenant: str):
    cur.execute(
        """SELECT s.supplier_id::text id, s.name, s.pan, s.gstn, s.status,
                  s.vendor_category_id::text vendor_category_id, s.is_active,
                  (SELECT count(*) FROM fact_bills b WHERE b.supplier_id = s.supplier_id) bills
           FROM dim_suppliers s
           WHERE s.tenant_id = %s AND COALESCE(s.is_active, true)""",
        (tenant,),
    )
    rows = [dict(r) for r in cur.fetchall()]

    # A GSTIN encodes its holder's PAN in characters 3-12, so a record with no
    # PAN column but a GSTIN still identifies its deductee. Using that lets a
    # PAN-less duplicate group with the record that has one.
    for r in rows:
        r["eff_pan"] = (r["pan"] or "").strip().upper() or (
            (r["gstn"] or "").strip()[2:12].upper() or None)

    by_pan, by_name = defaultdict(list), defaultdict(list)
    for r in rows:
        if r["eff_pan"]:
            by_pan[r["eff_pan"]].append(r)
        by_name[_norm(r["name"])].append(r)

    pan_groups = {f"PAN:{k}": v for k, v in by_pan.items() if len(v) > 1}
    claimed = {r["id"] for v in pan_groups.values() for r in v}

    name_groups, conflicts, misread = {}, {}, {}
    for nm, v in by_name.items():
        if len(v) < 2 or all(r["id"] in claimed for r in v):
            continue
        # Only two distinct PANs mean two legal entities. Several GSTINs under
        # one PAN is just a company registered in more than one state, which is
        # ordinary — treating that as a conflict blocks a valid merge.
        pans = {r["eff_pan"] for r in v if r["eff_pan"]}
        states = {(r["gstn"] or "").strip()[:2] for r in v if r["gstn"]}
        if len(pans) <= 1:
            name_groups[f"NAME:{nm}"] = v
        elif len(states) <= 1 and _is_misread(pans):
            # Same name, same state, PANs a transposition apart: one vendor whose
            # GSTIN was misread on some bills, not several vendors.
            misread[f"NAME:{nm}"] = v
        else:
            conflicts[f"NAME:{nm}"] = v

    # Same deductee, different trading name — a proprietor's PAN against their
    # firm's name. TDS aggregates per PAN so these must merge, but the merge
    # changes which payee name the app shows, so they are surfaced separately
    # rather than folded in silently.
    renames = {}
    for key in list(pan_groups):
        names = {_norm(r["name"]) for r in pan_groups[key]}
        if len(names) > 1:
            renames[key] = pan_groups.pop(key)
    return pan_groups, name_groups, conflicts, renames, misread


async def _recompute(tenant: str, supplier_ids: list[str]) -> int:
    """Rebuild FY state and bill TDS for the survivors, after the merges commit.

    Runs as its own phase on one connection, deliberately. An earlier version
    called this inside the still-open merge transaction over a second connection,
    which cannot work: that connection sees none of the uncommitted repointing,
    and it contends with the merge's own row locks on stg_bills/fact_bills —
    which deadlocked rather than finishing.
    """
    from decimal import Decimal

    import asyncpg

    from utils.tds_engine import TdsInput, calculate_tds

    conn = await asyncpg.connect(os.getenv("DB_URL") or os.getenv("DEV_DB_URL"))
    done = 0
    try:
        for supplier_id in supplier_ids:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM vendor_tds_fy_state WHERE tenant_id=$1::uuid AND supplier_id=$2::uuid",
                    tenant, supplier_id)
                bills = await conn.fetch(
                    """SELECT stg_bill_id::text id, bill_date, subtotal, tax_amount, total_amount
                       FROM stg_bills WHERE tenant_id=$1::uuid AND supplier_id=$2::uuid
                       ORDER BY bill_date, created_at""",
                    tenant, supplier_id)
                for b in bills:
                    tax = Decimal(str(b["tax_amount"] or 0))
                    r = await calculate_tds(conn, TdsInput(
                        tenant_id=tenant, supplier_id=supplier_id, bill_id=b["id"],
                        invoice_date=b["bill_date"],
                        taxable_amount=Decimal(str(b["subtotal"] or 0)),
                        total_amount=Decimal(str(b["total_amount"] or 0)),
                        gst_amount=tax, is_gst_shown_separately=tax > 0,
                    ), skip_backdate_check=True, skip_replay_check=True)
                    code = r.old_section or r.return_code
                    status = "CALCULATED" if r.applicable else "NA"
                    for t in ("stg_bills", "fact_bills"):
                        await conn.execute(
                            f"""UPDATE {t} SET tds_section_id=$2::uuid, tds_section_code=$3,
                                   tds_rate=$4, tds_rate_percent=$4, tds_base_amount=$5,
                                   tds_amount=$6, net_payable=$7, tds_status=$8
                               WHERE stg_bill_id=$1::uuid""",
                            b["id"], r.section_id, code, r.rate, r.base_amount,
                            r.tds_amount or Decimal(0), r.net_payable, status)
                    done += 1
    finally:
        await conn.close()
    return done


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv
    if not args:
        sys.exit(__doc__)
    tenant = args[0]

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    from backend_ocr.app.domain.vendor_merge import repoint_and_retire

    conn = psycopg2.connect(os.getenv("DB_URL") or os.getenv("DEV_DB_URL"))
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    pan_groups, name_groups, conflicts, renames, misread = _groups(cur, tenant)
    merged = retired = recomputed = 0
    survivors: list[str] = []
    try:
        for kind, groups in (("PAN", pan_groups), ("NAME", name_groups), ("PAN-RENAME", renames), ("MISREAD-GSTIN", misread)):
            for key, rows in sorted(groups.items()):
                keep = _pick_survivor(rows)
                others = [r for r in rows if r["id"] != keep["id"]]
                print(f"\n[{kind}] {key.split(':', 1)[1][:52]}  ({len(rows)} records)")
                print(f"   KEEP    {keep['name'][:30]:32} pan={str(keep['pan'] or '-'):11} "
                      f"{str(keep['status']):10} cat={'Y' if keep['vendor_category_id'] else 'N'} bills={keep['bills']}")
                # Carry a category over to the survivor if it lacks one — the
                # merge helper backfills pan/gstn but not this, and without it
                # the merged vendor still computes no TDS.
                donor = next((r for r in others if r["vendor_category_id"]), None)
                if not keep["vendor_category_id"] and donor:
                    print(f"   +cat    from retired copy (survivor had none)")
                    if apply:
                        cur.execute("UPDATE dim_suppliers SET vendor_category_id=%s WHERE supplier_id=%s",
                                    (donor["vendor_category_id"], keep["id"]))
                for r in others:
                    print(f"   retire  {r['name'][:30]:32} pan={str(r['pan'] or '-'):11} "
                          f"{str(r['status']):10} cat={'Y' if r['vendor_category_id'] else 'N'} bills={r['bills']}")
                    if apply:
                        moved = repoint_and_retire(cur, r["id"], keep["id"],
                                                   source_gstn=r["gstn"], source_pan=r["pan"])
                        hot = {k: v for k, v in moved.items() if k in
                               ("fact_bills", "stg_bills", "vendor_tds_fy_state",
                                "supplier_bank_accounts", "supplier_gstns")}
                        if hot:
                            print(f"           moved {hot}")
                        retired += 1
                if apply:
                    survivors.append(keep["id"])
                merged += 1

        if conflicts:
            print(f"\n=== NOT MERGED — same name, different PANs (different legal entities) ===")
            for key, rows in sorted(conflicts.items()):
                print(f"  {key.split(':', 1)[1][:60]}")
                for r in rows:
                    derived = "" if r["pan"] else " (derived from GSTIN)"
                    print(f"     pan={str(r['eff_pan'] or '-'):11}{derived:22} "
                          f"gstin={str(r['gstn'] or '-'):17} bills={r['bills']}")

        print(f"\ngroups: {len(pan_groups)} by PAN, {len(name_groups)} by name, "
              f"{len(conflicts)} conflicts left alone")
        print(f"merged {merged} groups | retired {retired} records | recomputed {recomputed} bills")
        if apply:
            conn.commit()
            print("\nmerges COMMITTED")
            # Phase 2, only now that the repointing is visible to other sessions.
            import asyncio
            recomputed = asyncio.run(_recompute(tenant, survivors))
            print(f"recomputed TDS on {recomputed} bills across {len(survivors)} survivors")
        else:
            conn.rollback()
            print("DRY RUN — rolled back (pass --apply to commit)")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
