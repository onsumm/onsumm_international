"""
Copy TDS mapping rules from seed tenant to all other tenants that have bills.
Run: python scripts/fix_tds_tenant_rules.py
"""
import asyncio, sys, os, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncpg
import config


async def main():
    db_url = (
        f"postgresql://{config.DB_USER}:{config.DB_PASSWORD}"
        f"@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}"
        f"?sslmode={config.DB_SSL}"
    )
    conn = await asyncpg.connect(db_url)

    # Inspect actual columns
    cols = await conn.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name='dim_tds_mapping_rules'
        ORDER BY ordinal_position
        """
    )
    print("dim_tds_mapping_rules columns:", [r["column_name"] for r in cols])

    seed_tenant = "11111111-1111-1111-1111-111111111111"
    target_tenants = [
        "10000000-0000-0000-0000-000000000001",
        "d0e00000-0000-0000-0000-000000000001",
        "d0e00000-0000-0000-0000-000000000002",
    ]

    rules = await conn.fetch(
        "SELECT * FROM dim_tds_mapping_rules WHERE tenant_id=$1 AND is_active=true ORDER BY priority",
        seed_tenant,
    )
    print(f"\nSeed tenant has {len(rules)} active rules")

    for tenant_id in target_tenants:
        existing = await conn.fetchval(
            "SELECT COUNT(*) FROM dim_tds_mapping_rules WHERE tenant_id=$1", tenant_id
        )
        if existing > 0:
            print(f"tenant {tenant_id}: already has {existing} rules, skipping")
            continue

        inserted = 0
        for rule in rules:
            new_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO dim_tds_mapping_rules
                    (rule_id, tenant_id, vendor_category_id, tds_section_id,
                     rate_override, is_active, priority, created_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,now())
                """,
                new_id, tenant_id,
                rule["vendor_category_id"], rule["tds_section_id"],
                rule["rate_override"], True, rule["priority"],
            )
            inserted += 1
        print(f"tenant {tenant_id}: inserted {inserted} rules")

    print()
    rows = await conn.fetch(
        "SELECT tenant_id, COUNT(*) AS cnt FROM dim_tds_mapping_rules GROUP BY tenant_id ORDER BY tenant_id"
    )
    print("Rules per tenant:")
    for r in rows:
        print(f"  {r['tenant_id']}: {r['cnt']}")

    await conn.close()


asyncio.run(main())
