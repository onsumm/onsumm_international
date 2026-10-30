"""
TDS FY Reset Job — runs at midnight April 1.

1. Creates vendor_tds_fy_state rows for the new FY
2. Expires Sec 197 certificates past valid_to
3. Flags unresolved catch-up decisions from prior FY as 'abandoned'
"""
from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger(__name__)


async def run_fy_reset(conn, new_fy_start: date | None = None):
    """Execute the FY reset.

    new_fy_start: defaults to April 1 of current year.
    """
    if not new_fy_start:
        today = date.today()
        new_fy_start = date(today.year, 4, 1)

    fy_year = new_fy_start.year
    new_fy = f"{fy_year}-{str(fy_year + 1)[-2:]}"
    prior_fy = f"{fy_year - 1}-{str(fy_year)[-2:]}"

    # 1. Create new FY state for all active vendor+section combos
    inserted = await conn.execute(
        """INSERT INTO vendor_tds_fy_state
               (tenant_id, supplier_id, tds_section_id, fy)
           SELECT DISTINCT tenant_id, supplier_id, tds_section_id, $1
           FROM vendor_tds_fy_state
           WHERE fy = $2
           ON CONFLICT (tenant_id, supplier_id, tds_section_id, fy) DO NOTHING""",
        new_fy, prior_fy,
    )
    logger.info("[tds_fy_reset] seeded FY %s state rows: %s", new_fy, inserted)

    # 2. Expire Sec 197 certificates past their valid_to
    expired = await conn.execute(
        """UPDATE dim_vendor_tds_overrides SET is_active = false
           WHERE lower_cert_valid_to < $1
             AND certificate_no IS NOT NULL
             AND is_active = true""",
        new_fy_start,
    )
    logger.info("[tds_fy_reset] expired certificates: %s", expired)

    # 3. Flag unresolved catch-up from prior FY
    abandoned = await conn.execute(
        """UPDATE vendor_tds_fy_state SET catchup_decision = 'abandoned'
           WHERE fy = $1 AND catchup_decision = 'pending'""",
        prior_fy,
    )
    logger.info("[tds_fy_reset] abandoned pending catch-ups from %s: %s", prior_fy, abandoned)

    return {"new_fy": new_fy, "prior_fy": prior_fy}
