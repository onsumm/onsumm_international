import os
from pathlib import Path
from dotenv import load_dotenv

_root = Path(__file__).resolve().parents[2]  # backend_ocr/app/config.py -> project root
load_dotenv(_root / ".env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# TypeSafe Jev: the classifier that files vendor documents (routers/vendor_doc_classify.py).
JEV_API_KEY = os.getenv("JEV_API_KEY", "")

# Build DB_URL from parts if not set directly
def _build_db_url() -> str:
    # DEV_DB_URL is this repo's .env convention for the dev/UAT database;
    # DB_URL (used by prod/Render) takes precedence when both are set.
    url = os.getenv("DB_URL", "") or os.getenv("DEV_DB_URL", "")
    if url:
        return url
    host = os.getenv("DB_HOST", "")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "neondb")
    user = os.getenv("DB_USER", "")
    pwd  = os.getenv("DB_PASSWORD", "")
    ssl  = os.getenv("DB_SSL", "require")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{name}?sslmode={ssl}"

DB_URL = _build_db_url()
POPPLER_PATH = os.getenv("POPPLER_PATH", "")

# Document storage (app/core/object_store.py): onsumm-assets on prod,
# onsumm-assets-dev on dev. Unset = documents are kept on local disk.
S3_BUCKET = os.getenv("S3_BUCKET", "").strip()
S3_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-south-1"
# Documents are never public. The API checks the tenant, then hands the browser
# a link that works for SIGNED_URL_TTL seconds: a CloudFront signed URL when the
# CDN below is configured (scripts/setup_cdn.sh), else an S3 presigned URL.
SIGNED_URL_TTL = int(os.getenv("SIGNED_URL_TTL", "300"))
FILES_CDN_DOMAIN = os.getenv("FILES_CDN_DOMAIN", "").strip()
CLOUDFRONT_KEY_ID = os.getenv("CLOUDFRONT_KEY_ID", "").strip()
CLOUDFRONT_PRIVATE_KEY_PATH = os.getenv("CLOUDFRONT_PRIVATE_KEY_PATH", "").strip()

# Connection pool — created lazily on first use so import doesn't fail if DB is down
_pool = None

def get_pool():
    global _pool
    if _pool is None:
        from psycopg2 import pool as _pgpool
        _pool = _pgpool.ThreadedConnectionPool(
            minconn=2,
            maxconn=50,
            dsn=DB_URL,
            connect_timeout=10,
        )
    return _pool

def get_conn():
    """Borrow a connection from the pool, retrying up to 10s if exhausted."""
    import time
    deadline = time.monotonic() + 10
    while True:
        try:
            return get_pool().getconn()
        except Exception as e:
            if "exhausted" in str(e).lower() and time.monotonic() < deadline:
                time.sleep(0.25)
                continue
            raise

def ensure_column(cur, table: str, column: str, ddl_type: str) -> None:
    """Add `column` to `table` only when it is missing.

    `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` takes an ACCESS EXCLUSIVE lock even
    when the column already exists, so running it on a request path makes every
    request queue behind any open transaction on that table — and every later
    reader queue behind it. This reads information_schema first (no table lock)
    and only issues the ALTER on the one run where the column is really missing.
    `table`, `column` and `ddl_type` are code constants, never user input.
    """
    cur.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND table_name = %s AND column_name = %s",
        (table, column),
    )
    if cur.fetchone() is None:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl_type}")


def _end_read_only_transaction(conn) -> None:
    """Roll back a pooled connection that only ever read.

    Handlers that just SELECT never commit, and returning the connection to the
    pool does not end its transaction, so it sat "idle in transaction" holding
    table locks until something reused it. When no transaction id was assigned
    the transaction wrote nothing, so rolling it back loses nothing. One that
    wrote and was left uncommitted is not touched (it may be relied on) but is
    logged so it can be found.
    """
    import logging
    from psycopg2 import extensions
    try:
        if conn.closed or conn.status != extensions.STATUS_IN_TRANSACTION:
            return
        with conn.cursor() as cur:
            cur.execute("SELECT pg_current_xact_id_if_assigned() IS NULL")
            read_only = cur.fetchone()[0]
        if read_only:
            conn.rollback()
        else:
            logging.getLogger(__name__).warning(
                "[pool] connection returned with uncommitted writes; leaving the transaction open")
    except Exception:
        pass


