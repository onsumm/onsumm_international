"""Review Hub — detection, scoring and the single write path into rh_findings.

This package is owned by the pipeline process but deliberately shared with the
FastAPI app, the same way utils/ocr_engine is (see
backend_ocr/app/integrations/ocr.py for the sys.path anchor). `types`, `ranking`
and `links` are pure — no I/O, no asyncpg — precisely so the API can import them
to score and render without pulling in the worker machinery.
"""
