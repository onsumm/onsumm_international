"""Uploads and ingestion attempts that errored and were never retried.

These are the "agent hub mistakes" in user terms: a bill was emailed in or
uploaded, something broke, and it silently never became a bill.
"""
from __future__ import annotations

from .. import links
from ..routing import data_audience
from ..types import FindingDraft, action, condition_hash, fields_evidence
from .base import FindingAdapter


class IngestionErrorsAdapter(FindingAdapter):
    detector = "adapter:ingestion_errors"
    family = "data_quality"
    source_service = "upload_ocr"

    async def scan(self, conn, tenant_id: str, limit: int):
        aud = data_audience()
        rows = await conn.fetch(
            """
            SELECT u.upload_id, u.file_name, u.status::text AS status,
                   u.error_message, u.created_at, u.uploaded_by_name, u.ocr_engine_used,
                   e.stage, e.error_type, e.validation_errors
              FROM raw_invoice_uploads u
              LEFT JOIN LATERAL (
                   SELECT stage, error_type, validation_errors
                     FROM raw_ingestion_errors
                    WHERE upload_id = u.upload_id
                    ORDER BY created_at DESC LIMIT 1
              ) e ON true
             WHERE u.tenant_id = $1::uuid
               AND u.status::text IN ('ERROR', 'PREPROCESS_ERROR')
               AND u.created_at > now() - interval '30 days'
               AND NOT EXISTS (
                   SELECT 1 FROM fact_invoices fi
                    WHERE fi.upload_id = u.upload_id AND fi.tenant_id = u.tenant_id
               )
             ORDER BY u.created_at DESC
             LIMIT $2
            """,
            tenant_id, limit,
        )

        for r in rows:
            uid = str(r["upload_id"])
            yield FindingDraft(
                tenant_id=tenant_id,
                finding_type="upload_never_became_a_bill",
                family=self.family,
                subject_type="upload",
                subject_id=uid,
                severity="MEDIUM",
                title="An uploaded document failed and never became a bill",
                detail=(r["error_message"] or r["error_type"] or
                        "Processing failed. Nothing downstream knows this document exists."),
                deep_link=links.data_extraction(),
                condition_hash=condition_hash(r["status"], r["error_type"], r["error_message"]),
                detector=self.detector,
                source_service=self.source_service,
                evidence=fields_evidence([
                    ("File", r["file_name"] or "—"),
                    ("Stage", r["stage"] or "—"),
                    ("Error", r["error_type"] or r["error_message"] or "—"),
                    ("Uploaded by", r["uploaded_by_name"] or "—"),
                    ("Engine", r["ocr_engine_used"] or "—"),
                ]),
                entities=(("upload", uid, "subject", r["file_name"]),),
                audience_roles=aud.roles,
                required_capability="edit",
                actions=[action("open_uploads", "Open extraction", kind="navigate",
                                href=links.data_extraction())],
            )