def put_conn(conn, close=False):
    """Return a connection to the pool (or discard on error)."""
    if conn is None:
        return
    if not close:
        _end_read_only_transaction(conn)
    try:
        get_pool().putconn(conn, close=close)
    except Exception:
        try:
            conn.close()  # last resort: close it directly so it's not held open
        except Exception:
            pass

SMTP_HOST        = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT        = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER        = os.getenv("SMTP_USER", "")
SMTP_PASSWORD    = os.getenv("SMTP_PASSWORD", "")
SMTP_ADMIN_EMAIL    = os.getenv("SMTP_ADMIN_EMAIL", "")
APPROVAL_SECRET     = os.getenv("APPROVAL_SECRET", "change-me")
JWT_SECRET          = os.getenv("JWT_SECRET", "change-me-jwt-secret")
IMAP_HOST           = os.getenv("IMAP_HOST", "imap.gmail.com")

SANDBOX_API_KEY    = os.getenv("SANDBOX_API_KEY", "")
SANDBOX_API_SECRET = os.getenv("SANDBOX_API_SECRET", "")

# ── Lendix disbursement (IMPS) gateway ───────────────────────────────────────
# docs/DISBURSEMENT_IMPS_API.md. api-dev.lendix.ai is a UAT/dev environment with
# its own hard ceiling (₹10 max per transaction) — this is not the production
# gateway regardless of which DB/tenant it's called for.
DISBURSEMENT_BASE_URL = os.getenv("DISBURSEMENT_BASE_URL", "https://api-dev.lendix.ai")
DISBURSEMENT_API_KEY  = os.getenv("DISBURSEMENT_API_KEY", "")
DISBURSEMENT_NBFC_NAME = os.getenv("DISBURSEMENT_NBFC_NAME", "Rupee112")


# Hostname fragments that mark a gateway as somewhere payments are safe to
# practise against. Deliberately a allow-list of known non-production markers:
# an endpoint we cannot place is treated as production, because wrongly
# promising "no real funds move" over a live rail is the costly direction of
# the two.
_NON_PROD_MARKERS = ("uat", "-dev.", "dev-", "sandbox", "staging", "localhost", "127.0.0.1", ".test")


def is_demo_gateway(base_url: str = "") -> bool:
    """Whether payments on this configuration move real money.

    Two separate ways a payment can be harmless: the gateway call is skipped
    entirely (DISBURSEMENT_FORCE_SUCCESS), or it is made for real but against
    a bank's non-production environment. Both earn the DEMO badge; neither is
    assumed. The Payment Hub used to hardcode this to True, which would have
    promised "no real funds move" on the day the base URL was pointed at
    production.
    """
    if DISBURSEMENT_FORCE_SUCCESS:
        return True
    url = (base_url or DISBURSEMENT_BASE_URL or "").lower()
    return any(m in url for m in _NON_PROD_MARKERS)


def gateway_profile(bank_code: str) -> dict:
    """Per-bank gateway credentials and identifiers.

    The same Lendix endpoint fronts every bank, but the payload is not the same
    for each: AU's UAT contract uses nbfcName EXUDE against remitter account
    2602201113751781, while ICICI's uses a different pair. Sending ICICI's
    nbfcName with bankProvider AUBANK is rejected by the gateway, so these
    cannot be one global setting.

    Reads DISBURSEMENT_<BANK>_<FIELD> and falls back to the global value, so a
    bank that shares the global credentials needs no configuration at all and a
    fourth bank needs env vars rather than code. BANK is the canonical code
    (ICICI, HDFC, AUBANK).
    """
    bank = "".join(ch for ch in (bank_code or "").upper() if ch.isalnum())

    def pick(field: str, default: str) -> str:
        return os.getenv(f"DISBURSEMENT_{bank}_{field}", "").strip() or default

    return {
        "api_key": pick("API_KEY", DISBURSEMENT_API_KEY),
        "nbfc_name": pick("NBFC_NAME", DISBURSEMENT_NBFC_NAME),
        # Blank means "use the tenant's remitter_accounts row", which is what
        # every payment did before the bank became selectable.
        "remitter_account": pick("REMITTER_ACCOUNT", ""),
        "base_url": pick("BASE_URL", DISBURSEMENT_BASE_URL),
    }

