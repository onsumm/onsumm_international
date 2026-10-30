# Disbursement IMPS API

Initiates and tracks IMPS payments to beneficiary bank accounts. All transactions are encrypted using RSA. Use `bankProvider` to pin a transaction to a specific bank — `ICICI` or `AUBANK`.

Base URL: `https://api-dev.lendix.ai`

### Supported Vendors

| Priority | Bank | Notes |
|---|---|---|
| Primary | ICICI Bank | Primary bank for IMPS disbursements with RSA encryption |
| Secondary | AU Bank | Secondary bank for IMPS disbursements |

---

## POST /disbursement/imps/initiate

Initiates an IMPS payment to transfer funds to a beneficiary account.

### Request Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `leadId` | string | Yes | Unique identifier for tracking and logging |
| `beneficiaryAccount` | string | Yes | Beneficiary bank account number (max 35 digits) |
| `beneficiaryIFSC` | string | Yes | Beneficiary IFSC code (11 characters: 4 alpha + 0 + 6 alphanumeric) |
| `beneficiaryName` | string | Yes | Beneficiary name as per bank account |
| `beneficiaryMobile` | string | Yes | Beneficiary mobile number (10 digits starting with 6-9) |
| `amount` | number | Yes | Transaction amount in INR (min ₹1, max varies by environment) |
| `tranRefNo` | string | Yes | Unique transaction reference for tracking/idempotency (alphanumeric, max 20 chars). Used as AU Bank's deduplication key — keep it under 20 chars to ensure idempotent retries. |
| `paymentRemarks` | string | Yes | Payment description (appears in bank statement) |
| `nbfcName` | string | Yes | NBFC name (e.g. `SOLOMON`, `DEVASHISH`, `AADHARINDIA`, `VRINDA`) — validated against customer config |
| `beneCode` | string | No | AU Bank beneficiary code (max 25 chars, alphanumeric). Only used when AU Bank processes the payment |
| `bankProvider` | string | Yes | Pins the transaction to a specific bank. Accepted values: `ICICI`, `AUBANK` |
| `remitterAccountNumber` | string | Yes | Remitter bank account number to debit. Must include leading zeros (e.g. `"010101000"`). Selects the specific account when multiple accounts are configured for the same NBFC + bank. |

### Amount Limits

- Minimum: ₹1 (ICICI requirement)
- UAT: ₹10 max
- Staging: ₹1,000 max
- Production: ₹1,50,000 max
- ICICI hard limit: ₹5,00,000

### Request Example

```json
{
    "leadId": "LEAD-TEST-999",
    "remitterAccountNumber": "010101000",
    "beneficiaryAccount": "12345604111218",
    "beneficiaryIFSC": "NPCI0000001",
    "beneficiaryName": "Kiran Kumar",
    "beneficiaryMobile": "9999988888",
    "amount": 1,
    "tranRefNo": "payment16756979",
    "paymentRemarks": "FTTransferP2A",
    "nbfcName": "Lendix",
    "bankProvider": "ICICI"
}
```

### Success Response (200 OK)

```json
{
    "success": true,
    "data": {
        "tranRefNo": "payment16756979",
        "bankRRN": "995033927898",
        "beneficiaryName": "VeskKP",
        "remitterAccountNumber": "010101000",
        "status": "SUCCESS",
        "statusMessage": "Transaction Successful",
        "timestamp": "2026-03-16T07:09:25.432Z"
    },
    "vendorInfo": {
        "vendorName": "ICICI",
        "vendorPriority": 1,
        "requestId": "7064b132-efa1-4d9b-ac08-15f9d46e2303",
        "endpoint": "IMPS_INITIATE",
        "responseTimeMs": 3072,
        "calledAt": "2026-03-16T07:09:22.360Z",
        "respondedAt": "2026-03-16T07:09:25.432Z",
        "reason": "HEALTH_BASED_SELECTION",
        "vendorResponse": {
            "apiCalls": [
                {
                    "callNumber": 1,
                    "apiName": "IMPS_INITIATE",
                    "response": {
                        "requestId": "1773644962452-900bea71",
                        "service": "UPI",
                        "encryptedKey": "<RSA-encrypted-session-key>",
                        "oaepHashingAlgorithm": "NONE",
                        "iv": "",
                        "encryptedData": "<AES-encrypted-payload>",
                        "clientInfo": "",
                        "optionalParam": ""
                    },
                    "status": "SUCCESS",
                    "duration": 1053,
                    "httpStatusCode": 200,
                    "attemptNumber": 1,
                    "totalAttempts": 1
                }
            ]
        }
    },
    "sessionId": "disbursement-1773644962351-hdr6d45zf",
    "timestamp": "2026-03-16T07:09:25.434Z",
    "referenceId": "7064b132-efa1-4d9b-ac08-15f9d46e2303"
}
```

