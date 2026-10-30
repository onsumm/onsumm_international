"""
test_pipelines.py — smoke-tests for all pipeline workers.

Runs one process_batch() cycle per worker against the real Neon DB.
Tally sync is tested in isolation; a ConnectionError (Tally not running)
is treated as PASS since that path is handled gracefully by the worker.

Usage:
    python test_pipelines.py
"""
import asyncio
import logging
import sys
import traceback

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test")

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"

results: list[tuple[str, str, str]] = []  # (worker, status, detail)


# ── helpers ───────────────────────────────────────────────────────────────────

async def _test_worker(name: str, worker_cls):
    try:
        worker = worker_cls()
        count  = await worker.process_batch()
        msg    = f"processed {count} item(s)"
        results.append((name, PASS, msg))
        logger.info("[%s] %s — %s", PASS, name, msg)
    except ConnectionError as e:
        # Expected when Tally / external service is unreachable
        msg = f"ConnectionError (expected if service offline): {e}"
        results.append((name, SKIP, msg))
        logger.warning("[%s] %s — %s", SKIP, name, msg)
    except Exception:
        tb  = traceback.format_exc()
        msg = tb.splitlines()[-1]
        results.append((name, FAIL, msg))
        logger.error("[%s] %s — %s", FAIL, name, msg)
        logger.debug(tb)


# ── DB connectivity ────────────────────────────────────────────────────────────

async def test_db():
    from db import close_pool, get_pool
    logger.info("--- DB connectivity ---")
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            val = await conn.fetchval("SELECT 1")
        assert val == 1
        results.append(("DB connection", PASS, "SELECT 1 = 1"))
        logger.info("[%s] DB connection", PASS)
    except Exception:
        tb  = traceback.format_exc()
        msg = tb.splitlines()[-1]
        results.append(("DB connection", FAIL, msg))
        logger.error("[%s] DB connection — %s", FAIL, msg)
        logger.debug(tb)
        raise SystemExit("Cannot reach database — aborting further tests.")
    finally:
        await close_pool()


# ── Module imports ─────────────────────────────────────────────────────────────

def test_imports():
    logger.info("--- Module imports ---")
    modules = [
        "config",
        "db",
        "models.enums",
        "utils.fiscal",
        "pipelines.base",
        "pipelines.api_sync",
        "pipelines.webhook",
        "pipelines.upload_ocr",
        "pipelines.staging_promotion",
        "pipelines.approval",
        "pipelines.tally_sync",
        "pipelines.deduplication",
    ]
    for mod in modules:
        try:
            __import__(mod)
            results.append((f"import {mod}", PASS, ""))
            logger.info("[%s] import %s", PASS, mod)
        except Exception:
            tb  = traceback.format_exc()
            msg = tb.splitlines()[-1]
            results.append((f"import {mod}", FAIL, msg))
            logger.error("[%s] import %s — %s", FAIL, mod, msg)


# ── Fiscal utils ───────────────────────────────────────────────────────────────

def test_fiscal():
    logger.info("--- Fiscal utils ---")
    from datetime import date
    from utils.fiscal import fiscal_period, get_fiscal_year
    try:
        fy = get_fiscal_year(date(2025, 3, 31))
        assert fy == 2025, f"expected 2025, got {fy}"
        fy2 = get_fiscal_year(date(2025, 4, 1))
        assert fy2 == 2026, f"expected 2026, got {fy2}"
        fp  = fiscal_period(date(2024, 12, 15))
        assert fp == (2025, 3, 9), f"unexpected period {fp}"
        results.append(("fiscal utils", PASS, f"FY boundary + fiscal_period OK"))
        logger.info("[%s] fiscal utils", PASS)
    except AssertionError as e:
        results.append(("fiscal utils", FAIL, str(e)))
        logger.error("[%s] fiscal utils — %s", FAIL, e)


# ── Tally XML builder round-trip ───────────────────────────────────────────────