# ── Demo vendor-payment OTP ──────────────────────────────────────────────────
# When set, this fixed code is accepted in place of the emailed OTP that gates a
# vendor payment (disbursement_api.py), and the OTP step shows it on screen so a
# walkthrough doesn't need inbox access.
#
# OFF by default. It used to default to "123456", which meant every environment
# that merely forgot to set it — including production — shipped a fixed bypass of
# the 2FA control on money movement. A demo environment opts in explicitly with
# DISBURSEMENT_DEMO_OTP=123456; everywhere else only the real emailed OTP works
# and the UI shows no shortcut.
DISBURSEMENT_DEMO_OTP = os.getenv("DISBURSEMENT_DEMO_OTP", "").strip()
if DISBURSEMENT_DEMO_OTP.lower() in ("off", "none", "false", "0"):
    DISBURSEMENT_DEMO_OTP = ""

# ── TEMPORARY: force disbursements to SUCCESS without calling Lendix ────────
# Lendix's vendor-payment API is currently failing, which blocks the "Pay Now"
# demo end to end. With this on, verify-otp skips the real gateway call and
# synthesizes a SUCCESS response instead (same shape `_real_imps_initiate`
# returns), so the OTP -> success flow can still be demoed. OFF by default —
# turn it on only for the demo environment, and remove it once Lendix's API
# is confirmed working again; it must never be set in an environment that
# moves real money.
DISBURSEMENT_FORCE_SUCCESS = os.getenv("DISBURSEMENT_FORCE_SUCCESS", "").strip().lower() in ("1", "true", "on", "yes")

TENANT_ID   = os.getenv("BACKEND_TENANT_ID", "d0e00000-0000-0000-0000-000000000001")
TENANT_NAME = os.getenv("TENANT_NAME", "Demo Company Pvt Ltd")
TENANT_GSTN = os.getenv("TENANT_GSTN", "29AACCD1234F1ZP")

# ── RBAC / identity service ──────────────────────────────────────────────────
# When set, incoming bearer tokens are verified against the RBAC service's
# public key (JWKS or PEM auto-detected) instead of the local HS256 secret.
# e.g. RBAC_PROD_URL=https://prod-rbac.onsumm.ai/api/auth/public-key
RBAC_PROD_URL          = os.getenv("RBAC_PROD_URL", "")
# Comma-separated list of asymmetric algorithms the RBAC issuer may use.
RBAC_JWT_ALGORITHMS    = [a.strip() for a in os.getenv("RBAC_JWT_ALGORITHMS", "RS256,ES256").split(",") if a.strip()]
# Expected issuer / audience claims (optional; validated only when set).
RBAC_JWT_ISSUER        = os.getenv("RBAC_JWT_ISSUER", "")
RBAC_JWT_AUDIENCE      = os.getenv("RBAC_JWT_AUDIENCE", "")
# How long (seconds) to cache the fetched public key before refetching.
RBAC_KEY_CACHE_TTL     = int(os.getenv("RBAC_KEY_CACHE_TTL", "600"))

# ── Token lifetimes ──────────────────────────────────────────────────────────
ACCESS_TOKEN_TTL_MIN   = int(os.getenv("ACCESS_TOKEN_TTL_MIN", "60"))    # short-lived access token
REFRESH_TOKEN_TTL_DAYS = int(os.getenv("REFRESH_TOKEN_TTL_DAYS", "30"))  # long-lived refresh token