> **AU Bank example (`FULL` log level)** — AU Bank calls include the request `encvalue` as well as the response `encvalue`, both AES-encrypted blobs, under `vendorResponse.apiCalls[].request` / `.response`:
> ```json
> {
>     "success": true,
>     "data": {
>         "tranRefNo": "hello123",
>         "bankRRN": "02040626068",
>         "status": "SUCCESS",
>         "statusMessage": "ACCEPTED",
>         "timestamp": "2026-06-04T13:12:37.561Z"
>     },
>     "vendorInfo": {
>         "vendorName": "AUBANK",
>         "vendorPriority": 2,
>         "endpoint": "IMPS_INITIATE",
>         "responseTimeMs": 113,
>         "reason": "HEALTH_BASED_SELECTION",
>         "logLevel": "FULL",
>         "vendorResponse": {
>             "apiCalls": [
>                 {
>                     "callNumber": 1,
>                     "apiName": "IMPS_INITIATE",
>                     "request": { "encvalue": "<AES-encrypted-request>" },
>                     "response": { "encvalue": "<AES-encrypted-response>" },
>                     "status": "SUCCESS",
>                     "duration": 93,
>                     "httpStatusCode": 200,
>                     "attemptNumber": 1,
>                     "totalAttempts": 1
>                 }
>             ]
>         }
>     }
> }
> ```
> For AU Bank, `data.status: "SUCCESS"` with `statusMessage: "ACCEPTED"` means the transaction was accepted for processing — poll `/disbursement/imps/status` for final settlement status.

### Important — `tranRefNo` in Response

The `tranRefNo` returned in the response is the **bank's internal reference ID** — not your original `tranRefNo`. You must save this value and pass it as `tranRefNo` in the status check request.

- **AU Bank** uses your `tranRefNo` as its deduplication key, so retries with the same value are idempotent.
- **ICICI** returns your original `tranRefNo` unchanged.

### Response Parameters

| Field | Type | Description |
|---|---|---|
| `success` | boolean | Overall request status |
| `data.tranRefNo` | string | Transaction reference (echoed from request, or bank-assigned — see note above) |
| `data.bankRRN` | string | Bank's 12-digit Reference Number (RRN) |
| `data.beneficiaryName` | string | Beneficiary name as per bank records |
| `data.remitterAccountNumber` | string | Remitter bank account number used for the transaction |
| `data.status` | string | Transaction status: `SUCCESS`, `PENDING`, or `FAILED` |
| `data.statusMessage` | string | Descriptive status message from bank |
| `vendorInfo` | object | Vendor information including bank name, response time, and ActCode |
| `sessionId` | string | Session identifier for this disbursement |
| `timestamp` | string | ISO 8601 timestamp of response |
| `referenceId` | string | Unique reference ID for this request |

### Transaction Status Values

| Status | Description |
|---|---|
| `SUCCESS` | Transaction completed successfully |
| `PENDING` | Transaction is being processed, check status later |
| `FAILED` | Transaction failed, check error details |
| `TIMEOUT` | Transaction timed out, check status to confirm |

### cURL Example