def test_tally_builder():
    logger.info("--- Tally XML builder ---")
    import sys, os
    _tally_dir = os.path.join(os.path.dirname(__file__), "tally2database")
    if _tally_dir not in sys.path:
        sys.path.insert(0, _tally_dir)
    try:
        from tally_builder import build_sales_voucher, build_receipt_voucher, build_ledger_master
        from datetime import date
        xml = build_sales_voucher(
            voucher_number="INV-001",
            invoice_date=date.today(),
            party_name="Test Party",
            sales_ledger="Sales A/c",
            line_items=[{"name": "Goods", "qty": 1, "unit": "NOS", "rate": 1000, "amount": 1000}],
            tax_ledger="GST",
            tax_amount=180,
            total_amount=1180,
            narration="Test",
        )
        assert "<VOUCHER" in xml.upper(), "No VOUCHER tag in output"
        results.append(("tally_builder build_sales_voucher", PASS, "XML generated"))
        logger.info("[%s] tally_builder build_sales_voucher", PASS)

        xml2 = build_receipt_voucher(
            voucher_number="RCP-001",
            payment_date=date.today(),
            party_name="Test Party",
            bank_ledger="Cash",
            amount=1180,
            transaction_ref="UTR123",
        )
        assert "<VOUCHER" in xml2.upper(), "No VOUCHER tag in receipt"
        results.append(("tally_builder build_receipt_voucher", PASS, "XML generated"))
        logger.info("[%s] tally_builder build_receipt_voucher", PASS)
    except Exception:
        tb  = traceback.format_exc()
        msg = tb.splitlines()[-1]
        results.append(("tally_builder", FAIL, msg))
        logger.error("[%s] tally_builder — %s", FAIL, msg)


# ── Worker batch tests ─────────────────────────────────────────────────────────

async def test_workers():
    from db import init_pool, close_pool
    from pipelines.api_sync          import ApiSyncWorker
    from pipelines.webhook           import WebhookWorker
    from pipelines.upload_ocr        import UploadOcrWorker
    from pipelines.staging_promotion import StagingPromotionWorker
    from pipelines.approval          import ApprovalWorker
    from pipelines.tally_sync        import TallySyncWorker
    from pipelines.deduplication     import DeduplicationWorker

    logger.info("--- Worker process_batch() ---")
    await init_pool()
    try:
        await _test_worker("ApiSyncWorker",          ApiSyncWorker)
        await _test_worker("WebhookWorker",          WebhookWorker)
        await _test_worker("UploadOcrWorker",        UploadOcrWorker)
        await _test_worker("StagingPromotionWorker", StagingPromotionWorker)
        await _test_worker("ApprovalWorker",         ApprovalWorker)
        await _test_worker("TallySyncWorker",        TallySyncWorker)
        await _test_worker("DeduplicationWorker",    DeduplicationWorker)
    finally:
        await close_pool()


# ── Main ───────────────────────────────────────────────────────────────────────

async def main():
    print("\n" + "=" * 60)
    print("  Processing Pipeline — Test Suite")
    print("=" * 60 + "\n")

    # 1. Imports (sync)
    test_imports()

    # 2. Fiscal utils (sync)
    test_fiscal()

    # 3. Tally XML builder (sync)
    test_tally_builder()

    # 4. DB connectivity (async) — exits on failure
    await test_db()

    # 5. Worker batches (async, need DB)
    await test_workers()

    # ── Summary ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  RESULTS")
    print("=" * 60)
    passed = failed = skipped = 0
    for name, status, detail in results:
        line = f"  {status}  {name}"
        if detail:
            line += f"\n         {detail}"
        print(line)
        if "PASS" in status:
            passed  += 1
        elif "FAIL" in status:
            failed  += 1
        else:
            skipped += 1

    print()
    print(f"  Total: {passed} passed, {failed} failed, {skipped} skipped")
    print("=" * 60 + "\n")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