# ── Platform admin (ops) identity ────────────────────────────────────────────
# Deliberately NO default and NO fallback to JWT_SECRET. Two reasons:
#   1. A different signing key makes it cryptographically impossible for a tenant
#      token to be accepted on /api/admin/* or vice versa. That is the primary
#      barrier; the `typ` claim check is only defence in depth.
#   2. Dev and prod currently share JWT_SECRET (docs/DEV_ENVIRONMENT.md §6), so a
#      shared default would make a token minted on dev valid against production's
#      cross-tenant admin surface. Set a DIFFERENT value per environment.
# Unset => /api/admin/* returns 503 rather than falling back to something weaker.
PLATFORM_JWT_SECRET       = os.getenv("PLATFORM_JWT_SECRET", "")
PLATFORM_TOKEN_TTL_MIN    = int(os.getenv("PLATFORM_TOKEN_TTL_MIN", "30"))
PLATFORM_REFRESH_TTL_DAYS = int(os.getenv("PLATFORM_REFRESH_TTL_DAYS", "7"))
JWT_AUDIENCE_PLATFORM     = "onsumm-platform"
# Support sessions are short by construction; ops re-issues if they need longer.
IMPERSONATION_MAX_TTL_MIN = int(os.getenv("IMPERSONATION_MAX_TTL_MIN", "30"))

APP_URL = os.getenv("APP_URL", "http://localhost:8001")

# ── Gmail OAuth (user-sent email) ─────────────────────────────────────────────
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
# Must exactly match a redirect URI registered on the OAuth client, per env.
OAUTH_REDIRECT_URI   = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8001/api/oauth/email/callback")

# ── Microsoft Graph OAuth (Outlook mailboxes) ─────────────────────────────────
# Registered in Entra as "Any Entra ID tenant + personal Microsoft accounts", so
# work/school and consumer mailboxes both connect through the shared /common
# authority — we never hold a per-customer tenant id.
MS_CLIENT_ID     = os.getenv("MS_CLIENT_ID", "")
MS_CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET", "")
# Graph reuses the Google callback path: which provider a callback belongs to is
# carried in the signed state, not the URL. Overridable only for deployments that
# register a different host with Entra than with Google.
MS_OAUTH_REDIRECT_URI = os.getenv("MS_OAUTH_REDIRECT_URI", "") or OAUTH_REDIRECT_URI

# Whether "Continue with Google/Microsoft" also asks for mailbox access.
#
# True (default): one consent covers sign-in *and* the mailbox, so an SSO user
# never connects a mailbox separately.
#
# False: sign-in asks only for the permissions Entra classifies as low impact
# (openid, profile, email, offline_access, User.Read). That matters because the
# common enterprise policy — "allow user consent for apps from verified
# publishers, for selected permissions" — lets a user self-consent ONLY to those
# five, and only from a verified publisher. Mail.Read/Mail.Send are not among
# them, so asking for them at sign-in forces every enterprise user to chase
# their IT admin. With this off (and publisher verification done) they sign in
# unaided, and connect the mailbox as a separate step that can be approved once.
SSO_REQUEST_MAILBOX_SCOPES = os.getenv("SSO_REQUEST_MAILBOX_SCOPES", "true").lower() not in ("0", "false", "no")
# Where the callback sends the browser back to after connecting.
APP_RETURN_URL       = os.getenv("APP_RETURN_URL", "http://localhost:5173")
# Fernet key for encrypting stored OAuth tokens at rest. MUST be identical across
# environments/restarts or previously stored tokens become undecryptable.
EMAIL_TOKEN_KEY      = os.getenv("EMAIL_TOKEN_KEY", "")

# Fernet key for encrypting tenant-supplied Nova API keys at rest
# (int_nova_credentials.api_key_encrypted — nova_credentials_api.py). Deliberately
# a separate key from EMAIL_TOKEN_KEY: these are different secrets with different
# blast radii, and rotating one should never require decrypting/re-encrypting the
# other. MUST stay identical across restarts or previously stored keys become
# undecryptable — no fallback default, matching PLATFORM_JWT_SECRET's stance above.
NOVA_KEY_ENCRYPTION_KEY = os.getenv("NOVA_KEY_ENCRYPTION_KEY", "")
