"""RailRouter: decides which rail an approved bill is paid on.

The rule that matters (00_DECISIONS.md D2): an India-resident tenant never gets
the USDC rail, whatever the request says. Everything else defaults to the bank
rail, so a request without `rail` behaves exactly as before the crypto rail
existed.
"""
from __future__ import annotations

from typing import Optional

from ...integrations.payments.rails import RAILS, Rail
from .policy import PolicyEngine, PolicyResult, load_policy


class RailRouter:
    def __init__(self, policy: Optional[PolicyEngine] = None):
        self.policy = policy or PolicyEngine()

    @staticmethod
    def normalise(requested: Optional[str]) -> str:
        r = (requested or "IMPS").strip().upper()
        return "IMPS" if r in ("BANK", "INR", "") else r

    def select(self, cur, tenant_id: str, requested: Optional[str]) -> tuple[Rail, PolicyResult]:
        """The rail to use and, for USDC, the tenant-level policy verdict."""
        name = self.normalise(requested)
        rail = RAILS.get(name)
        if rail is None:
            res = PolicyResult().refuse("RAIL_NOT_SUPPORTED", f"Unknown payment rail {requested!r}.")
            return RAILS["IMPS"], res
        if name == "IMPS":
            return rail, PolicyResult()
        cur.execute("SELECT COALESCE(is_resident_india, true) AS r FROM dim_tenants WHERE tenant_id=%s", (tenant_id,))
        row = cur.fetchone() or {"r": True}
        return rail, self.policy.check_tenant(bool(row["r"]), load_policy(cur, tenant_id))

    def available(self, cur, tenant_id: str) -> list[dict]:
        """Rails the UI may offer, each with the reason when it is not available."""
        out = []
        for name in ("IMPS", "USDC"):
            _, res = self.select(cur, tenant_id, name)
            out.append({"rail": name, "allowed": res.allowed, "codes": res.codes, "messages": res.messages})
        return out