```bash
curl -X POST 'https://api-dev.lendix.ai/disbursement/imps/initiate' \
  --header 'Content-Type: application/json' \
  --header 'x-api-key: YOUR_API_KEY_HERE' \
  --data '{
    "leadId": "LEAD-TEST-999",
    "remitterAccountNumber":"010101000",
    "beneficiaryAccount": "12345604111218",
    "beneficiaryIFSC": "NPCI0000001",
    "beneficiaryName": "Kiran Kumar",
    "beneficiaryMobile": "9999988888",
    "amount": 1,
    "tranRefNo": "payment16756979",
    "paymentRemarks": "FTTransferP2A",
    "nbfcName": "Lendix",
    "bankProvider": "ICICI"
}'
```

### Common Error Codes (ICICI ActCode)

| ActCode | Error Type | Description | Retryable |
|---|---|---|---|
| `0` | - | Transaction Successful | N/A |
| `18` | `NPCI_DOWN` | NPCI/Issuing bank is not connected or down | Yes |
| `91` | `INSUFFICIENT_BALANCE` | Insufficient balance in remitter account | No |
| `997` | `TRANSACTION_PENDING` | Gateway failure - Initiate status check | Check Status |

### Error Response Example

```json
{
  "success": false,
  "error": {
    "message": "Invalid IFSC code format",
    "details": {
      "errorType": "INVALID_IFSC",
      "field": "beneficiaryIFSC"
    }
  },
  "timestamp": "2026-01-23T09:50:00.000Z",
  "referenceId": "abc123-def456-ghi789"
}
```

---

## POST /disbursement/imps/status

Checks the status of a previously initiated IMPS transaction. Use this endpoint when a transaction returns `PENDING` status or to verify transaction completion.

### Request Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `tranRefNo` | string | Yes | The `tranRefNo` from the initiate response (bank's reference, not your original ref) |
| `date` | string | Yes | Transaction date in `MM/DD/YYYY` format. Used by ICICI Bank for status lookup |
| `nbfcName` | string | Yes | NBFC name for account lookup (e.g. `SOLOMON`) |
| `bankProvider` | string | Yes | Bank that processed the initiation. Accepted values: `ICICI`, `AUBANK` |
| `remitterAccountNumber` | string | Yes | Remitter account number used during initiation (with leading zeros, e.g. `"010101000"`) |

### Request Example

```json
{
    "nbfcName": "Lendix",
    "tranRefNo": "payment16756979",
    "date": "03/16/2026",
    "bankProvider": "ICICI",
    "remitterAccountNumber": "010101000"
}
```

### Success Response (200 OK)

```json
{
    "success": true,
    "data": {
        "tranRefNo": "Testing01",
        "bankRRN": "315711957867",
        "beneficiaryName": "Kiran Kumar",
        "remitterAccountNumber": "010101000",
        "status": "SUCCESS",
        "statusMessage": "BC Outward transaction Successful",
        "timestamp": "2026-03-16T07:10:39.292Z"
    },
    "vendorInfo": {
        "vendorName": "ICICI",
        "vendorPriority": 1,
        "requestId": "3855669d-5ca0-49b9-9f64-c7cbb0419c65",
        "endpoint": "IMPS_STATUS",
        "responseTimeMs": 2502,
        "calledAt": "2026-03-16T07:10:36.790Z",
        "respondedAt": "2026-03-16T07:10:39.292Z",
        "reason": "HEALTH_BASED_SELECTION",
        "vendorResponse": {
            "apiCalls": [
                {
                    "callNumber": 1,
                    "apiName": "IMPS_STATUS",
                    "response": {
                        "requestId": "1773645036885-c9e84a03",
                        "service": "UPI",
                        "encryptedKey": "<RSA-encrypted-session-key>",
                        "oaepHashingAlgorithm": "NONE",
                        "iv": "",
                        "encryptedData": "<AES-encrypted-payload>",
                        "clientInfo": "",
                        "optionalParam": ""
                    },
                    "status": "SUCCESS",
                    "duration": 541,
                    "httpStatusCode": 200,
                    "attemptNumber": 1,
                    "totalAttempts": 1
                }
            ]
        }
    },
    "sessionId": "disbursement-1773645036776-lup0gd3s3",
    "timestamp": "2026-03-16T07:10:39.303Z",
    "referenceId": "3855669d-5ca0-49b9-9f64-c7cbb0419c65"
}
```

