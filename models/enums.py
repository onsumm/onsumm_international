"""Python mirrors of all PostgreSQL enum types in the schema."""
from enum import Enum


class DataSource(str, Enum):
    TALLY        = "TALLY"
    ZOHO         = "ZOHO"
    QBO          = "QBO"
    XERO         = "XERO"
    FRESHBOOKS   = "FRESHBOOKS"
    SAGE         = "SAGE"
    MYOB         = "MYOB"
    WAVE         = "WAVE"
    RAZORPAY     = "RAZORPAY"
    STRIPE       = "STRIPE"
    UPLOAD       = "UPLOAD"
    MANUAL       = "MANUAL"
    GENERIC_REST = "GENERIC_REST"


class EntityType(str, Enum):
    INVOICE     = "INVOICE"
    PAYMENT     = "PAYMENT"
    BILL        = "BILL"
    CREDIT_NOTE = "CREDIT_NOTE"
    CUSTOMER    = "CUSTOMER"
    SUPPLIER    = "SUPPLIER"
    JOURNAL     = "JOURNAL"


class InvoiceStatus(str, Enum):
    DRAFT    = "DRAFT"
    SENT     = "SENT"
    PARTIAL  = "PARTIAL"
    PAID     = "PAID"
    OVERDUE  = "OVERDUE"
    VOID     = "VOID"


class PaymentStatus(str, Enum):
    PENDING  = "PENDING"
    CLEARED  = "CLEARED"
    BOUNCED  = "BOUNCED"
    REFUNDED = "REFUNDED"


class SyncStatus(str, Enum):
    PENDING = "PENDING"
    SYNCED  = "SYNCED"
    ERROR   = "ERROR"


class UploadStatus(str, Enum):
    RECEIVED         = "RECEIVED"
    PREPROCESSING    = "PREPROCESSING"
    OCR_PRIMARY      = "OCR_PRIMARY"
    OCR_FALLBACK     = "OCR_FALLBACK"
    VALIDATING       = "VALIDATING"
    DONE             = "DONE"
    ERROR            = "ERROR"
    PREPROCESS_ERROR = "PREPROCESS_ERROR"


class OcrEngine(str, Enum):
    PADDLEOCR = "PADDLEOCR"
    SERVAM    = "SERVAM"
    MERGED    = "MERGED"
    NONE      = "NONE"


class ValidationFlag(str, Enum):
    OK        = "OK"
    MISMATCH  = "MISMATCH"
    DUPLICATE = "DUPLICATE"
    REVIEW    = "REVIEW"


class RiskBucket(str, Enum):
    LOW      = "LOW"
    MEDIUM   = "MEDIUM"
    HIGH     = "HIGH"
    CRITICAL = "CRITICAL"


class TxnStatus(str, Enum):
    INITIATED  = "INITIATED"
    PROCESSING = "PROCESSING"
    COMPLETED  = "COMPLETED"
    FAILED     = "FAILED"
    BOUNCED    = "BOUNCED"
    REVERSED   = "REVERSED"
    CANCELLED  = "CANCELLED"


class ScheduleStatus(str, Enum):
    PENDING  = "PENDING"
    PARTIAL  = "PARTIAL"
    PAID     = "PAID"
    OVERDUE  = "OVERDUE"
    WAIVED   = "WAIVED"
    DISPUTED = "DISPUTED"


class PaymentCycleType(str, Enum):
    PAYABLE    = "PAYABLE"
    RECEIVABLE = "RECEIVABLE"


class PaymentMethod(str, Enum):
    BANK_TRANSFER = "BANK_TRANSFER"
    UPI           = "UPI"
    CARD          = "CARD"
    CHEQUE        = "CHEQUE"
    CASH          = "CASH"
    GATEWAY       = "GATEWAY"


class WfTaskType(str, Enum):
    NUDGE    = "NUDGE"
    ESCALATE = "ESCALATE"
    APPROVE  = "APPROVE"
    REVIEW   = "REVIEW"
    ALERT    = "ALERT"


class WfTaskStatus(str, Enum):
    PENDING     = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    DONE        = "DONE"
    SKIPPED     = "SKIPPED"


class WfEntityType(str, Enum):
    INVOICE  = "INVOICE"
    CUSTOMER = "CUSTOMER"
    PAYMENT  = "PAYMENT"
    SUPPLIER = "SUPPLIER"
    BILL     = "BILL"


class NudgeChannel(str, Enum):
    EMAIL    = "EMAIL"
    SMS      = "SMS"
    WHATSAPP = "WHATSAPP"
    SLACK    = "SLACK"
    IN_APP   = "IN_APP"


class NudgeStatus(str, Enum):
    PENDING   = "PENDING"
    SENT      = "SENT"
    FAILED    = "FAILED"
    CANCELLED = "CANCELLED"


class ConnectionStatus(str, Enum):
    ACTIVE       = "ACTIVE"
    PAUSED       = "PAUSED"
    ERROR        = "ERROR"
    DISCONNECTED = "DISCONNECTED"


class SyncJobStatus(str, Enum):
    RUNNING   = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED    = "FAILED"
    PARTIAL   = "PARTIAL"


class ConflictResolution(str, Enum):
    OUTBOUND_WRITE = "OUTBOUND_WRITE"
    SKIP           = "SKIP"
    LOG_CONFLICT   = "LOG_CONFLICT"


class BillingDiscrepancyType(str, Enum):
    QTY_MISMATCH              = "QTY_MISMATCH"
    RATE_MISMATCH             = "RATE_MISMATCH"
    COST_MISMATCH             = "COST_MISMATCH"
    MISSING_IN_NOVA           = "MISSING_IN_NOVA"
    MISSING_IN_INVOICE        = "MISSING_IN_INVOICE"
    UNMAPPED_LINE_ITEM        = "UNMAPPED_LINE_ITEM"
    ENTITY_UNRESOLVED         = "ENTITY_UNRESOLVED"
    NOVA_SNAPSHOT_UNAVAILABLE = "NOVA_SNAPSHOT_UNAVAILABLE"


class BillingDiscrepancyStatus(str, Enum):
    OPEN                  = "OPEN"
    ACKNOWLEDGED          = "ACKNOWLEDGED"
    RESOLVED              = "RESOLVED"
    DISPUTED_WITH_VENDOR  = "DISPUTED_WITH_VENDOR"
    IGNORED               = "IGNORED"


class BillingReconciliationStatus(str, Enum):
    PENDING                       = "PENDING"
    RUNNING                       = "RUNNING"
    COMPLETED                     = "COMPLETED"
    COMPLETED_WITH_DISCREPANCIES  = "COMPLETED_WITH_DISCREPANCIES"
    FAILED                        = "FAILED"
