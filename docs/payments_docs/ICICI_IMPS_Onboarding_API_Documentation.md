# ICICI Bank — IMPS & Onboarding API Documentation

Reference for **Beneficiary Onboarding** and **IMPS Payments** — covering endpoints, API Gateway errors, registration/validation errors, and IMPS transaction & status-check response codes.

---

## Table of Contents

1. [Environments](#environments)
2. [Endpoints](#endpoints)
   - [Beneficiary Registration & Validation](#beneficiary-registration--validation)
   - [IMPS](#imps)
3. [API Gateway Error Codes](#api-gateway-error-codes)
4. [API Gateway Error Response Payloads](#api-gateway-error-response-payloads)
5. [Onboarding — Beneficiary Registration Errors](#onboarding--beneficiary-registration-errors)
   - [Through Account & IFSC](#through-account--ifsc)
   - [Through VPA](#through-vpa)
   - [Generic API Errors (8000–8011)](#generic-api-errors-80008011)
6. [IMPS — Transaction & Status Check Response Codes](#imps--transaction--status-check-response-codes)
7. [IMPS — Current Transaction Status Mapping](#imps--current-transaction-status-mapping)

---

## Environments

| Environment | Base URL |
|-------------|----------|
| UAT / Sandbox | `https://apibankingonesandbox.icicibank.com` |
| Live / Production | `https://apibankingone.icicibank.com` |

---

## Endpoints

### Beneficiary Registration & Validation

Errors during beneficiary registration & validation.

| Purpose | Env | Method | Endpoint |
|---------|-----|--------|----------|
| Bene registration via Account & IFSC | UAT | POST | `/api/Corporate/CIB/v1/BeneAddition` |
| Bene registration via Account & IFSC | Live | POST | `/api/Corporate/CIB/v1/BeneAddition` |
| Bene registration via VPA | UAT | POST | `/api/Corporate/CIB/VPA/v1/BeneAddition` |
| Bene registration via VPA | Live | POST | `/api/Corporate/CIB/VPA/v1/BeneAddition` |

### IMPS

IMPS transaction & status-check endpoints.

| Purpose | Env | Endpoint |
|---------|-----|----------|
| Transaction API (V1 Flow) | UAT | `/api/v1/composite-payment` |
| Transaction API (V2 Flow) | UAT | `/api/v2/composite-payment` |
| Transaction API (V1 Flow) | Live | `/api/v1/composite-payment` |
| Transaction API (V2 Flow) | Live | `/api/v2/composite-payment` |
| IMPS status check | UAT | `/api/v1/composite-status` |
| IMPS status check | Live | `/api/v1/composite-status` |

> *V1 Flow* and *V2 Flow* refer to the two composite-payment transaction flows offered by ICICI.

---

## API Gateway Error Codes

Errors occurring during requests to the ICICI Bank API Gateway.

| Error Code | Error Message | Actionable |
|-----------|---------------|------------|
| 501 | internal exception. Please do status check after sometime | Do the status check |
| 401 | Unauthorized | Check the API Key. Request should be from the API Key provided |
| 429 | Too Many Requests | Maintain the TPS defined |
| 403 | Forbidden | Check the IP address & API Key |
| 997 | Internal exception | Do the status check |
| 997 | Bad request | Check the request packet. If correct, do the status check to confirm failure |
| 8010 | INTERNAL_SERVICE_FAILURE | Check with ICICI's Tech team before actioning further |
| 8011 | Host Not Found | Check with ICICI's Tech team before actioning further |
| 8012 | BACKEND_CONNECTION_TIMEOUT — Cannot connect to service | Check with ICICI's Tech team before actioning further |
| 8013 | BACKEND_READ_TIMEOUT — Cannot read from service | Check with ICICI's Tech team before actioning further |
| 8014 | Bad URL | Check with ICICI's Tech team before actioning further |
| 8015 | Invalid decrypted request | Check with ICICI's Tech team before actioning further |
| 8016 | Request Decryption Failure | Verify request is encrypted with the correct certificate, then share with ICICI's tech team |
| 8017 | Request Schema Validation Failure | Check with ICICI's Tech team before actioning further |
| 8018 | Response Schema Validation Failure | Check with ICICI's Tech team before actioning further |
| 8019 | Response Encryption Failure | Check with ICICI's Tech team before actioning further |
| 8099 | BlankResponsefromBackend | Do the status check |
| 8123 | Configured amount limit exceeded | Failed — see payload table below |
| 8096 | Invalid request | Failed |

---

## API Gateway Error Response Payloads

Shows the exact response body returned and whether it is sent **Plain** or **Encrypted**.

| Code | Description | Format | Response Body |
|------|-------------|--------|---------------|
| 501 | internal exception | Plain | `{"Internal Exception. Initiate Status check after Some time"}` |
| 401 | Unauthorized | Plain | `{ "success": false, "response": 401, "errormessage": "Unauthorized" }` |
| 429 | Too Many Requests | Plain | `{"Too Many Requests"}` |
| 403 | Forbidden | Plain | `{ "Forbidden"}` |
| 997 | Internal exception | Encrypted | `{ "errorCode":"997", "description":"Note : Initiate Status check after Some time" }` |
| 997 | Bad request | Encrypted | `{"success":false,"errorCode":"997","description":"Bad request"}` |
| 8010 | INTERNAL_SERVICE_FAILURE | Encrypted | `{"success": false, "errorcode": 8010, "errormessage": "The system had an internal exception"}` |
| 8011 | Host Not Found | Encrypted | `{ "success": false, "response": 8011, "message": "The Server referenced in the URL cannot be reached." }` |
| 8012 | BACKEND_CONNECTION_TIMEOUT | Encrypted | `{ "success": false, "response": 8012, "message": "Cannot connect to service" }` |
| 8013 | BACKEND_READ_TIMEOUT | Encrypted | `{ "success": false, "response": 8013, "errormessage": "BACKEND_READ_TIMEOUT-Cannot read from service" }` |
| 8014 | Bad URL | Encrypted | `{ "success": false, "response": 8014, "message": "The URL is incorrect." }` |
| 8015 | Invalid decrypted request | Encrypted | `{ "success": false, "response": 8015, "message": "Mandatory Field is Missing" }` |
| 8016 | Request Decryption Failure | Plain | `{ "success": false, "response": 8016, "message": "Decryption Fail" }` |
| 8017 | Request Schema Validation Failure | Encrypted | `{ "success": false, "response": 8017, "message": "Improper JSON" }` |
| 8018 | Response Schema Validation Failure | Encrypted | `{ "success": false, "response": 8018, "message": "Improper Schema Validation" }` |
| 8019 | Response Encryption Failure | Plain | `{ "success": false, "response": 8019, "message": "Response Encryption Failure" }` |
| 8099 | BlankResponsefromBackend | Plain | `{ "success": false, "response": 8099, "message": "Internal Exception. Initiate Status check after Some time" }` |
| 8123 | Configured amount limit exceeded | Encrypted | `{ "errorCode": "8123", "description": "Configured amount limit exceeded" }` |

---

## Onboarding — Beneficiary Registration Errors

### Through Account & IFSC

| Error Code | Error Message | Description |
|-----------|---------------|-------------|
| 24036 | You cannot logon at this time. Please contact the bank for further information. | — |
| 111 | Connection timeout. | — |
| 995109 | REGISTRATION FAILED. CORPID IS MANDATORY | Corp ID is mandatory |
| 995109 | REGISTRATION FAILED. USERID IS MANDATORY | USERID is mandatory |
| 995109 | REGISTRATION FAILED. AGGREGATOR NAME IS MANDATORY | Aggregator name is mandatory |
| 995109 | REGISTRATION FAILED. AGGREGATOR ID IS MANDATORY | Aggregator ID is mandatory |
| 995109 | REGISTRATION FAILED. URN IS MANDATORY | URN is mandatory |
| 995109 | REGISTRATION FAILED. CORPORATE ALREADY REGISTERED | Corporate already registered with ICICI Bank system |
| 106803 | Invalid Login Credentials | Corp ID and User ID must match CIB; Alias ID mandatory if login ID created in CIB |
| 9906 | Invalid Corp Id or User Id or Aggregator Id is passed | Corp ID, User ID and Aggregator ID must be configured in ICICI's system |
| 999032 | Customer Not Registered | Customer not registered with ICICI Bank |
| 100340 | Counterparty already exists. Please give a unique Bank id, Branch id, Account number combination | — |
| 101436 | Incorrect IFSC entered | — |
| 999039 | Beneficiary not found | Invalid Beneficiary ID |

### Through VPA

| Error Code | Error Message | Description |
|-----------|---------------|-------------|
| 999588 | Invalid VPA | VPA entered is invalid |
| 999584 | Invalid Customer details | Invalid CorpID, UserID, or AggrID |
| 101324 | User ID is mandatory | User ID is mandatory |
| 103024 | Corporate ID is mandatory | Corp ID is mandatory |
| 999590 | Bene user already registered | Beneficiary already registered |
| 999586 | VPA null | VPA cannot be null |
| 999587 | URN null | URN cannot be null |
| 995609 | Counterparty already exists with bene ID | Existing merchant with same bene ID |
| 100260 | Nickname already exists. | Nickname already exists |
| 995608 | Nickname already exists for bene ID | Nickname already exists for the given bene ID |

### Generic API Errors (8000–8011)

| Error Code | Error Name | Description |
|-----------|-----------|-------------|
| 8000 | Invalid Encrypted Request | Decryption Failure |
| 8001 | JSON is EMPTY | JSON Schema request empty |
| 8002 | INVALID_JSON | JSON is not valid |
| 8003 | INVALID_FIELD FORMAT OR LENGTH | Field not in the format mentioned |
| 8004 | MISSING_REQUIRED_FIELD_Date | Mandatory field data is missing |
| 8005 | MISSING_REQUIRED_FIELD | Mandatory field is missing |
| 8006 | INVALID_FIELD_LENGTH | Length of field exceeds defined length |
| 8007 | Invalid JSON, OPEN CURLY BRACE MISSING | Open brace missing in JSON |
| 8008 | Invalid JSON, END CURLY BRACE MISSING | Closing brace missing in JSON |
| 8009 | Internal Server Error | White space characters |
| 8010 | Internal Service Failure | Routing Failure |
| 8011 | INVALID_FIELD | Received additional properties |

---

## IMPS — Transaction & Status Check Response Codes

Legend:
- **Txn API Action** — action if this RC is populated in the IMPS transaction API response.
- **Status Check Action** — action if this RC is populated in the `ActCode` tag of the IMPS status check API response (do status check after 5 mins or before T+2 days of initiation).
- **Class** — TD = Technical Decline, BD = Business Decline, DA = Deemed Approved.

| Act Code | Description | Scenario | Txn API Action | Status Check Action | Class | Declined By | Corrective Action |
|----------|-------------|----------|----------------|---------------------|-------|-------------|-------------------|
| 0 | Success | Beneficiary credited in real time | Success | Success | Successful | N/A | N/A |
| 1 | Invalid Beneficiary Mobile No / MAS | Beneficiary account number invalid | Failed | Failed | BD | Beneficiary Bank | Validate bene account before re-initiating |
| 2 | Amount limit exceeded for the customer | Exceeds max permissible daily value | Failed | Failed | BD | Beneficiary Bank | Retry on next calendar date |
| 3 | Frozen Account | Beneficiary account frozen | Failed | Failed | BD | Beneficiary Bank | Validate bene account before re-initiating |
| 4 | NRE Account | Beneficiary is NRE account | Failed | Failed | BD | Beneficiary Bank | Validate bene account before re-initiating |
| 5 | Closed Account | Beneficiary account closed | Failed | Failed | BD | Beneficiary Bank | Validate bene account before re-initiating |
| 6 | Limit exceeded for member bank | Remitter bank limit exceeded in current settlement cycle (NPCI reject) | Failed | Failed | BD | NPCI | Retry in next settlement cycle |
| 7 | Transaction not permitted for this account type | Not permitted for bene account type (e.g. FD) | Failed | Failed | BD | Beneficiary Bank | Validate bene account before re-initiating |
| 8 | Transaction limit exceeded for this account type | Bene bank transaction limit exceeded | Failed | Failed | BD | Beneficiary Bank | Retry on next calendar date |
| 9 | Transaction not allowed — non-reloadable card | Non-reloadable card | Failed | Failed | BD | Beneficiary Bank | Validate bene account before re-initiating |
| 10 | No routing for institution/network | IFSC & Bank ID not configured at ICICI (bank not live / negative list) | Failed | Failed | BD | Remitter Bank | Check bene bank is live in IMPS |
| 11 | Original transaction timed out — perform Query | Timed out at NPCI / Bene Bank | Pending | Pending (Check 360 on T+2) | Deemed Approved | N/A | Do not retry unless original confirmed failed |
| 12 | Bank not live on IMPS account / IFSC transfer | Bene bank not live in IMPS (NPCI) | Failed | Failed | BD | NPCI | Check bene bank is live in IMPS |
| 13 | Invalid amount | Amount negative, < Re.1, > 5 lac, or >2 decimals | Failed | Failed | TD | Remitter Bank | Correct the amount value |
| 14 | Duplicate transaction in IMPS | Duplicate txn initiated by client | Failed | Failed | TD | NPCI | Status check, retry later with different `tranRefNo` |
| 15 | Beneficiary is Merchant | Bene is a merchant | Failed | Failed | BD | Beneficiary Bank | N/A |
| 16 | Format Error | Error in packet format from client | Failed | Status inquiry failed. Reinitiate with correct packet | TD | Remitter Bank | Check & correct request packet format |
| 17 | Financial P2P/P2A txn not found — retry inquiry after 300s | Remitter bank: txn not found | N/A | Populated in `CheckStatusCode`; reinitiate at increasing intervals until 30 min post-txn. Mark failed if still 17 after 30 min | TD | N/A | Retry after some time |
| 18 | NPCI/Issuing bank not connected or down | NPCI / bene bank down | Failed | Failed | TD | NPCI | Retry after some time |
| 19 | Unconfigured RC | Declined with RC not configured at remitter bank | Failed | Failed | TD | NPCI / Beneficiary Bank | Retry after some time |
| 20 | Invalid Response Code | Declined with RC not configured at remitter bank | Failed | Failed | TD | Beneficiary Bank | Retry after some time |
| 21 | Decline on verification | Original request timed out NPCI↔bene; bene declined on verification | Failed | Failed | TD | Beneficiary Bank | Retry after some time |
| 22 | Transaction not permitted from remitter account | Remitter freeze / account-level concurrency | Failed | Failed | BD | Remitter Bank | Confirm no freeze on remitter account, then retry |
| 23 | System malfunction at ICICI CBS | Internal CBS exception | Failed | Failed | TD | Remitter Bank | Retry after some time |
| 24 | Transaction not allowed — Amount > 2 lakh | NPCI decline if amount > INR 5 lac | Failed | Failed | BD | NPCI | Review & correct amount |
| 29 | Suspected fraud — declined on risk score | NPCI risk-score fraud decline | Failed | Failed | BD | NPCI | Retry after some time |
| 30 | Original transaction timed out — perform Query | Timed out at NPCI | Pending | Pending (Check 360 on T+2) | Deemed Approved | N/A | Do not retry unless original confirmed failed |
| 31 | Original transaction timed out — check debit statement | Timed out at ICICI CBS | Pending | Pending | Deemed Approved | N/A | N/A |
| 32 | Original transaction timed out | Inter-bank txn timed out at ICICI CBS | Failed | Failed | TD | Remitter Bank | Retry after some time |
| 33 | Original transaction timed out — check debit statement | Intra-bank txn timed out at ICICI CBS | Pending | Pending | Deemed Approved | N/A | Perform status check for terminal status |
| 34 | Suspect Fraud | NPCI / bene bank fraud decline | Failed | Failed | BD | NPCI / Beneficiary Bank | Retry after some time |
| 35 | Invalid Message | Bene bank technical decline | Failed | Failed | TD | Beneficiary Bank | Retry after some time |
| 36 | Invalid Transaction | Bene bank technical decline | Failed | Failed | TD | Beneficiary Bank | Retry after some time |
| 37 | Invalid Amount | Bene bank technical decline | Failed | Failed | TD | Beneficiary Bank | Retry after some time |
| 38 | Transfer amount exceeds limit | Exceeds max per-transaction value | Failed | Failed | BD | Beneficiary Bank | Check & revise transaction value |
| 39 | Timeout at CDCI for outward transaction | Timeout at CDCI (outward) | Failed | Failed | TD | N/A | Retry after some time |
| 40 | Timeout at CDCI for intra-bank transaction | Timeout at CDCI (intra) | Pending | Pending | NA | N/A | Perform status check for terminal status |
| 41 | Transaction not processed — client txn date expired | Txn date/time ≠ current or wrong format (YYYYMMDDHHmmss) | Failed | Failed | TD | Remitter Bank | Send correct date/time & format |
| 49 | Suspected fraud — declined on model risk score | NPCI model risk-score fraud decline | Failed | Failed | BD | NPCI | Retry after some time |
| 50 | Check transaction limit exceeded | NPCI: check-transaction count breached | Failed | Failed | BD | NPCI | Retry after some time |
| 51 | Declined by bene bank — insufficient funds | Insufficient funds in bene pool account | Failed | Failed | BD | Beneficiary Bank | Retry later / next settlement cycle |
| 52 | Invalid Account | Beneficiary account invalid | Failed | Failed | BD | Beneficiary Bank | Validate bene account before re-initiating |
| 60 | Reason Unknown (Technical decline by IMPS) | Technical issue | Failed | Failed | TD | Remitter Bank | N/A |
| 61 | Reason Unknown (Technical decline by CBS) | Technical issue | Failed | Failed | TD | Remitter Bank | N/A |
| 62 | Reason Unknown (Technical decline by NPCI) | Undefined RC from NPCI | Failed | Failed | TD | NPCI | Retry after some time |
| 63 | Original transaction timed out — perform Query | Timed out at ICICI Bank | Pending | Pending | NA | N/A | Perform status check for terminal status |
| 64 | Unable to process client txn request | Technical issue | Failed | Failed | TD | Remitter Bank | Retry after some time |
| 65 | Exceed Account limit | Daily count breached OR max balance exceeded (Jan Dhan / Payments Bank) | Failed | Failed | BD | Beneficiary Bank | Retry on next calendar date |
| 67 | Customer reversed | Funds returned by bene bank | N/A | Failed | TD | Beneficiary Bank | Retry transaction |
| 68 | Duplicate transaction with settlement ID | Existing PENDING/SUCCESS txn with same Settlement ID | Failed | Failed | BD | Remitter Bank | Perform status check for terminal status |
| 69 | Funds returned from beneficiary bank | Populated in status check if funds returned | N/A | Failed | BD | Beneficiary Bank | Validate bene account before re-initiating |
| 70 | OCH — Technical Failure | Technical failure at OCH | Failed | Failed | TD | Remitter Bank | Retry after resolution |
| 71 | OCH — Invalid Amount | Technical failure at OCH | Failed | Failed | TD | Remitter Bank | Retry after resolution |
| 72 | OCH — Invalid account number | Technical failure at OCH | Failed | Failed | TD | Remitter Bank | Retry after resolution |
| 73 | Original txn timed out — check status after RECON | Timeout at OCH | Failed | Failed | TD | Remitter Bank | Retry after resolution |
| 74 | Credit amount in current/nodal account | OCH declines; ensure funds in current/nodal account | Failed | Failed | TD | Remitter Bank | Retry after resolution |
| 75 | Duplicate transaction in OCH | Technical failure at OCH | Failed | Failed | TD | Remitter Bank | Retry after resolution |
| 76 | Credit amount in current/nodal account | Balance not available in remitter account | Failed | Failed | BD | Remitter Bank | Retry after resolution |
| 77 | Balance inquiry declined — technical error | Technical failure at OCH | Failed | Failed | TD | Remitter Bank | Retry after resolution |
| 80 | Duplicate request (omit processing) — perform Query | Same request re-initiated before first is logged | Pending | Pending | TD | Remitter Bank | Perform status check for terminal status |
| 96 | Unable to process | Bene bank technical decline | Failed | Failed | TD | Beneficiary Bank | Retry after some time |
| 100 | New Code Introduction | Status check before 180s of original txn | N/A | Populated in `CheckStatusCode`; reinitiate ≥180s after txn | BD | Remitter Bank | Re-initiate status check after 300s |
| 101 | IMPS Switch Not reachable | Switch not reachable | Failed | Pending | TD | Remitter Bank | Retry after resolution |
| 102 | Perform Query transaction | Connectivity disconnected mid-transaction | Pending | Pending | N/A | N/A | Perform status check for terminal status |
| 103 | No transaction response | Internal timeout (technical) | Pending | Pending | Deemed Approved | Remitter Bank | Perform status check for terminal status |
| 201 | Invalid Beneficiary IFSC Code or NBIN | Invalid bene IFSC (NPCI reject) | Failed | Failed | BD | NPCI | Verify bene IFSC details |
| 202 | Customer Limit Exceeded | Customer txn limit exceeded | Failed | Failed | BD | Beneficiary Bank | Retry on next calendar date |
| 203 | Foreign inward remittance not allowed | N/A for Composite Payouts | Failed | Failed | BD | N/A | N/A |
| 204 | Txn not allowed — invalid payment reference | Decline by NPCI / bene bank | Failed | Failed | BD | NPCI / Beneficiary Bank | Retry after some time |
| 205 | Transaction amount less than Rs.1 | NPCI decline if amount < INR 1.00 | Failed | Failed | BD | NPCI | Verify transaction amount |
| 206 | Txn not allowed — invalid remitter account | Bene bank decline: invalid remitter account | Failed | Failed | BD | Beneficiary Bank | Highlight to Bank for investigation |
| 207 | Txn not allowed — general error | General error | Failed | Failed | TD | NPCI | Retry after some time |
| 208 | Foreign inward remittance for P2P only | N/A for Composite Payouts | Failed | Failed | BD | N/A | N/A |
| 403 | Unauthorized API Access | Txn from unwhitelisted IP | Failed | Pending | TD | Remitter Bank | Retry after some time |
| 804 | Transaction not processed | Debit not processed at CBS; status check later (outward) | N/A | Failed | TD | Remitter Bank | Re-initiate the pay-out request |
| 805 | Transaction expired — check Bank Statement | Status check beyond T+2 days (intra) | N/A | Check status against account statement | BD | Remitter Bank | Do not re-initiate without validating account statement |
| 814 | Duplicate Transaction | Duplicate pay-out at switch | Pending, do status check | Pending edge case. Status check at increasing intervals; highlight to bank if not updated by T+1 | TD | Remitter Bank | Status check returns original txn status; highlight to bank if not updated by T+2 |
| 901 | Invalid BC | Invalid BC details | Failed | Pending | TD | Remitter Bank | Retry using correct BC ID |
| 902 | Authentication failed | Incorrect passcode | Failed | Pending | TD | Remitter Bank | Retry using correct passcode |
| 903 | Invalid BC Retailer | Invalid BC retailer code | Failed | Failed | TD | Remitter Bank | Retry after some time |
| 904 | No response from DCMS | Internal exception | Failed | Failed | TD | Remitter Bank | Retry after some time |
| 905 | Card Validation failed | Card validation failed | Failed | Failed | TD | Remitter Bank | Retry after some time |
| 915 | BC Nodal Account not supported | N/A for Composite Payouts | Failed | Failed | TD | Remitter Bank | Retry after some time |
| 916 | BC Nodal Account not Found | N/A for Composite Payouts | Failed | Failed | TD | Remitter Bank | Retry after some time |
| 917 | BC Retailer code mapping not Found | N/A for Composite Payouts | Failed | Failed | TD | Remitter Bank | Retry after some time |
| 918 | BC daily amount high | N/A for Composite Payouts | Failed | Failed | TD | Remitter Bank | Retry after some time |
| 919 | BC monthly amount high | N/A for Composite Payouts | Failed | Failed | TD | Remitter Bank | Retry after some time |
| 920 | BC min invalid amount | N/A for Composite Payouts | Failed | Failed | TD | Remitter Bank | Retry after some time |
| 921 | BC max invalid amount | N/A for Composite Payouts | Failed | Failed | TD | Remitter Bank | Retry after some time |
| 922 | Channel GST amount missing | N/A for Composite Payouts | Failed | Failed | TD | Remitter Bank | Retry after some time |
| 923 | Channel service charge amount missing | N/A for Composite Payouts | Failed | Failed | TD | Remitter Bank | Retry after some time |
| 924 | Channel retailer state missing | N/A for Composite Payouts | Failed | Failed | TD | Remitter Bank | Retry after some time |
| U01 | The request is duplicate | NPCI: duplicate request | Failed | Failed | TD | NPCI | Retry after some time |
| U02 | Amount CAP is exceeded | NPCI: amount cap exceeded | Failed | Failed | BD | NPCI | Retry on next calendar date |
| U03 | Net debit CAP is exceeded | NPCI: net debit cap exceeded | Failed | Failed | BD | NPCI | Retry in next settlement cycle |
| U04 | Request is not found | NPCI: request not found | Failed | Failed | TD | NPCI | Retry after some time |
| U06 | Transaction ID is mismatched | NPCI: txn ID mismatch | Failed | Failed | TD | NPCI | Retry after some time |
| U17 | Beneficiary is not registered | NPCI: bene bank not registered | Failed | Failed | BD | NPCI | Retry after some time |
| U28 | Beneficiary not available | NPCI: bene bank not available | Failed | Failed | TD | NPCI | Retry after some time |
| U48 | Transaction id is not present | NPCI: txn id not present | Failed | Failed | TD | NPCI | Retry after some time |
| U49 | Request message id is not present | NPCI: request message id not present | Failed | Failed | TD | NPCI | Retry after some time |
| U52 | Beneficiary orgId not found | NPCI: bene orgID not found | Failed | Failed | TD | NPCI | Retry after some time |
| ZI | SUSPECTED FRAUD | Suspected fraud | Failed | Failed | BD | Remitter Bank / NPCI | Review engagement — suspected fraud |
| U92 | PAYER BANK OFFLINE | Remitter bank offline | Failed | Failed | TD | NPCI | Retry after some time |
| U78 | BENEFICIARY BANK OFFLINE | Bene bank offline | Failed | Failed | TD | NPCI | Retry after some time |
| U16 | RISK THRESHOLD EXCEEDED | Risk threshold exceeded | Failed | Failed | BD | NPCI | Retry after some time |
| U26 | Credit pay acknowledgement not received | No credit-pay ack from bene bank | Failed | Failed | TD | NPCI | Retry after some time |
| U84 | Beneficiary bank DA outgoing count | Bene DA count exceeded threshold | Failed | Failed | TD | NPCI | Retry after some time |
| UZ3 | BENEFICIARY FORCED OFFLINE | Bene forced offline | Failed | Failed | TD | NPCI | Retry after some time |
| U88 | Connection timeout in reqpay credit | Bene bank timeout during reqpay credit | Failed | Failed | TD | NPCI | Retry after some time |
| UQ1 | BENEFICIARY SCHEDULED OFFLINE | Bene scheduled offline | Failed | Failed | TD | NPCI | Retry after some time |

---

## IMPS — Current Transaction Status Mapping

Interpretation of the `Response` value returned in the Current Transaction Status.

| Response received | Status | Scenario |
|-------------------|--------|----------|
| Credit confirmation received | Success | Funds credited to beneficiary |
| Success | Success | Funds credited to beneficiary |
| Completed | Success | Funds credited to beneficiary |
| Failed | Failure | Funds reversed to remitter |
| Returned received | Failure | Funds reversed to remitter |
| Customer Returned | Failure | Funds reversed to remitter |
| Deemed approved | Pending | Transaction is pending |
| Suspect | Pending | Transaction is pending |
| Timeout | Pending | Transaction is pending |
| Initiated | Pending | Transaction is pending |
| Customer Reversed | Failure | Funds reversed to remitter |
| Reversed | Failure | Funds reversed to remitter |
| Debit Reversal Confirmation | Failure | Funds reversed to remitter |
| Chargeback Acceptance | Failure | Reversed |
| Chargeback Raise | Pending | Deemed success until chargeback acceptance/representment by bene bank |
| Complaint Raise | Pending | No fund movement between remitter & beneficiary |
| Credit Adjustment | Failure | Funds reversed to remitter |
| Differed Chargeback Acceptance | Failure | Funds reversed to remitter |
| Differed Chargeback Raise | Pending | No fund movement until chargeback acceptance/rejection |
| Differed Pre-Arbitration Declined | Success | Funds with beneficiary |
| Differed Pre-Arbitration Raise | Success | Funds with beneficiary until pre-arbitration accepted by bene bank |
| Differed Re-presentment Raise | Success | Funds with beneficiary |
| Fraud Chargeback Accept | Failure | Funds reversed to remitter |
| Fraud Chargeback Raise | Success | Funds with beneficiary until chargeback acceptance by bene bank |
| Fraud Chargeback Representment | Success | Funds with beneficiary |
| Pre-Arbitration Acceptance | Failure | Funds reversed to remitter |
| Pre-Arbitration Declined | Success | Funds with beneficiary |
| Pre-Arbitration Raise | Success | Funds with beneficiary until pre-arbitration accepted by bene bank |
| Refund Reversal Confirmation | Failure | Funds reversed to remitter |
| Re-presentment Raise | Success | Funds with beneficiary |
| Response to Complaint | Pending | No fund movement between remitter & beneficiary |
| Wrong Credit Chargeback Acceptance | Failure | Funds reversed to remitter |
| Wrong Credit Chargeback Raise | Success | Funds with beneficiary until chargeback acceptance by beneficiary |
| Wrong credit Representment | Success | Funds with beneficiary |
| Differed Pre-Arbitration Acceptance | Failure | Funds reversed to remitter |
| Arbitration Raise | Success | Funds with beneficiary |

---

### Notes

- All request/response bodies are exchanged **encrypted** using the ICICI-issued certificate, except where the API Gateway returns a **Plain** payload (see the payload table).
- For any `Pending` / `Deemed Approved` outcome, always perform a **status check** to obtain the terminal status before retrying — never blindly re-initiate a pay-out.
- Maintain the agreed **TPS** to avoid `429 Too Many Requests`.
