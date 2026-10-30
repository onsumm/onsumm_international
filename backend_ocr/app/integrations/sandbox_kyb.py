"""Sandbox.co.in KYB verification helpers — GSTIN, PAN, and Bank (penny-less)."""
import time
import httpx
from ..config import SANDBOX_API_KEY, SANDBOX_API_SECRET

_BASE = "https://api.sandbox.co.in"

_token_cache: tuple[str, float] = ("", 0.0)


def _get_token(force: bool = False) -> str:
    global _token_cache
    token, expires_at = _token_cache
    if not force and token and time.time() < expires_at:
        return token
    r = httpx.post(
        f"{_BASE}/authenticate",
        headers={
            "x-api-key": SANDBOX_API_KEY,
            "x-api-secret": SANDBOX_API_SECRET,
            "x-api-version": "1.0.0",
            "Content-Type": "application/json",
        },
        timeout=10,
    )
    data = r.json()
    if r.status_code != 200 or "access_token" not in data:
        raise RuntimeError(f"Sandbox auth failed: {data.get('message', r.text)}")
    token = data["access_token"]
    _token_cache = (token, time.time() + 82800)  # cache ~23 h
    return token


def _headers(force_reauth: bool = False) -> dict:
    return {
        "x-api-key": SANDBOX_API_KEY,
        "authorization": _get_token(force=force_reauth),
        "x-api-version": "1.0",
        "Content-Type": "application/json",
    }


def _call_with_retry(fn):
    """Call fn(_headers()), retry once with fresh token on 401/subscription errors."""
    result = fn(_headers())
    raw = result.get("raw", {})
    msg = raw.get("message", "") or ""
    if not result.get("valid") and ("subscription" in msg.lower() or raw.get("code") == 401):
        result = fn(_headers(force_reauth=True))
    return result


# ── 1. GSTIN Verification ─────────────────────────────────────────────────────

def verify_gstin(gstin: str) -> dict:
    def _do(hdrs):
        try:
            r = httpx.post(f"{_BASE}/gst/compliance/public/gstin/search", headers=hdrs, json={"gstin": gstin}, timeout=10)
            data = r.json()
            if r.status_code != 200 or data.get("code") != 200:
                return {"valid": False, "error": data.get("message", "Verification failed"), "raw": data}
            d = data.get("data", {}).get("data", {})
            return {
                "valid": True,
                "legal_name": d.get("lgnm"),
                "trade_name": d.get("tradeNam"),
                "status": d.get("sts", ""),
                "pan": gstin[2:12] if len(gstin) == 15 else None,
                "raw": data,
            }
        except Exception as e:
            return {"valid": False, "error": str(e), "raw": {}}
    return _call_with_retry(_do)


# ── 2. PAN Verification ───────────────────────────────────────────────────────

def verify_pan(pan: str, name: str) -> dict:
    payload = {
        "@entity": "in.co.sandbox.kyc.pan_verification.request",
        "pan": pan.upper(),
        "name_as_per_pan": name[:100],
        "consent": "Y",
        "reason": "KYB vendor onboarding verification for payment processing",
    }
    def _do(hdrs):
        try:
            r = httpx.post(f"{_BASE}/kyc/pan/verify", headers=hdrs, json=payload, timeout=10)
            data = r.json()
            if r.status_code != 200 or data.get("code") != 200:
                return {"valid": False, "error": data.get("message", "PAN verification failed"), "raw": data}
            d = data.get("data", {})
            return {
                "valid": d.get("status") == "valid",
                "name_match": d.get("name_as_per_pan_match", False),
                "pan_status": d.get("status"),
                "category": d.get("category"),
                "raw": data,
            }
        except Exception as e:
            return {"valid": False, "error": str(e), "raw": {}}
    return _call_with_retry(_do)


# ── 3. Bank Account Verification (Penny-Less) ─────────────────────────────────

def verify_bank_account(account_number: str, ifsc: str, name: str | None = None) -> dict:
    params = {"name": name} if name else {}
    def _do(hdrs):
        try:
            r = httpx.get(
                f"{_BASE}/bank/{ifsc}/accounts/{account_number}/penniless-verify",
                headers=hdrs, params=params, timeout=15,
            )
            data = r.json()
            if r.status_code != 200 or data.get("code") != 200:
                msg = data.get("message", "Bank verification failed")
                if "credit" in msg.lower() or "unavailable" in msg.lower() or r.status_code == 503:
                    return {"valid": False, "skipped": True, "error": "Bank verification skipped (service unavailable)", "raw": data}
                return {"valid": False, "error": msg, "raw": data}
            d = data.get("data", {})
            exists = d.get("account_exists", False)
            msg = (d.get("message") or "").strip()
            # account_exists is the verdict. This used to also fail on `or msg`, but
            # the provider returns a message on SUCCESS too ("Bank Account details
            # verified successfully."), so every good account was reported as
            # "verification failed: <success message>" — and no account could ever
            # reach bank_verified through this path.
            if not exists:
                return {"valid": False, "error": msg or "Account does not exist", "raw": data}
            return {"valid": True, "name_at_bank": d.get("name_at_bank"),
                    "message": msg, "raw": data}
        except Exception as e:
            return {"valid": False, "error": str(e), "raw": {}}
    return _call_with_retry(_do)
