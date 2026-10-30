import os
from pathlib import Path
from dotenv import load_dotenv

_root = Path(__file__).resolve().parent
load_dotenv(_root / ".env")

# ── Database ──────────────────────────────────────────────────────────────────

DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", "5432"))
DB_NAME     = os.getenv("DB_NAME", "postgres")
DB_USER     = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_SSL      = os.getenv("DB_SSL", "require")
DB_SSL_CERT = os.getenv("DB_SSL_CERT", "global-bundle.pem")

# Use DB_URL if set (falling back to this repo's DEV_DB_URL convention), else
# build from parts. Strip query params since asyncpg gets SSL via ssl= arg.
_raw_url = os.getenv("DB_URL") or os.getenv("DEV_DB_URL") or (
    f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)
DATABASE_URL = _raw_url.split("?")[0]

# ── Pipeline batch & polling ──────────────────────────────────────────────────

API_SYNC_BATCH      = int(os.getenv("API_SYNC_BATCH",      "50"))
WEBHOOK_BATCH       = int(os.getenv("WEBHOOK_BATCH",       "50"))
UPLOAD_BATCH        = int(os.getenv("UPLOAD_BATCH",        "10"))
PROMOTION_BATCH     = int(os.getenv("PROMOTION_BATCH",     "100"))
APPROVAL_BATCH      = int(os.getenv("APPROVAL_BATCH",      "50"))
TDS_BATCH           = int(os.getenv("TDS_BATCH",           "50"))
POLL_INTERVAL_S     = float(os.getenv("POLL_INTERVAL_S",   "5"))

# ── Tally Prime sync ──────────────────────────────────────────────────────────

TALLY_HOST                 = os.getenv("TALLY_HOST", "localhost")
TALLY_PORT                 = int(os.getenv("TALLY_PORT", "9000"))
TALLY_COMPANY              = os.getenv("TALLY_COMPANY_NAME", "")
TALLY_SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "15"))
TALLY_PULL_DAYS            = int(os.getenv("TALLY_PULL_DAYS", "30"))
TALLY_THREAD_POOL          = int(os.getenv("TALLY_THREAD_POOL", "4"))
DEFAULT_SALES_LEDGER       = os.getenv("DEFAULT_SALES_LEDGER", "Sales A/c")
DEFAULT_TAX_LEDGER         = os.getenv("DEFAULT_TAX_LEDGER", "")
DEFAULT_BANK_LEDGER        = os.getenv("DEFAULT_BANK_LEDGER", "Cash")

# ── OCR ───────────────────────────────────────────────────────────────────────

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
POPPLER_PATH   = os.getenv("POPPLER_PATH", "")   # e.g. /opt/homebrew/bin on macOS

# How the OCR engine reads a PDF before GPT-4o structures it. Restart to apply.
#   auto   - use the embedded text layer when the PDF has one, else vision (default)
#   text   - always use the text layer; fail if the PDF has none
#   vision - always rasterise pages and use GPT-4o vision (pre-text-layer behaviour)
# Digital invoices carry exact characters in their text layer, so "auto" avoids
# vision's occasional misread digits (GSTINs and invoice numbers are the usual
# casualties) and is faster. Scans have no text layer and fall through to vision.
OCR_TO_USE = os.getenv("OCR_TO_USE", "auto").strip().lower()
OPENAI_OCR_MODEL = os.getenv("OPENAI_OCR_MODEL", "gpt-4o")

# Text-layer adequacy thresholds. A PDF qualifies for text mode only when it
# averages >= PER_PAGE chars AND no single page falls below MIN_PAGE (which is
# how a scanned page hiding inside an otherwise-digital PDF gets caught).
OCR_TEXT_MIN_CHARS_PER_PAGE = int(os.getenv("OCR_TEXT_MIN_CHARS_PER_PAGE", "100"))
OCR_TEXT_MIN_CHARS_ANY_PAGE = int(os.getenv("OCR_TEXT_MIN_CHARS_ANY_PAGE", "20"))

