"""ICICI IMPS ActCode / API-Gateway error-code lookup —
docs/payments_docs/ICICI_IMPS_Onboarding_API_Documentation.md.

Lendix's gateway passes through ICICI's own ActCode (and sometimes ICICI's
raw API-Gateway error code) largely unexplained — `disbursement_api.py` was
showing whatever opaque `statusMessage` the gateway happened to return
("Disbursement gateway returned 400: All vendors failed. Last error: ICICI
API error (500)"), which says nothing about what actually went wrong or
whether it's worth retrying. This module turns a bare code into what the
documented table actually says: what happened, who declined it, and what to
do about it.
"""
from __future__ import annotations

from typing import Optional, TypedDict


class ActCodeInfo(TypedDict):
    description: str
    scenario: str
    klass: str          # TD | BD | Deemed Approved | Successful | NA
    declined_by: str
    corrective_action: str


# ── §6: IMPS — Transaction & Status Check Response Codes ────────────────────
ACT_CODES: dict[str, ActCodeInfo] = {
    "0": {"description": "Success", "scenario": "Beneficiary credited in real time", "klass": "Successful", "declined_by": "N/A", "corrective_action": "N/A"},
    "1": {"description": "Invalid Beneficiary Mobile No / MAS", "scenario": "Beneficiary account number invalid", "klass": "BD", "declined_by": "Beneficiary Bank", "corrective_action": "Validate bene account before re-initiating"},
    "2": {"description": "Amount limit exceeded for the customer", "scenario": "Exceeds max permissible daily value", "klass": "BD", "declined_by": "Beneficiary Bank", "corrective_action": "Retry on next calendar date"},
    "3": {"description": "Frozen Account", "scenario": "Beneficiary account frozen", "klass": "BD", "declined_by": "Beneficiary Bank", "corrective_action": "Validate bene account before re-initiating"},
    "4": {"description": "NRE Account", "scenario": "Beneficiary is NRE account", "klass": "BD", "declined_by": "Beneficiary Bank", "corrective_action": "Validate bene account before re-initiating"},
    "5": {"description": "Closed Account", "scenario": "Beneficiary account closed", "klass": "BD", "declined_by": "Beneficiary Bank", "corrective_action": "Validate bene account before re-initiating"},
    "6": {"description": "Limit exceeded for member bank", "scenario": "Remitter bank limit exceeded in current settlement cycle (NPCI reject)", "klass": "BD", "declined_by": "NPCI", "corrective_action": "Retry in next settlement cycle"},
    "7": {"description": "Transaction not permitted for this account type", "scenario": "Not permitted for bene account type (e.g. FD)", "klass": "BD", "declined_by": "Beneficiary Bank", "corrective_action": "Validate bene account before re-initiating"},
    "8": {"description": "Transaction limit exceeded for this account type", "scenario": "Bene bank transaction limit exceeded", "klass": "BD", "declined_by": "Beneficiary Bank", "corrective_action": "Retry on next calendar date"},
    "9": {"description": "Transaction not allowed — non-reloadable card", "scenario": "Non-reloadable card", "klass": "BD", "declined_by": "Beneficiary Bank", "corrective_action": "Validate bene account before re-initiating"},
    "10": {"description": "No routing for institution/network", "scenario": "IFSC & Bank ID not configured at ICICI (bank not live / negative list)", "klass": "BD", "declined_by": "Remitter Bank", "corrective_action": "Check bene bank is live in IMPS"},
    "11": {"description": "Original transaction timed out — perform Query", "scenario": "Timed out at NPCI / Bene Bank", "klass": "Deemed Approved", "declined_by": "N/A", "corrective_action": "Do not retry unless original confirmed failed"},
    "12": {"description": "Bank not live on IMPS account / IFSC transfer", "scenario": "Bene bank not live in IMPS (NPCI)", "klass": "BD", "declined_by": "NPCI", "corrective_action": "Check bene bank is live in IMPS"},
    "13": {"description": "Invalid amount", "scenario": "Amount negative, < Re.1, > 5 lac, or >2 decimals", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Correct the amount value"},
    "14": {"description": "Duplicate transaction in IMPS", "scenario": "Duplicate txn initiated by client", "klass": "TD", "declined_by": "NPCI", "corrective_action": "Status check, retry later with different tranRefNo"},
    "15": {"description": "Beneficiary is Merchant", "scenario": "Bene is a merchant", "klass": "BD", "declined_by": "Beneficiary Bank", "corrective_action": "N/A"},
    "16": {"description": "Format Error", "scenario": "Error in packet format from client", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Check & correct request packet format"},
    "17": {"description": "Financial P2P/P2A txn not found — retry inquiry after 300s", "scenario": "Remitter bank: txn not found", "klass": "TD", "declined_by": "N/A", "corrective_action": "Retry after some time"},
    "18": {"description": "NPCI/Issuing bank not connected or down", "scenario": "NPCI / bene bank down", "klass": "TD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "19": {"description": "Unconfigured RC", "scenario": "Declined with RC not configured at remitter bank", "klass": "TD", "declined_by": "NPCI / Beneficiary Bank", "corrective_action": "Retry after some time"},
    "20": {"description": "Invalid Response Code", "scenario": "Declined with RC not configured at remitter bank", "klass": "TD", "declined_by": "Beneficiary Bank", "corrective_action": "Retry after some time"},
    "21": {"description": "Decline on verification", "scenario": "Original request timed out NPCI<->bene; bene declined on verification", "klass": "TD", "declined_by": "Beneficiary Bank", "corrective_action": "Retry after some time"},
    "22": {"description": "Transaction not permitted from remitter account", "scenario": "Remitter freeze / account-level concurrency", "klass": "BD", "declined_by": "Remitter Bank", "corrective_action": "Confirm no freeze on remitter account, then retry"},
    "23": {"description": "System malfunction at ICICI CBS", "scenario": "Internal CBS exception", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after some time"},
    "24": {"description": "Transaction not allowed — Amount > 2 lakh", "scenario": "NPCI decline if amount > INR 5 lac", "klass": "BD", "declined_by": "NPCI", "corrective_action": "Review & correct amount"},
    "29": {"description": "Suspected fraud — declined on risk score", "scenario": "NPCI risk-score fraud decline", "klass": "BD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "30": {"description": "Original transaction timed out — perform Query", "scenario": "Timed out at NPCI", "klass": "Deemed Approved", "declined_by": "N/A", "corrective_action": "Do not retry unless original confirmed failed"},
    "31": {"description": "Original transaction timed out — check debit statement", "scenario": "Timed out at ICICI CBS", "klass": "Deemed Approved", "declined_by": "N/A", "corrective_action": "N/A"},
    "32": {"description": "Original transaction timed out", "scenario": "Inter-bank txn timed out at ICICI CBS", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after some time"},
    "33": {"description": "Original transaction timed out — check debit statement", "scenario": "Intra-bank txn timed out at ICICI CBS", "klass": "Deemed Approved", "declined_by": "N/A", "corrective_action": "Perform status check for terminal status"},
    "34": {"description": "Suspect Fraud", "scenario": "NPCI / bene bank fraud decline", "klass": "BD", "declined_by": "NPCI / Beneficiary Bank", "corrective_action": "Retry after some time"},
    "35": {"description": "Invalid Message", "scenario": "Bene bank technical decline", "klass": "TD", "declined_by": "Beneficiary Bank", "corrective_action": "Retry after some time"},
    "36": {"description": "Invalid Transaction", "scenario": "Bene bank technical decline", "klass": "TD", "declined_by": "Beneficiary Bank", "corrective_action": "Retry after some time"},
    "37": {"description": "Invalid Amount", "scenario": "Bene bank technical decline", "klass": "TD", "declined_by": "Beneficiary Bank", "corrective_action": "Retry after some time"},
    "38": {"description": "Transfer amount exceeds limit", "scenario": "Exceeds max per-transaction value", "klass": "BD", "declined_by": "Beneficiary Bank", "corrective_action": "Check & revise transaction value"},
    "39": {"description": "Timeout at CDCI for outward transaction", "scenario": "Timeout at CDCI (outward)", "klass": "TD", "declined_by": "N/A", "corrective_action": "Retry after some time"},
    "40": {"description": "Timeout at CDCI for intra-bank transaction", "scenario": "Timeout at CDCI (intra)", "klass": "NA", "declined_by": "N/A", "corrective_action": "Perform status check for terminal status"},
    "41": {"description": "Transaction not processed — client txn date expired", "scenario": "Txn date/time != current or wrong format (YYYYMMDDHHmmss)", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Send correct date/time & format"},
    "49": {"description": "Suspected fraud — declined on model risk score", "scenario": "NPCI model risk-score fraud decline", "klass": "BD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "50": {"description": "Check transaction limit exceeded", "scenario": "NPCI: check-transaction count breached", "klass": "BD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "51": {"description": "Declined by bene bank — insufficient funds", "scenario": "Insufficient funds in bene pool account", "klass": "BD", "declined_by": "Beneficiary Bank", "corrective_action": "Retry later / next settlement cycle"},
    "52": {"description": "Invalid Account", "scenario": "Beneficiary account invalid", "klass": "BD", "declined_by": "Beneficiary Bank", "corrective_action": "Validate bene account before re-initiating"},
    "60": {"description": "Reason Unknown (Technical decline by IMPS)", "scenario": "Technical issue", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "N/A"},
    "61": {"description": "Reason Unknown (Technical decline by CBS)", "scenario": "Technical issue", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "N/A"},
    "62": {"description": "Reason Unknown (Technical decline by NPCI)", "scenario": "Undefined RC from NPCI", "klass": "TD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "63": {"description": "Original transaction timed out — perform Query", "scenario": "Timed out at ICICI Bank", "klass": "NA", "declined_by": "N/A", "corrective_action": "Perform status check for terminal status"},
    "64": {"description": "Unable to process client txn request", "scenario": "Technical issue", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after some time"},
    "65": {"description": "Exceed Account limit", "scenario": "Daily count breached OR max balance exceeded (Jan Dhan / Payments Bank)", "klass": "BD", "declined_by": "Beneficiary Bank", "corrective_action": "Retry on next calendar date"},
    "67": {"description": "Customer reversed", "scenario": "Funds returned by bene bank", "klass": "TD", "declined_by": "Beneficiary Bank", "corrective_action": "Retry transaction"},
    "68": {"description": "Duplicate transaction with settlement ID", "scenario": "Existing PENDING/SUCCESS txn with same Settlement ID", "klass": "BD", "declined_by": "Remitter Bank", "corrective_action": "Perform status check for terminal status"},
    "69": {"description": "Funds returned from beneficiary bank", "scenario": "Populated in status check if funds returned", "klass": "BD", "declined_by": "Beneficiary Bank", "corrective_action": "Validate bene account before re-initiating"},
    "70": {"description": "OCH — Technical Failure", "scenario": "Technical failure at OCH", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after resolution"},
    "71": {"description": "OCH — Invalid Amount", "scenario": "Technical failure at OCH", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after resolution"},
    "72": {"description": "OCH — Invalid account number", "scenario": "Technical failure at OCH", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after resolution"},
    "73": {"description": "Original txn timed out — check status after RECON", "scenario": "Timeout at OCH", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after resolution"},
    "74": {"description": "Credit amount in current/nodal account", "scenario": "OCH declines; ensure funds in current/nodal account", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after resolution"},
    "75": {"description": "Duplicate transaction in OCH", "scenario": "Technical failure at OCH", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after resolution"},
    "76": {"description": "Credit amount in current/nodal account", "scenario": "Balance not available in remitter account", "klass": "BD", "declined_by": "Remitter Bank", "corrective_action": "Retry after resolution"},
    "77": {"description": "Balance inquiry declined — technical error", "scenario": "Technical failure at OCH", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after resolution"},
    "80": {"description": "Duplicate request (omit processing) — perform Query", "scenario": "Same request re-initiated before first is logged", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Perform status check for terminal status"},
    "96": {"description": "Unable to process", "scenario": "Bene bank technical decline", "klass": "TD", "declined_by": "Beneficiary Bank", "corrective_action": "Retry after some time"},
    "100": {"description": "New Code Introduction", "scenario": "Status check before 180s of original txn", "klass": "BD", "declined_by": "Remitter Bank", "corrective_action": "Re-initiate status check after 300s"},
    "101": {"description": "IMPS Switch Not reachable", "scenario": "Switch not reachable", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after resolution"},
    "102": {"description": "Perform Query transaction", "scenario": "Connectivity disconnected mid-transaction", "klass": "NA", "declined_by": "N/A", "corrective_action": "Perform status check for terminal status"},
    "103": {"description": "No transaction response", "scenario": "Internal timeout (technical)", "klass": "Deemed Approved", "declined_by": "Remitter Bank", "corrective_action": "Perform status check for terminal status"},
    "201": {"description": "Invalid Beneficiary IFSC Code or NBIN", "scenario": "Invalid bene IFSC (NPCI reject)", "klass": "BD", "declined_by": "NPCI", "corrective_action": "Verify bene IFSC details"},
    "202": {"description": "Customer Limit Exceeded", "scenario": "Customer txn limit exceeded", "klass": "BD", "declined_by": "Beneficiary Bank", "corrective_action": "Retry on next calendar date"},
    "203": {"description": "Foreign inward remittance not allowed", "scenario": "N/A for Composite Payouts", "klass": "BD", "declined_by": "N/A", "corrective_action": "N/A"},
    "204": {"description": "Txn not allowed — invalid payment reference", "scenario": "Decline by NPCI / bene bank", "klass": "BD", "declined_by": "NPCI / Beneficiary Bank", "corrective_action": "Retry after some time"},
    "205": {"description": "Transaction amount less than Rs.1", "scenario": "NPCI decline if amount < INR 1.00", "klass": "BD", "declined_by": "NPCI", "corrective_action": "Verify transaction amount"},
    "206": {"description": "Txn not allowed — invalid remitter account", "scenario": "Bene bank decline: invalid remitter account", "klass": "BD", "declined_by": "Beneficiary Bank", "corrective_action": "Highlight to Bank for investigation"},
    "207": {"description": "Txn not allowed — general error", "scenario": "General error", "klass": "TD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "208": {"description": "Foreign inward remittance for P2P only", "scenario": "N/A for Composite Payouts", "klass": "BD", "declined_by": "N/A", "corrective_action": "N/A"},
    "403": {"description": "Unauthorized API Access", "scenario": "Txn from unwhitelisted IP", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after some time"},
    "804": {"description": "Transaction not processed", "scenario": "Debit not processed at CBS; status check later (outward)", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Re-initiate the pay-out request"},
    "805": {"description": "Transaction expired — check Bank Statement", "scenario": "Status check beyond T+2 days (intra)", "klass": "BD", "declined_by": "Remitter Bank", "corrective_action": "Do not re-initiate without validating account statement"},
    "814": {"description": "Duplicate Transaction", "scenario": "Duplicate pay-out at switch", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Status check at increasing intervals; highlight to bank if not updated by T+2"},
    "901": {"description": "Invalid BC", "scenario": "Invalid BC details", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry using correct BC ID"},
    "902": {"description": "Authentication failed", "scenario": "Incorrect passcode", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry using correct passcode"},
    "903": {"description": "Invalid BC Retailer", "scenario": "Invalid BC retailer code", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after some time"},
    "904": {"description": "No response from DCMS", "scenario": "Internal exception", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after some time"},
    "905": {"description": "Card Validation failed", "scenario": "Card validation failed", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after some time"},
    "915": {"description": "BC Nodal Account not supported", "scenario": "N/A for Composite Payouts", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after some time"},
    "916": {"description": "BC Nodal Account not Found", "scenario": "N/A for Composite Payouts", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after some time"},
    "917": {"description": "BC Retailer code mapping not Found", "scenario": "N/A for Composite Payouts", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after some time"},
    "918": {"description": "BC daily amount high", "scenario": "N/A for Composite Payouts", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after some time"},
    "919": {"description": "BC monthly amount high", "scenario": "N/A for Composite Payouts", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after some time"},
    "920": {"description": "BC min invalid amount", "scenario": "N/A for Composite Payouts", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after some time"},
    "921": {"description": "BC max invalid amount", "scenario": "N/A for Composite Payouts", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after some time"},
    "922": {"description": "Channel GST amount missing", "scenario": "N/A for Composite Payouts", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after some time"},
    "923": {"description": "Channel service charge amount missing", "scenario": "N/A for Composite Payouts", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after some time"},
    "924": {"description": "Channel retailer state missing", "scenario": "N/A for Composite Payouts", "klass": "TD", "declined_by": "Remitter Bank", "corrective_action": "Retry after some time"},
    "U01": {"description": "The request is duplicate", "scenario": "NPCI: duplicate request", "klass": "TD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "U02": {"description": "Amount CAP is exceeded", "scenario": "NPCI: amount cap exceeded", "klass": "BD", "declined_by": "NPCI", "corrective_action": "Retry on next calendar date"},
    "U03": {"description": "Net debit CAP is exceeded", "scenario": "NPCI: net debit cap exceeded", "klass": "BD", "declined_by": "NPCI", "corrective_action": "Retry in next settlement cycle"},
    "U04": {"description": "Request is not found", "scenario": "NPCI: request not found", "klass": "TD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "U06": {"description": "Transaction ID is mismatched", "scenario": "NPCI: txn ID mismatch", "klass": "TD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "U17": {"description": "Beneficiary is not registered", "scenario": "NPCI: bene bank not registered", "klass": "BD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "U28": {"description": "Beneficiary not available", "scenario": "NPCI: bene bank not available", "klass": "TD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "U48": {"description": "Transaction id is not present", "scenario": "NPCI: txn id not present", "klass": "TD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "U49": {"description": "Request message id is not present", "scenario": "NPCI: request message id not present", "klass": "TD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "U52": {"description": "Beneficiary orgId not found", "scenario": "NPCI: bene orgID not found", "klass": "TD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "ZI": {"description": "SUSPECTED FRAUD", "scenario": "Suspected fraud", "klass": "BD", "declined_by": "Remitter Bank / NPCI", "corrective_action": "Review engagement — suspected fraud"},
    "U92": {"description": "PAYER BANK OFFLINE", "scenario": "Remitter bank offline", "klass": "TD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "U78": {"description": "BENEFICIARY BANK OFFLINE", "scenario": "Bene bank offline", "klass": "TD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "U16": {"description": "RISK THRESHOLD EXCEEDED", "scenario": "Risk threshold exceeded", "klass": "BD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "U26": {"description": "Credit pay acknowledgement not received", "scenario": "No credit-pay ack from bene bank", "klass": "TD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "U84": {"description": "Beneficiary bank DA outgoing count", "scenario": "Bene DA count exceeded threshold", "klass": "TD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "UZ3": {"description": "BENEFICIARY FORCED OFFLINE", "scenario": "Bene forced offline", "klass": "TD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "U88": {"description": "Connection timeout in reqpay credit", "scenario": "Bene bank timeout during reqpay credit", "klass": "TD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
    "UQ1": {"description": "BENEFICIARY SCHEDULED OFFLINE", "scenario": "Bene scheduled offline", "klass": "TD", "declined_by": "NPCI", "corrective_action": "Retry after some time"},
}

# ── §3: API Gateway Error Codes (transport/gateway layer, not bank ActCodes) ─
GATEWAY_ERROR_CODES: dict[str, ActCodeInfo] = {
    "501": {"description": "Internal exception", "scenario": "ICICI API Gateway internal exception", "klass": "TD", "declined_by": "ICICI API Gateway", "corrective_action": "Do a status check after some time"},
    "401": {"description": "Unauthorized", "scenario": "Request not authorized for the API key used", "klass": "TD", "declined_by": "ICICI API Gateway", "corrective_action": "Check the API Key — request must come from the provisioned key"},
    "429": {"description": "Too Many Requests", "scenario": "TPS limit exceeded", "klass": "TD", "declined_by": "ICICI API Gateway", "corrective_action": "Maintain the agreed TPS"},
    "403": {"description": "Forbidden", "scenario": "IP or API key not whitelisted", "klass": "TD", "declined_by": "ICICI API Gateway", "corrective_action": "Check the IP address & API Key"},
    "997": {"description": "Internal exception / Bad request", "scenario": "Gateway-level internal exception, or a malformed request packet", "klass": "TD", "declined_by": "ICICI API Gateway", "corrective_action": "Check the request packet; if correct, do a status check to confirm the transaction's real outcome"},
    "8010": {"description": "INTERNAL_SERVICE_FAILURE", "scenario": "Routing failure", "klass": "TD", "declined_by": "ICICI API Gateway", "corrective_action": "Check with ICICI's tech team before actioning further"},
    "8011": {"description": "Host Not Found", "scenario": "Referenced server unreachable", "klass": "TD", "declined_by": "ICICI API Gateway", "corrective_action": "Check with ICICI's tech team before actioning further"},
    "8012": {"description": "BACKEND_CONNECTION_TIMEOUT", "scenario": "Cannot connect to service", "klass": "TD", "declined_by": "ICICI API Gateway", "corrective_action": "Check with ICICI's tech team before actioning further"},
    "8013": {"description": "BACKEND_READ_TIMEOUT", "scenario": "Cannot read from service", "klass": "TD", "declined_by": "ICICI API Gateway", "corrective_action": "Check with ICICI's tech team before actioning further"},
    "8014": {"description": "Bad URL", "scenario": "Incorrect URL", "klass": "TD", "declined_by": "ICICI API Gateway", "corrective_action": "Check with ICICI's tech team before actioning further"},
    "8015": {"description": "Invalid decrypted request", "scenario": "Mandatory field missing after decryption", "klass": "TD", "declined_by": "ICICI API Gateway", "corrective_action": "Check with ICICI's tech team before actioning further"},
    "8016": {"description": "Request Decryption Failure", "scenario": "Request not encrypted with the correct certificate", "klass": "TD", "declined_by": "ICICI API Gateway", "corrective_action": "Verify the request is encrypted with the correct certificate, then share with ICICI's tech team"},
    "8017": {"description": "Request Schema Validation Failure", "scenario": "Improper JSON", "klass": "TD", "declined_by": "ICICI API Gateway", "corrective_action": "Check with ICICI's tech team before actioning further"},
    "8018": {"description": "Response Schema Validation Failure", "scenario": "Improper schema validation", "klass": "TD", "declined_by": "ICICI API Gateway", "corrective_action": "Check with ICICI's tech team before actioning further"},
    "8019": {"description": "Response Encryption Failure", "scenario": "Gateway failed to encrypt the response", "klass": "TD", "declined_by": "ICICI API Gateway", "corrective_action": "Check with ICICI's tech team before actioning further"},
    "8099": {"description": "BlankResponsefromBackend", "scenario": "Backend returned no response", "klass": "TD", "declined_by": "ICICI API Gateway", "corrective_action": "Do a status check"},
    "8123": {"description": "Configured amount limit exceeded", "scenario": "Amount exceeds the configured limit", "klass": "BD", "declined_by": "ICICI API Gateway", "corrective_action": "Reduce the amount and retry"},
    "8096": {"description": "Invalid request", "scenario": "Request rejected as invalid", "klass": "TD", "declined_by": "ICICI API Gateway", "corrective_action": "Check the request payload"},
}


def describe_act_code(code: Optional[str]) -> Optional[ActCodeInfo]:
    """Looks up a bank ActCode first, then a gateway-layer error code —
    they're disjoint numbering in practice but both arrive in the same
    `actCode` field from Lendix, so check both tables. Returns None for an
    unrecognized/absent code, so the caller can fall back to the gateway's
    own raw message rather than claim to explain something it doesn't."""
    if not code:
        return None
    code = str(code).strip()
    return ACT_CODES.get(code) or GATEWAY_ERROR_CODES.get(code)


def explain(code: Optional[str], fallback_message: str = "") -> str:
    """Human-readable one-liner for a status_message field: "<description> —
    <who declined it> — <corrective action>", or the gateway's own raw
    message when the code isn't recognized."""
    info = describe_act_code(code)
    if not info:
        return fallback_message
    parts = [info["description"]]
    if info["declined_by"] not in ("N/A", ""):
        parts.append(f"declined by {info['declined_by']}")
    if info["corrective_action"] not in ("N/A", ""):
        parts.append(info["corrective_action"])
    return " — ".join(parts)
