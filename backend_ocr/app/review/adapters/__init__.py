"""Deterministic detectors: one module per existing source of "something is wrong".

They exist because the platform already computes a great deal of this and
surfaces almost none of it — ai_anomalies has seven working detectors and zero
readers, mapping_review_queue has no API, wf_tasks is written by the approval
worker and read by no router. These adapters give that signal a reader.
"""
from .base import FindingAdapter

__all__ = ["FindingAdapter", "ALL_ADAPTERS"]


def _load():
    from .anomalies import AnomaliesAdapter
    from .bill_coding import BillCodingAdapter
    from .approval_stalls import ApprovalStallsAdapter
    from .billing_discrepancies import BillingDiscrepanciesAdapter
    from .comm_stall import CommStallAdapter
    from .contract_bill_variance import ContractBillVarianceAdapter
    from .fx_alerts import FxAlertsAdapter
    from .gst_name_overwrite import GstNameOverwriteAdapter
    from .ingestion_errors import IngestionErrorsAdapter
    from .mapping_queue import MappingQueueAdapter
    from .master_data_conflict import MasterDataConflictAdapter
    from .ocr_quality import OcrQualityAdapter
    from .payment_failures import PaymentFailuresAdapter
    from .staging_validation import StagingValidationAdapter
    from .vendor_compliance import VendorComplianceAdapter
    from .vendor_duplicates import VendorDuplicatesAdapter
    from .vendor_identity import VendorIdentityAdapter
    return [
        AnomaliesAdapter(), BillingDiscrepanciesAdapter(), StagingValidationAdapter(),
        VendorIdentityAdapter(), GstNameOverwriteAdapter(), ApprovalStallsAdapter(),
        PaymentFailuresAdapter(), FxAlertsAdapter(), IngestionErrorsAdapter(),
        MappingQueueAdapter(), VendorDuplicatesAdapter(), OcrQualityAdapter(),
        VendorComplianceAdapter(), MasterDataConflictAdapter(),
        ContractBillVarianceAdapter(), CommStallAdapter(),
        BillCodingAdapter(),
    ]


ALL_ADAPTERS = _load