> **AU Bank note** — a `PENDING` status check response (before final settlement) looks like:
> ```json
> {
>     "success": true,
>     "data": {
>         "tranRefNo": "",
>         "bankRRN": "",
>         "beneficiaryName": "Kiran Kumar",
>         "remitterAccountNumber": "010101000",
>         "status": "PENDING",
>         "statusMessage": "BC Outward transaction Successful",
>         "timestamp": "2026-03-25T18:21:55.517Z"
>     },
>     "vendorInfo": {
>         "vendorName": "ICICI",
>         "vendorPriority": 1,
>         "endpoint": "IMPS_STATUS",
>         "responseTimeMs": 4398,
>         "reason": "HEALTH_BASED_SELECTION"
>     }
> }
> ```
> When `tranRefNo`/`bankRRN` come back empty with `status: "PENDING"`, keep polling — the bank has not yet returned a final reference.

### Response Parameters

| Field | Type | Description |
|---|---|---|
| `success` | boolean | Overall request status |
| `data.tranRefNo` | string | Transaction reference number |
| `data.bankRRN` | string | Bank's 12-digit Reference Number |
| `data.beneficiaryName` | string | Beneficiary name as per bank records |
| `data.remitterAccountNumber` | string | Remitter bank account number used for the transaction |
| `data.status` | string | Transaction status: `SUCCESS`, `PENDING`, or `FAILED` |
| `data.statusMessage` | string | Descriptive status message from bank |
| `vendorInfo.vendorResponse` | object | Contains `ImpsResponse` with detailed transaction info including `BeneAccNo`, `BeneIFSC`, `Amount`, etc. |
| `timestamp` | string | ISO 8601 timestamp of response |
| `referenceId` | string | Unique reference ID for this request |

### cURL Example

```bash
curl -X POST 'https://api-dev.lendix.ai/disbursement/imps/status' \
  --header 'Content-Type: application/json' \
  --header 'x-api-key: YOUR_API_KEY_HERE' \
  --data '{
    "tranRefNo": "payment123456",
    "date": "01/23/2026",
    "nbfcName": "SOLOMON",
    "bankProvider": "ICICI",
    "remitterAccountNumber": "010101000"
}'
```

---

# Vendor Payment Module

Pay suppliers and vendors for outstanding bills via IMPS. Built on the same rails as Disbursement — ICICI and AU Bank, with RSA encryption, vendor pinning via `bankProvider`, and no reactive fallback. Includes two additional metadata fields: `vendorName` (the payee) and `month` (the billing period being settled).

Base URL: `https://api-dev.lendix.ai`

### Supported Vendors

| Priority | Bank | Notes |
|---|---|---|
| Primary | ICICI Bank | Primary bank for vendor payments with RSA encryption |
| Secondary | AU Bank | Secondary bank for vendor payments |

---

## POST /vendor-payment/pay

Initiate an IMPS payment to a vendor/supplier. Use `bankProvider` to select the bank — fallback is disabled; a single attempt is made per request.

### Request Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `leadId` | string | Yes | Tracking/reference identifier for the request |
| `nbfcName` | string | Yes | NBFC whose account funds the payment |
| `vendorName` | string | Yes | The payee/supplier being paid. Metadata only — validated, logged, and stored but not sent to the bank. |
| `month` | string | Yes | Billing period being settled, in `YYYY-MM` format (e.g. `2026-08`). Metadata only — not sent to the bank. |
| `bankProvider` | string | Yes | Pins the payment to a specific bank. Accepted values: `ICICI`, `AUBANK`. Fallback is disabled. |
| `remitterAccountNumber` | string | Yes | Source/debit account for the payment (include leading zeros) |
| `amount` | number | Yes | Amount in INR (subject to per-environment and per-NBFC limits) |
| `beneficiaryAccount` | string | Yes | Payee bank account number |
| `beneficiaryIFSC` | string | Yes | Payee IFSC code (11 characters) |
| `beneficiaryName` | string | Yes | Payee name as per bank account |
| `beneficiaryMobile` | string | Yes | Payee mobile number (10 digits) |
| `paymentRemarks` | string | Yes | Payment description (appears in bank statement) |
| `tranRefNo` | string | Yes | Unique reference for tracking/idempotency (1–20 alphanumeric characters) |
| `beneCode` | string | No | AU Bank beneficiary code (alphanumeric, max 25 chars). Only used when AU Bank processes the payment. |
| `idempotencyKey` | string | No | Duplicate prevention key |

