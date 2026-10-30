"""Seed a tenant's TDS catalogue for B2B vendor payments — sections, vendor
categories, and the mapping rules that join them.

    python scripts/seed_tds_b2b_catalogue.py <tenant_id> [--dry-run]

Why this exists: nothing seeds dim_tds_sections when a tenant is created, so a
new tenant has no sections, no categories, and therefore no TDS at all
(calculate_tds returns "No mapping rule - vendor not categorised"). Two live
tenants are in exactly that state.

Idempotent on (tenant_id, return_code, nature_of_payment) for sections and on
(tenant_id, name) for categories, so re-running tops up rather than duplicating.
That key matters: the existing catalogue already carries duplicate sections from
a seed applied repeatedly under slightly different labels.

Rates and thresholds below are the caller-supplied FY 2026-27 chart. Two notes
on how they are expressed:

  * **Split rates are done with two categories, not one section.** The schema
    has has_split_rate / rate_individual_huf / rate_others columns, but NOTHING
    in the codebase reads them — the engine only ever uses default_rate. So
    194C's 1%/2% and 194D's 2%/10% are modelled as two categories mapped to the
    same section with a rate_override each, which is the pattern already working
    for 194C.
  * **A threshold belongs to a section, not a category.** 194-O's threshold
    differs by payee type (₹5L/yr for Individual/HUF, none for others), so it
    needs two sections rather than one section with two categories.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# (return_code, old_label, nature_of_payment, rate, threshold_type, t1, t2, pan_missing_rate, order)
#
# return_code is the 4-digit Income Tax Act 2025 payment code that goes on the
# return. The codes for pre-existing sections came with the data; the ones added
# here follow the same series. 194-IC is the one payment whose code could not be
# confirmed, and the column is NOT NULL — so it carries the literal "194IC"
# instead of an invented 10xx. That is deliberately not 4 digits, so it cannot
# be mistaken for a filing code and shows up the moment anyone looks. Replace it
# with the real code before filing. Calculation is unaffected either way: the
# engine reads old_section_label, never return_code.
SECTIONS = [
    # --- services & contracts ---
    ("1023", "194C",     "Contractor Payments",                        "1.00",  "DUAL",         "30000",   "100000", "20", 40),
    ("1026", "194JA",    "Technical Services",                         "2.00",  "AGGREGATE_FY", "50000",   None,     "20", 20),
    ("1027", "194JB",    "Professional Services",                      "10.00", "AGGREGATE_FY", "50000",   None,     "20", 10),
    ("1028", "194JB",    "Director Fees",                              "10.00", "NONE",         None,      None,     "20", 30),
    ("1006", "194H",     "Commission or brokerage",                    "2.00",  "AGGREGATE_FY", "20000",   None,     "20", 50),
    ("1005", "194D",     "Insurance commission",                       "2.00",  "AGGREGATE_FY", "20000",   None,     "20", 55),
    # --- rent ---
    ("1008", "194I(a)",  "Rent - Plant/Machinery/Equipment",           "2.00",  "PER_MONTH",    "50000",   None,     "20", 58),
    ("1009", "194I(b)",  "Rent - Building/Land/Furniture",             "10.00", "PER_MONTH",    "50000",   None,     "20", 60),
    # --- goods & assets ---
    ("1031", "194Q",     "Goods Purchase",                             "0.10",  "EXCESS_OF",    "5000000", None,     "5",  70),
    ("1010", "194IA",    "Purchase of immovable property",             "1.00",  "PER_TXN",      "5000000", None,     "20", 72),
    ("194IC", "194IC",   "Joint development agreement",                "10.00", "NONE",         None,      None,     "20", 74),
    ("1037", "194S",     "Virtual digital assets",                     "1.00",  "AGGREGATE_FY", "50000",   None,     "20", 76),
    # --- platforms, benefits, finance ---
    ("1035", "194O",     "E-commerce seller - Individual/HUF",         "0.10",  "AGGREGATE_FY", "500000",  None,     "5",  78),
    ("1035", "194O",     "E-commerce seller - Company/Other",          "0.10",  "NONE",         None,      None,     "5",  79),
    ("1033", "194R",     "Perquisite or benefit to business associate", "10.00", "AGGREGATE_FY", "20000",   None,     "20", 82),
    ("1022", "194A",     "Interest - General",                         "10.00", "AGGREGATE_FY", "10000",   None,     "20", 80),
    ("1067", "194T",     "Partner remuneration/commission/interest",    "10.00", "AGGREGATE_FY", "20000",   None,     "20", 84),
]

# (category name, description, nature_of_payment it maps to, rate_override, priority)
CATEGORIES = [
    ("Contractor - Individual/HUF",      "Job work, transport, manpower, advertising, catering - individual or HUF payee", "Contractor Payments",                        "1.000",  40),
    ("Contractor - Company/Firm",        "Same work, company/firm/other payee - 2% instead of 1%",                         "Contractor Payments",                        "2.000",  50),
    ("Technical Services",               "Technical services, call centre, film royalty",                                  "Technical Services",                         None,     20),
    ("Professional Services",            "CA, lawyer, consultant, doctor, architect; royalties; non-compete",              "Professional Services",                      None,     10),
    ("Director Fees",                    "Non-salary payments to a director - no threshold",                               "Director Fees",                              None,     30),
    ("Commission / Brokerage",           "Agents, brokers and commission payees",                                          "Commission or brokerage",                    None,     55),
    ("Insurance Agent - Individual/Firm","Insurance commission, non-company payee",                                        "Insurance commission",                       "2.000",  56),
    ("Insurance Agent - Company",        "Insurance commission, company payee - 10% instead of 2%",                        "Insurance commission",                       "10.000", 57),
    ("Rent - Plant & Machinery",         "Equipment and machinery hire",                                                   "Rent - Plant/Machinery/Equipment",           None,     58),
    ("Rent - Building/Land",             "Office, warehouse, land, furniture",                                             "Rent - Building/Land/Furniture",             None,     60),
    ("Goods Purchase (194Q)",            "Goods suppliers, where our turnover crossed Rs 10 crore last year",              "Goods Purchase",                             None,     70),
    ("Property Seller (194-IA)",         "Sale of immovable property other than agricultural land",                        "Purchase of immovable property",             None,     72),
    ("Joint Development Partner",        "Payments under a joint development agreement",                                   "Joint development agreement",                None,     74),
    ("Virtual Digital Asset Seller",     "Crypto and other VDA transfers",                                                 "Virtual digital assets",                     None,     76),
    ("E-commerce Seller - Individual",   "Platform seller, individual/HUF - Rs 5 lakh annual threshold",                   "E-commerce seller - Individual/HUF",         None,     78),
    ("E-commerce Seller - Company",      "Platform seller, company/other - no threshold",                                  "E-commerce seller - Company/Other",          None,     79),
    ("Business Associate (Perquisite)",  "Free goods, trips, gifts given to a business associate",                         "Perquisite or benefit to business associate", None,     82),
    ("Interest - General",               "Interest on loans or deposits, non-bank",                                        "Interest - General",                         None,     80),
    ("Partner (Firm)",                   "Remuneration, commission or interest paid by a firm to its partners",            "Partner remuneration/commission/interest",   None,     84),
]

# Stale categories with no mapping rule and no vendors — they only clutter the
# picker. Deactivated, never deleted, so the choice is reversible.
RETIRE = ["Contractor", "Landlord / Rent", "Goods Supplier", "Director", "Interest Payee", "Health"]


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if not args:
        sys.exit(__doc__)
    tenant = args[0]

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    import asyncpg
    conn = await asyncpg.connect(os.getenv("DB_URL") or os.getenv("DEV_DB_URL"))
    tx = conn.transaction()
    await tx.start()
    added_s = updated_s = added_c = added_r = retired = 0
    try:
        before = await conn.fetch(
            "SELECT * FROM dim_tds_sections WHERE tenant_id=$1::uuid", tenant)
        backup = Path(os.getenv("TMP", ".")) / f"tds_catalogue_backup_{tenant[:8]}.json"
        backup.write_text(json.dumps([dict(r) for r in before], default=str, indent=1), encoding="utf-8")

        sec_ids: dict[str, str] = {}
        for rc, label, nature, rate, ttype, t1, t2, pmr, order in SECTIONS:
            row = await conn.fetchrow(
                """SELECT tds_section_id, default_rate, threshold_type, threshold_amount,
                          threshold_amount_2, pan_missing_rate
                   FROM dim_tds_sections
                   WHERE tenant_id=$1::uuid AND nature_of_payment=$2
                     AND return_code IS NOT DISTINCT FROM $3""",
                tenant, nature, rc)
            if row:
                sec_ids[nature] = str(row["tds_section_id"])
                # Bring an existing row in line with the chart (e.g. Director
                # Fees carried a 50k threshold the chart says does not exist).
                # Compare numerically — the DB hands back Decimal('30000.00')
                # where the chart says '30000', and string-comparing those
                # reports every untouched row as a change.
                def same(db_val, want) -> bool:
                    if db_val is None or want is None:
                        return (db_val is None or Decimal(str(db_val)) == 0) and want is None
                    return Decimal(str(db_val)) == Decimal(str(want))

                differs = (
                    not same(row["default_rate"], rate)
                    or row["threshold_type"] != ttype
                    or not same(row["threshold_amount"], t1)
                    or not same(row["threshold_amount_2"], t2)
                    or not same(row["pan_missing_rate"], pmr)
                )
                if differs:
                    await conn.execute(
                        """UPDATE dim_tds_sections SET default_rate=$2::numeric,
                               threshold_type=$3, threshold_amount=$4::numeric,
                               threshold_amount_2=$5::numeric, pan_missing_rate=$6::numeric,
                               old_section_label=$7, display_order=$8, is_active=true, updated_at=now()
                           WHERE tds_section_id=$1""",
                        row["tds_section_id"], rate, ttype, t1, t2, pmr, label, order)
                    updated_s += 1
                    print(f"  ~ section {label:9} {nature[:38]:40} rate={rate} {ttype} t1={t1} t2={t2}")
                continue
            new_id = await conn.fetchval(
                """INSERT INTO dim_tds_sections
                       (tenant_id, return_code, old_section_label, nature_of_payment, side,
                        default_rate, pan_missing_rate, threshold_type, threshold_amount,
                        threshold_amount_2, base_calculation, is_active, display_order)
                   VALUES ($1::uuid,$2,$3,$4,'AP',$5::numeric,$6::numeric,$7,$8::numeric,$9::numeric,
                           'GST_EXCLUSIVE',true,$10)
                   RETURNING tds_section_id""",
                tenant, rc, label, nature, rate, pmr, ttype, t1, t2, order)
            sec_ids[nature] = str(new_id)
            added_s += 1
            print(f"  + section {label:9} {nature[:38]:40} rate={rate} {ttype} t1={t1} t2={t2}")

        for name, desc, nature, override, priority in CATEGORIES:
            cat_id = await conn.fetchval(
                "SELECT category_id FROM dim_vendor_categories WHERE tenant_id=$1::uuid AND name=$2",
                tenant, name)
            if not cat_id:
                cat_id = await conn.fetchval(
                    """INSERT INTO dim_vendor_categories
                           (tenant_id, name, description, allowed_entity_types, display_order, is_active)
                       VALUES ($1::uuid,$2,$3,ARRAY['INDIVIDUAL_HUF','OTHERS'],$4,true)
                       RETURNING category_id""",
                    tenant, name, desc, priority)
                added_c += 1
                print(f"  + category {name}")
            else:
                await conn.execute(
                    "UPDATE dim_vendor_categories SET is_active=true WHERE category_id=$1", cat_id)
            section_id = sec_ids.get(nature)
            if not section_id:
                print(f"  ! category {name}: no section for {nature!r} — rule skipped")
                continue
            has_rule = await conn.fetchval(
                """SELECT 1 FROM dim_tds_mapping_rules
                   WHERE tenant_id=$1::uuid AND vendor_category_id=$2 AND is_active""",
                tenant, cat_id)
            if not has_rule:
                await conn.execute(
                    """INSERT INTO dim_tds_mapping_rules
                           (tenant_id, tds_section_id, vendor_category_id, priority, rate_override,
                            description, is_active)
                       VALUES ($1::uuid,$2::uuid,$3,$4,$5::numeric,$6,true)""",
                    tenant, section_id, cat_id, priority, override, f"{name} -> {nature}")
                added_r += 1
                print(f"  + rule     {name} -> {nature}" + (f" @ {override}%" if override else ""))

        for name in RETIRE:
            n = await conn.execute(
                """UPDATE dim_vendor_categories c SET is_active=false
                   WHERE c.tenant_id=$1::uuid AND c.name=$2 AND c.is_active
                     AND NOT EXISTS (SELECT 1 FROM dim_tds_mapping_rules m
                                     WHERE m.vendor_category_id=c.category_id AND m.is_active)
                     AND NOT EXISTS (SELECT 1 FROM dim_suppliers s
                                     WHERE s.vendor_category_id=c.category_id)""",
                tenant, name)
            if n.endswith("1"):
                retired += 1
                print(f"  - retired empty category {name}")

        print(f"\nsections +{added_s} ~{updated_s} | categories +{added_c} | rules +{added_r} | retired {retired}")
        print(f"backup of prior sections: {backup}")
        if dry:
            await tx.rollback()
            print("DRY RUN — rolled back")
        else:
            await tx.commit()
            print("committed")
    except Exception:
        await tx.rollback()
        raise
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
