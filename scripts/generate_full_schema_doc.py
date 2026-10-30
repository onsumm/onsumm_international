"""Regenerates docs/full_database_schema.md from the live Postgres schema.

Introspects public enums, tables/columns, primary keys, foreign keys, unique
constraints, and indexes, and writes them in the same format the doc already
uses (see docs/full_database_schema.md), so downstream readers/tools don't
need to change how they parse it.
"""
import os
import sys

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "full_database_schema.md")


def main():
    conn = psycopg2.connect(os.environ["DB_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    lines = []
    lines.append("# Full Database Schema Documentation")
    lines.append("")
    lines.append("## Documentation Objective")
    lines.append(
        "This document reflects the live PostgreSQL (AWS RDS) schema as of the last "
        "schema pull. It covers all public tables, enum types, columns, keys, "
        "constraints, and indexes."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Enum Types")
    lines.append("")

    # ── Enums ────────────────────────────────────────────────────────────────
    cur.execute(
        """
        SELECT t.typname AS enum_name, e.enumlabel AS value
        FROM pg_type t
        JOIN pg_enum e ON t.oid = e.enumtypid
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public'
        ORDER BY t.typname, e.enumsortorder
        """
    )
    enums = {}
    for row in cur.fetchall():
        enums.setdefault(row["enum_name"], []).append(row["value"])

    for enum_name in sorted(enums):
        lines.append(f"### `{enum_name}`")
        lines.append("Allowed values:")
        for v in enums[enum_name]:
            lines.append(f"- `{v}`")
        lines.append("")

    lines.append("---")
    lines.append("")

    # ── Tables ───────────────────────────────────────────────────────────────
    cur.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """
    )
    tables = [r["table_name"] for r in cur.fetchall()]

    for table in tables:
        lines.append(f"## Table: `{table}`")
        lines.append("")
        lines.append("### Columns")
        lines.append("| Column | Type | Nullable | Default |")
        lines.append("|---|---|---|---|")

        cur.execute(
            """
            SELECT column_name, data_type, udt_name, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        for col in cur.fetchall():
            dtype = col["udt_name"] if col["data_type"] == "USER-DEFINED" else col["data_type"]
            dtype = {
                "int4": "int4", "int8": "int8", "varchar": "varchar",
                "timestamptz": "timestamptz", "bool": "bool",
            }.get(dtype, dtype)
            default = col["column_default"] if col["column_default"] is not None else "NULL"
            lines.append(
                f"| `{col['column_name']}` | `{dtype}` | `{col['is_nullable']}` | `{default}` |"
            )
        lines.append("")

        lines.append("### Keys and Constraints")

        # Primary key
        cur.execute(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = 'public' AND tc.table_name = %s
              AND tc.constraint_type = 'PRIMARY KEY'
            ORDER BY kcu.ordinal_position
            """,
            (table,),
        )
        pk_cols = [r["column_name"] for r in cur.fetchall()]
        lines.append(f"**Primary keys:** {', '.join(f'`{c}`' for c in pk_cols) if pk_cols else 'none'}")

        # Foreign keys
        cur.execute(
            """
            SELECT
                kcu.column_name,
                ccu.table_name AS ref_table,
                ccu.column_name AS ref_column,
                rc.delete_rule
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
            JOIN information_schema.referential_constraints rc
              ON tc.constraint_name = rc.constraint_name AND tc.table_schema = rc.constraint_schema
            JOIN information_schema.constraint_column_usage ccu
              ON rc.unique_constraint_name = ccu.constraint_name AND rc.unique_constraint_schema = ccu.table_schema
            WHERE tc.table_schema = 'public' AND tc.table_name = %s
              AND tc.constraint_type = 'FOREIGN KEY'
            ORDER BY kcu.column_name
            """,
            (table,),
        )
        fks = cur.fetchall()
        lines.append("**Foreign keys:**")
        if fks:
            for fk in fks:
                rule = fk["delete_rule"].replace("_", " ")
                lines.append(
                    f"- `{fk['column_name']}` -> `{fk['ref_table']}.{fk['ref_column']}` (ON DELETE {rule})"
                )
        else:
            lines.append("none")

        # Unique constraints (excluding PK)
        cur.execute(
            """
            SELECT tc.constraint_name, array_agg(kcu.column_name ORDER BY kcu.ordinal_position) AS cols
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = 'public' AND tc.table_name = %s
              AND tc.constraint_type = 'UNIQUE'
            GROUP BY tc.constraint_name
            ORDER BY tc.constraint_name
            """,
            (table,),
        )
        uniques = cur.fetchall()
        if uniques:
            lines.append("**Unique constraints:**")
            for u in uniques:
                col_list = u["cols"]
                if isinstance(col_list, str):
                    col_list = col_list.strip("{}").split(",")
                cols = ", ".join(f"`{c}`" for c in col_list)
                lines.append(f"- `{u['constraint_name']}`: ({cols})")
        else:
            lines.append("**Unique constraints:** none")

        # Indexes
        cur.execute(
            """
            SELECT indexname, indexdef FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = %s
            ORDER BY indexname
            """,
            (table,),
        )
        idxs = cur.fetchall()
        lines.append("**Indexes:**")
        if idxs:
            for idx in idxs:
                lines.append(f"- `{idx['indexname']}`: `{idx['indexdef']}`")
        else:
            lines.append("- none")

        lines.append("")
        lines.append("---")
        lines.append("")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")

    print(f"Wrote {len(tables)} tables, {len(enums)} enums -> {OUT_PATH}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    sys.exit(main())