### Request Example

```json
{
    "leadId": "LEAD-TEST-999",
    "vendorName": "Signzy",
    "month": "2026-06",
    "remitterAccountNumber": "1811240418267540",
    "beneficiaryAccount": "2212249339904050",
    "beneficiaryIFSC": "NPCI0000001",
    "beneficiaryName": "Kiran Kumar",
    "beneficiaryMobile": "9999988888",
    "amount": 10,
    "tranRefNo": "payment16757",
    "paymentRemarks": "FTTransferP2",
    "nbfcName": "Lendix",
    "bankProvider": "AUBANK"
}
```

### Success Response (200 OK)

```json
{
    "success": true,
    "data": {
        "tranRefNo": "payment16757",
        "bankRRN": "02160726041",
        "status": "SUCCESS",
        "statusMessage": "ACCEPTED",
        "timestamp": "2026-07-16T08:35:35.940Z"
    },
    "vendorInfo": {
        "vendorName": "AUBANK",
        "vendorPriority": 1,
        "requestId": "184c5366-5cac-476d-a95e-a10bb5b3b6ae",
        "endpoint": "VENDOR_PAYMENT_INITIATE",
        "responseTimeMs": 141,
        "calledAt": "2026-07-16T08:35:35.798Z",
        "respondedAt": "2026-07-16T08:35:35.939Z",
        "reason": "HEALTH_BASED_SELECTION",
        "logLevel": "FULL",
        "vendorResponse": {
            "apiCalls": [
                {
                    "callNumber": 1,
                    "apiName": "VENDOR_PAYMENT_INITIATE",
                    "request": { "encvalue": "<AES-encrypted-request>" },
                    "response": { "encvalue": "<AES-encrypted-response>" },
                    "status": "SUCCESS",
                    "duration": 95,
                    "httpStatusCode": 200,
                    "attemptNumber": 1,
                    "totalAttempts": 1
                }
            ]
        }
    },
    "sessionId": "vendor-payment-1784190935798-39l5if4nf",
    "timestamp": "2026-07-16T08:35:35.951Z",
    "referenceId": "184c5366-5cac-476d-a95e-a10bb5b3b6ae"
}
```

### cURL Example

```bash
curl --location 'https://api-dev.lendix.ai/vendor-payment/pay' \
  --header 'Content-Type: application/json' \
  --header 'x-api-key: YOUR_API_KEY_HERE' \
  --data '{
    "leadId": "LEAD-TEST-999",
    "vendorName": "Signzy",
    "month": "2026-06",
    "remitterAccountNumber": "1811240418267540",
    "beneficiaryAccount": "2212249339904050",
    "beneficiaryIFSC": "NPCI0000001",
    "beneficiaryName": "Kiran Kumar",
    "beneficiaryMobile": "9999988888",
    "amount": 10,
    "tranRefNo": "payment16757",
    "paymentRemarks": "FTTransferP2",
    "nbfcName": "Lendix",
    "bankProvider": "AUBANK"
}'
```

### Notes

- `vendorName` and `month` are metadata only — validated, logged, and stored but stripped before the bank call. They do not affect routing.
- Routing is pinned by `bankProvider`; reactive fallback is disabled. A single money-movement attempt is made per request.
- Per-NBFC and per-environment amount limits apply (same as Disbursement).
- Behavior, error classification, and status mapping mirror the Disbursement module.