# Longest PDF any upload route or OCR will accept. Checked before a file is saved
# and again before OCR (so emailed PDFs are covered). An 88-page book sent to OCR
# on 2026-10-10 rendered every page into memory and froze the 1 GB API box.
# frontend/src/pages/UploadPage.tsx mirrors this to warn before uploading.
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "20"))

# Partially-selectable PDFs: a healthy text layer can still omit fields that exist
# only inside a raster letterhead (a vendor GSTIN baked into the logo band). When
# text extraction leaves a legally-required field empty AND the page carries images,
# re-read with vision and fill ONLY the gaps — text values are never overwritten.
OCR_GAP_BACKFILL = os.getenv("OCR_GAP_BACKFILL", "true").strip().lower() in ("1", "true", "yes")

# ── Sandbox.co.in KYB / GST ──────────────────────────────────────────────────

SANDBOX_API_KEY    = os.getenv("SANDBOX_API_KEY", "")
SANDBOX_API_SECRET = os.getenv("SANDBOX_API_SECRET", "")
SANDBOX_BASE_URL   = os.getenv("SANDBOX_BASE_URL", "https://api.sandbox.co.in")

# ── Misc ──────────────────────────────────────────────────────────────────────

TENANT_ID          = os.getenv("TENANT_ID")   # single-tenant fallback; multi-tenant ignores this
PIPELINE_TENANT_ID  = os.getenv("PIPELINE_TENANT_ID", "")   # legacy single-value; prefer PIPELINE_TENANT_IDS
PIPELINE_TENANT_IDS: list[str] = [t.strip() for t in PIPELINE_TENANT_ID.split(",") if t.strip()]
LOG_LEVEL          = os.getenv("LOG_LEVEL", "INFO")

# Amount threshold (in base currency) below which payments are auto-approved
AUTO_APPROVE_THRESHOLD = float(os.getenv("AUTO_APPROVE_THRESHOLD", "50000"))

# ── FX / Multi-Currency ───────────────────────────────────────────────────────

FX_FALLBACK_API_KEY    = os.getenv("FX_FALLBACK_API_KEY", "")
FX_FETCH_CURRENCIES    = os.getenv("FX_FETCH_CURRENCIES", "USD,EUR,GBP,JPY,AED,SGD,AUD,CAD").split(",")
FX_PRIMARY_SOURCE_URL  = os.getenv("FX_PRIMARY_SOURCE_URL", "https://open.er-api.com/v6/latest/INR")
FX_BACKFILL_DAYS       = int(os.getenv("FX_BACKFILL_DAYS", "90"))

FX_DEFAULT_SETTINGS = {
    "enabled": True,
    "reconciliation_tolerance_pct": 2.0,
    "revaluation_frequency": "DAILY",
    "alerts_enabled": True,
    "stale_alert_days": 3,
    "thresholds": {
        "USD": {"pay_now_below": 82.0, "delay_pay_above": 87.0, "collect_now_above": 85.0, "volatility_threshold_pct": 1.5},
        "EUR": {"pay_now_below": 89.0, "delay_pay_above": 95.0, "collect_now_above": 92.0, "volatility_threshold_pct": 1.5},
        "GBP": {"pay_now_below": 105.0, "delay_pay_above": 112.0, "collect_now_above": 108.0, "volatility_threshold_pct": 1.5},
        "JPY": {"pay_now_below": 0.55, "delay_pay_above": 0.62, "collect_now_above": 0.58, "volatility_threshold_pct": 2.0},
        "AED": {"pay_now_below": 22.0, "delay_pay_above": 24.5, "collect_now_above": 23.5, "volatility_threshold_pct": 1.5},
        "SGD": {"pay_now_below": 60.0, "delay_pay_above": 65.0, "collect_now_above": 63.0, "volatility_threshold_pct": 1.5},
        "AUD": {"pay_now_below": 53.0, "delay_pay_above": 58.0, "collect_now_above": 56.0, "volatility_threshold_pct": 1.5},
        "CAD": {"pay_now_below": 59.0, "delay_pay_above": 64.0, "collect_now_above": 62.0, "volatility_threshold_pct": 1.5},
    },
}

