"""Spendable USDC in a treasury Safe: the figure payouts are checked against.

The Safe's on-chain balance is not the whole story:

  - In demo settlement (CRYPTO_SETTLEMENT=demo) a payout is recorded but never
    broadcast, so the real balance does not fall. `settled_offchain_units` is
    what those demo payouts took out.
  - A released payout still waiting for signatures (or for its receipt) has
    already claimed its amount. `reserved_units` sums those.

    available = on-chain balance − settled off-chain − reserved

The pay page shows this beside the raw balance, and `PayoutService.prepare`
checks a new payout against it, so two payouts cannot both claim the same
funds and a demo cannot spend more than the Safe holds.
"""
from __future__ import annotations

from typing import Optional

# Payout sub-statuses that hold a Safe nonce: released, not yet confirmed.
_HOLDING = ("PROPOSED", "PARTIALLY_SIGNED", "READY", "SUBMITTED")


def treasury_book(cur, tenant_id: str, wallet: dict, balance: Optional[int]) -> dict:
    """Book figures for one treasury wallet row; units are USDC base units (str)."""
    cur.execute(
        """SELECT COALESCE(SUM(amount_units) FILTER (WHERE simulated), 0) AS sim_out,
                  COALESCE(SUM(amount_units) FILTER (WHERE created_at >= date_trunc('day', now())), 0) AS today_out,
                  COUNT(*) FILTER (WHERE created_at >= date_trunc('day', now())) AS today_n
             FROM chain_transfers
            WHERE tenant_id = %s AND direction = 'OUT' AND NOT removed AND lower(from_address) = lower(%s)""",
        (tenant_id, wallet["address"]))
    t = cur.fetchone() or {}
    cur.execute(
        """SELECT COALESCE(SUM(gross_units), 0) AS reserved, COUNT(*) AS n
             FROM crypto_disbursement_details
            WHERE tenant_id = %s AND treasury_wallet_id = %s AND sub_status = ANY(%s)""",
        (tenant_id, wallet["id"], list(_HOLDING)))
    r = cur.fetchone() or {}
    sim_out, reserved = int(t.get("sim_out") or 0), int(r.get("reserved") or 0)
    return {
        "settled_offchain_units": str(sim_out),
        "reserved_units": str(reserved), "reserved_count": int(r.get("n") or 0),
        "paid_today_units": str(int(t.get("today_out") or 0)), "paid_today_count": int(t.get("today_n") or 0),
        "available_units": str(max(0, balance - sim_out - reserved)) if balance is not None else None,
    }


def available_units(cur, tenant_id: str, wallet: dict, balance: Optional[int]) -> Optional[int]:
    """`treasury_book(...)["available_units"]` as an int; None when the balance is unknown.

    Savepoint-protected: on a database without the demo-settlement columns it
    falls back to the raw balance.
    """
    if balance is None:
        return None
    try:
        cur.execute("SAVEPOINT treasury_book")
        book = treasury_book(cur, tenant_id, wallet, balance)
        cur.execute("RELEASE SAVEPOINT treasury_book")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT treasury_book")
        return balance
    return int(book["available_units"])