---

## POST /vendor-payment/status

Check the status of a previously submitted vendor payment.

### Request Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `nbfcName` | string | Yes | NBFC name for account lookup |
| `tranRefNo` | string | Yes | Transaction reference from the `pay` call |
| `date` | string | Yes | Transaction date in `MM/DD/YYYY` format |
| `bankProvider` | string | Yes | Bank used for the payment. Accepted values: `ICICI`, `AUBANK` |
| `remitterAccountNumber` | string | Yes | Source account used during the `pay` call |

### Request Example

```json
{
    "nbfcName": "Lendix",
    "tranRefNo": "payment167597",
    "date": "07/16/2026",
    "bankProvider": "AUBANK",
    "remitterAccountNumber": "1811240418267540"
}
```

### Success Response (200 OK)

```json
{
    "success": true,
    "data": {
        "tranRefNo": "payment167597",
        "bankRRN": "619713295042",
        "remitterAccountNumber": "1811240418267540",
        "status": "PENDING",
        "statusMessage": "SUCCESS",
        "timestamp": "2026-07-16T08:30:57.131Z"
    },
    "vendorInfo": {
        "vendorName": "AUBANK",
        "vendorPriority": 1,
        "requestId": "57328c8f-32ef-4ead-a50a-75d40e22ce65",
        "endpoint": "VENDOR_PAYMENT_STATUS",
        "responseTimeMs": 243,
        "calledAt": "2026-07-16T08:30:56.888Z",
        "respondedAt": "2026-07-16T08:30:57.131Z",
        "reason": "HEALTH_BASED_SELECTION",
        "logLevel": "FULL",
        "vendorResponse": {
            "apiCalls": [
                {
                    "callNumber": 1,
                    "apiName": "VENDOR_PAYMENT_STATUS",
                    "request": {
                        "TransactionEnquiryRequest": {
                            "uniqueRequestId": "1784190656890",
                            "corporateCode": "23153441",
                            "transactionRequestId": "payment167597",
                            "batchNo": ""
                        }
                    },
                    "response": {
                        "TransactionEnquiryResponse": {
                            "uniqueRequestId": "1784190656890",
                            "responseCode": "00",
                            "responseMessage": "SUCCESS",
                            "transactionRequestId": "payment167597",
                            "batchNo": "02160726040",
                            "batchStatus": "SUCCESS",
                            "batchStatusCode": "00",
                            "noOfTransactions": 1,
                            "transactionEnquiryDetails": [
                                {
                                    "beneficiaryName": "Kiran Kumar",
                                    "beneficiaryAccount": "2212249339904050",
                                    "amount": "10",
                                    "paymentMethodName": "IMPS",
                                    "remitterAccountNo": "1811240418267540",
                                    "transactionRefNo": "payment167597",
                                    "valueDate": "2026-07-16",
                                    "payableCurrency": "INR",
                                    "statusCode": "101",
                                    "status": "SENTTOCORE",
                                    "errorCode": "",
                                    "errorMessage": "",
                                    "utr": "619713295042"
                                }
                            ]
                        }
                    },
                    "status": "SUCCESS",
                    "duration": 100,
                    "httpStatusCode": 200,
                    "attemptNumber": 1,
                    "totalAttempts": 1
                }
            ]
        }
    },
    "sessionId": "vendor-payment-1784190656887-mc2mqa38r",
    "timestamp": "2026-07-16T08:30:57.162Z",
    "referenceId": "57328c8f-32ef-4ead-a50a-75d40e22ce65"
}
```

### cURL Example

```bash
curl --location 'https://api-dev.lendix.ai/vendor-payment/status' \
  --header 'Content-Type: application/json' \
  --header 'x-api-key: YOUR_API_KEY_HERE' \
  --data '{
    "nbfcName": "Lendix",
    "tranRefNo": "payment167597",
    "date": "07/16/2026",
    "bankProvider": "AUBANK",
    "remitterAccountNumber": "1811240418267540"
}'
```