# ── Pipeline CDC config ───────────────────────────────────────────────────────
# All pipeline tuning lives here.  Workers declare only `name`; they inherit
# from _default and override per-key as needed.
#
# Keys
#   backfill_batch      rows per iteration during Phase 1 (full drain)
#   incremental_batch   rows per iteration during Phase 2 (CDC / steady-state)
#   poll_interval_s     cooldown between iterations when rows ARE found (Phase 2)
#   idle_poll_interval_s sleep / LISTEN timeout when queue is empty (Phase 2)
#   error_backoff_s     initial retry delay after an exception
#   error_backoff_cap_s maximum retry delay (exponential cap)

PIPELINE_CFG: dict[str, dict] = {
    "_default": {
        "backfill_batch":       200,
        "incremental_batch":     50,
        "poll_interval_s":        5.0,
        "idle_poll_interval_s":  60.0,
        "error_backoff_s":        5.0,
        "error_backoff_cap_s":  120.0,
    },
    "api_sync":          { "backfill_batch": 100, "incremental_batch": 25, "poll_interval_s": 1.0 },
    "webhook":           { "backfill_batch": 100, "incremental_batch": 25, "poll_interval_s": 1.0 },
    "upload_ocr":        { "backfill_batch":  20, "incremental_batch":  5, "poll_interval_s": 2.0 },
    "staging_promotion": { "backfill_batch": 200, "incremental_batch": 50, "poll_interval_s": 2.0 },
    "tds_processing":    { "backfill_batch": 100, "incremental_batch": 50, "poll_interval_s": 5.0 },
    "category_enrichment":{ "backfill_batch": 200, "incremental_batch": 100, "poll_interval_s": 10.0 },
    "gst_verification":  { "backfill_batch":  50, "incremental_batch": 20, "poll_interval_s":  5.0, "idle_poll_interval_s": 3600.0 },
    "bank_verification": { "backfill_batch":  50, "incremental_batch": 20, "poll_interval_s":  5.0, "idle_poll_interval_s": 3600.0 },
    "deduplication":     { "backfill_batch": 100, "incremental_batch": 50, "poll_interval_s": 10.0 },
    # Review Hub. One wakeup = one full reconcile pass across every tenant and
    # adapter, so the cooldown carries the cost control: fact_invoices is written
    # constantly by staging_promotion / tds_processing / category_enrichment, and
    # without a long poll_interval_s this would reconcile every few seconds.
    "review_adapters":   { "poll_interval_s": 60.0, "idle_poll_interval_s": 300.0,
                           "error_backoff_s": 15.0, "error_backoff_cap_s": 600.0 },
    # The agent. incremental_batch is small because each row is a model call
    # sharing a ten-connection pool with twelve other workers.
    "review_agent":      { "incremental_batch": 8, "poll_interval_s": 5.0,
                           "idle_poll_interval_s": 120.0,
                           "error_backoff_s": 30.0, "error_backoff_cap_s": 600.0 },
    # Time-driven — skip_backfill=True; only poll cadence matters
    "approval":   { "poll_interval_s": 30.0, "idle_poll_interval_s": 90.0 },
    "tally_read": { "poll_interval_s": 60.0, "idle_poll_interval_s": 120.0 },
    "tally_sync": { "poll_interval_s": 60.0, "idle_poll_interval_s": 120.0 },
    # Time-driven — polls hourly, skips immediately if today's rate already exists
    "fx_rate": {
        "poll_interval_s":       3600.0,
        "idle_poll_interval_s": 14400.0,
        "error_backoff_s":        300.0,
        "error_backoff_cap_s":   3600.0,
    },
    # Time-driven — polls every 4 hours, skips if today's notes already generated
    "fx_agent": {
        "poll_interval_s":       14400.0,
        "idle_poll_interval_s": 14400.0,
        "error_backoff_s":        600.0,
        "error_backoff_cap_s":   3600.0,
    },
}
