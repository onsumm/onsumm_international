"""
SAP Item Code Mapping — TOON catalog document generator.

Renders every UNIQUE SAP item_code → material_description in the DB (for one
tenant) as a TOON (Token-Oriented Object Notation) document — the compact,
schema-declared representation the GPT-4o-mini matcher feeds to the LLM:

    catalog[N]{item_code,material_desc}:
      4560000390,COAL
      4570012520,"C.I Pulley,Size-14"",Section-B,Groove-2"
      ...

Usage:
    python3 scripts/sap_catalog_toon.py \
        --tenant d0e00000-0000-0000-0000-000000000004 \
        --out docs/sap_catalog.toon
"""

import argparse
import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
DB_URL = os.getenv("DB_URL")


def toon_cell(v) -> str:
    s = "" if v is None else str(v)
    if any(ch in s for ch in (",", '"', "\n")) or s != s.strip():
        return '"' + s.replace('"', '""') + '"'
    return s


def build_toon(rows) -> str:
    body = [f"  {toon_cell(code)},{toon_cell(desc)}" for code, desc in rows]
    return f"catalog[{len(rows)}]{{item_code,material_desc}}:\n" + "\n".join(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="d0e00000-0000-0000-0000-000000000004")
    ap.add_argument("--out", default=None, help="write TOON doc to this file (else stdout)")
    args = ap.parse_args()

    if not DB_URL:
        sys.exit("DB_URL not set in environment / .env")

    conn = psycopg2.connect(dsn=DB_URL)
    cur = conn.cursor()
    cur.execute(
        """SELECT item_code, material_desc
           FROM sap_item_catalog
           WHERE tenant_id = %s AND is_active
           ORDER BY item_code""",
        (args.tenant,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    toon = build_toon(rows)

    if args.out:
        out_path = args.out
        if not os.path.isabs(out_path):
            out_path = os.path.join(os.path.dirname(__file__), "..", out_path)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(toon + "\n")
        print(f"Wrote {len(rows)} items as TOON → {out_path} ({len(toon)} chars)")
    else:
        print(toon)


if __name__ == "__main__":
    main()
