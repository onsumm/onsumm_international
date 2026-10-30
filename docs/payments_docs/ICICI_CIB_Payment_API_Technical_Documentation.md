# ICICI Bank — CIB Payment API

## Technical Integration Documentation (UAT)

**Product:** Corporate Internet Banking (CIB) — Payments API
**Bank:** ICICI Bank Ltd.
**Environment:** UAT (User Acceptance Testing) → Production
**Document Type:** Technical Integration Guide
**Integration Mode:** Partner / Aggregator based API integration

---

## 1. Overview

The CIB Payment API allows a registered Partner/Aggregator to initiate and track payments on behalf of their corporate clients through ICICI Bank's Corporate Internet Banking (CIBNEXT) platform. The API supports multiple payment rails (NEFT, RTGS, IMPS, ICICI-to-ICICI FT, and Virtual Account Payments), along with supporting services for registration, balance enquiry, transaction status, and bank statements.

All communication is secured through IP whitelisting and public-key certificate based encryption.

### CIBNEXT Access Links

Maintain the following link on your website for **Current Account Opening** / aggregator direct approval:

**Direct Approval path for aggregator:**

```
https://cibnext.icicibank.com/corp/AuthenticationController?FORMSGROUP_ID__=AuthenticationFG&__START_TRAN_FLAG__=Y&FG_BUTTONS__=LOAD&ACTION.LOAD=Y&AuthenticationFG.LOGIN_FLAG=1&BANK_ID=ICI&__CALL_MODE__=57&AGGR_ID=<Enter the Aggregator Id here>
```

**Short Link:**

```
https://cibnext.icicibank.com/corp/AuthenticationController?FORMSGROUP_ID__=AuthenticationFG&__START_TRAN_FLAG__=Y&FG_BUTTONS__=LOAD&ACTION.LOAD=Y&AuthenticationFG.LOGIN_FLAG=1&BANK_ID=ICI&USER_ID=&ORG_ID=&MENU_ID=25
```

---

## 2. Scope of Services

| Activity / Web Service | Purpose / Category |
|---|---|
| Registration | One-time registration |
| De-registration | One-time de-registration |
| Registration Status | Check registration status |
| Balance Fetch | Effective available balance of registered account |
| Transactional | Payment without OTP |
| Transactional Status | Payment status for all modes |
| NEFT Incremental Status | Incremental status of a particular NEFT transaction |
| Bank Statement | Bank statement of registered account |

---

## 3. Handshake & Onboarding Pre-requisites

Both **UAT** and **Production** environments require the following to be exchanged and configured before integration:

- IP address (or a range of static IP addresses)
- Client CA-signed certificate with **4096-bit public key** for encryption (in `.txt` and `.cer` format)

### 3.1 Pre-requisites Detail

| Pre-requisite | Use | Remarks |
|---|---|---|
| **IP** | Client server static IP / range of IP for API request | Client static IP or range of IP is required for whitelisting at ICICI's end. Requests are accepted **only** from whitelisted IPs. |
| **Public Key Certificate (4096-bit)** | Public key / CA-signed certificate for encryption at ICICI's end | Used at ICICI's end to encrypt the response before sending to the client. Shared by ICICI after client request. Client decrypts the response using their **Private Key**. The private key must be the one associated with the shared public key certificate. A mismatch between the public and private key will cause encryption/decryption errors. |
| **SSL Certificate** | SSL / domain secure certificate for callback communication | Used for callback communication; must be a secure domain-based certificate. |

### 3.2 Certificate Guidelines

1. **Encryption must occur at the client end** using ICICI's public key certificate (provided after configuration at ICICI's end).
2. Clients may use **OpenSSL** commands to generate the public key certificate (requires server access).
3. Certificate must be **4096-bit**, format **X.509**, with extension `.cer`, `.crt`, or `.txt`.
4. Certificate content must begin with `-----BEGIN CERTIFICATE-----` and end with `-----END CERTIFICATE-----`.

**Self-test at client end:** Encrypt a sample request payload using your **public key** (the one shared with ICICI), then decrypt the same payload using your **private key**. If this round-trip works, the public/private key pair is valid.

---

## 4. Payment (Transactional) API

This is the core payment initiation service. It processes payments without OTP for a registered user.

### 4.1 Request Parameters

| Parameter | Datatype | Length | Mandatory | Remarks |
|---|---|---|:---:|---|
| `AGGRID` | NVARCHAR2(100) | 100 | Y | Partner / Aggregator ID |
| `AGGRNAME` | NVARCHAR2(32) | 32 | Y | Aggregator name |
| `CORPID` | NVARCHAR2(32) | 32 | Y | Corporate ID |
| `USERID` | NVARCHAR2(32) | 32 | Y | User ID |
| `URN` | NVARCHAR2(40) | 40 | Y | Same as passed in the registration request by Partner |
| `UNIQUEID` | NVARCHAR2(40) | 40 | Y | Reconciliation is done on the **first 15 characters** of the Unique ID (bank statement debit narration captures the first 15 characters) |
| `DEBITACC` | NVARCHAR2(34) | 34 | Y | Debit account number |
| `CREDITACC` | NVARCHAR2(34) | 34 | Y | Credit / beneficiary account number |
| `IFSC` | NVARCHAR2(32) | 32 | Y | For all ICICI FT, IFSC must be static `ICIC0000011`. For Virtual Account Payments (VAP), IFSC must be `ICIC0000103`, `ICIC0000104`, or `ICIC0000106` |
| `AMOUNT` | NUMBER(18,3) | 18 (up to 2 decimals) | Y | Transaction amount |
| `CURRENCY` | NVARCHAR2(3) | 3 | Y | `INR` |
| `TXNTYPE` | CHAR(11) | 11 | Y | See transaction type mapping below |
| `PAYEENAME` | NVARCHAR2(80) | 80 | Y | Special characters not allowed |
| `REMARKS` | NVARCHAR2(255) | 255 | N | Not mandatory; special characters not allowed. For NEFT, a maximum of **32 characters** is allowed |
| `WORKFLOW_REQD` | CHAR(1) | 1 | N | Mandatory when bypassing the transaction workflow (no approval required) |
| `BENLEI` | NVARCHAR2(20) | 20 | N | Beneficiary LEI. Required for **non-individual beneficiary** for NEFT & RTGS above **INR 50 Cr** |

### 4.2 Transaction Type (`TXNTYPE`) Mapping

| Value | Payment Mode |
|---|---|
| `RTG` | RTGS |
| `RGS` | NEFT |
| `IFS` | IMPS |
| `TPA` | All ICICI-to-ICICI transfers |
| `VAP` | Virtual Account Payments |

**Virtual Account Payment (VAP) rules:**
- `TXNTYPE` should be `VAP` and `RGS`.
- IFSC should be `ICIC0000103`, `ICIC0000104`, or `ICIC0000106`, depending on the client codes created for the virtual account number based collection service (communicated during setup).
- **IMPS & RTGS transactions are NOT allowed for virtual payments.**

### 4.3 Success Response — UAT

| Parameter | Datatype | Remarks |
|---|---|---|
| `URN` | NVARCHAR2(40) | |
| `UNIQUEID` | NVARCHAR2(40) | |
| `REQID` | NVARCHAR2(11) | |
| `UTRNUMBER` | NVARCHAR2(32) | New tag. Not returned for Failure / Pending-for-approval cases |
| `STATUS` | NVARCHAR2(20) | |
| `RESPONSE` | CHAR(10) | Value set: `SUCCESS` / `FAILURE` only |

### 4.4 Success Response — Live (Production)

| Parameter | Datatype |
|---|---|
| `AGGR_ID` | NVARCHAR2(100) |
| `AGGR_NAME` | NVARCHAR2(32) |
| `CORP_ID` | NVARCHAR2(32) |
| `USER_ID` | NVARCHAR2(32) |
| `URN` | NVARCHAR2(40) |
| `UNIQUEID` | NVARCHAR2(40) |
| `UTRNUMBER` | NVARCHAR2(32) (New tag) |
| `REQID` | NVARCHAR2(11) |
| `STATUS` | NVARCHAR2(20) |
| `RESPONSE` | CHAR(10) |

### 4.5 Failure Response

| Parameter | Datatype |
|---|---|
| `RESPONSECODE` | NUMBER(40) |
| `ERRORCODE` | NUMBER(40) |
| `MESSAGE` | NVARCHAR2(255) |
| `STATUS` | NVARCHAR2(20) |
| `RESPONSE` | CHAR(10) |

---

## 5. Status Values (Payment & Status APIs)

| STATUS | Meaning |
|---|---|
| `SUCCESS` | Transaction got processed |
| `FAILURE` | Transaction not processed |
| `PENDING` | Transaction is SUSPECT. Try inquiry after 2 hours to get the final terminal status |
| `Pending For Processing` | Transaction initiated after cut-off (NEFT & RTGS only) |
| `Pending For approval` | Transaction went for approval |
| `DUPLICATE` | Same Unique ID used for a 2nd transaction (repetition of an already-used Unique ID) |
| `Processing` | Transaction done post cut-off & status check done during cut-off time |
| `Reversed` | RTGS transaction reversed within 2 hours |
| `REVERSED` (NEFT) | NEFT transaction rejected while processing; auto-reversed to client's account within 24 hrs |
| `UNCERTAIN` | Applicable for FT, NEFT & RTGS. Status could not be retrieved. **Do not re-initiate if amount is debited** |

### 5.1 IMPS-Specific Note

For an IMPS transaction, if **no RRN number is generated** (RRN can be checked in the status inquiry API) and there is **no debit for 2 hrs**, consider the transaction as **failed**.

---

## 6. Response vs. Status Interpretation Logic

> Always refer to the API `RESPONSE` field before arriving at the terminal status.

| Condition | Action |
|---|---|
| `RESPONSE = FAILURE` | The status holds no significance — the service itself could not respond to the request |
| `RESPONSE = FAILURE` & `STATUS = Pending` | Check the inquiry API as per the message reflected in `RESPONSE`. For IMPS, status remains pending; if account is debited, write to ICICI. If not debited within 30 mins of initiation, consider it a failure |
| `RESPONSE = Success` & `STATUS = Pending` / `Pending For Processing` | Re-initiate the inquiry service |
| `RESPONSE = Success` & `STATUS = Success` | Terminal state reached; no further action required |
| `RESPONSE = Success` & `STATUS = Failure` | Re-initiate the transaction |
| API gateway error (no `STATUS` tag available) | Re-initiate the inquiry service |

---

## 7. Transaction Status API

Used to fetch the status of a transaction performed in E-Banking.

### 7.1 Request Parameters

| Parameter | Datatype | Length | Mandatory |
|---|---|---|:---:|
| `AGGRID` | NVARCHAR2(100) | 100 | Y |
| `CORPID` | NVARCHAR2(32) | 32 | Y |
| `USERID` | NVARCHAR2(32) | 32 | Y |
| `UNIQUEID` | NVARCHAR2(40) | 40 | Y |
| `URN` | NVARCHAR2(40) | 40 | Y |

### 7.2 Success Response

| Parameter | Datatype | Remarks |
|---|---|---|
| `URN` | NVARCHAR2(40) | |
| `UNIQUEID` | NVARCHAR2(40) | |
| `STATUS` | NVARCHAR2(20) | |
| `RESPONSE` | CHAR(10) | Value set: `SUCCESS` / `FAILURE` only |
| `UTRNUMBER` | NVARCHAR2(32) | |

### 7.3 Failure Response

| Parameter | Datatype | Remarks |
|---|---|---|
| `MESSAGE` | NVARCHAR2(255) | |
| `STATUS` | NVARCHAR2(20) | New tag |
| `RESPONSE` | CHAR(10) | |

### 7.4 Behaviour Notes

- CORP ID & USER ID are **not** passed; the `URN` is used to establish the linkage.
- This service returns the transaction status for a transaction done in E-Banking.
- `USERID` is validated for whether it is registered.
- The transaction status is returned as output.
- Response for incorrect input packet details is returned in the `RESPONSE` field.

---

## 8. Error Messages (Payment API)

| Scenario | Message | RESPONSE |
|---|---|---|
| Transaction Success (without workflow) | "Transaction with reference id XXXX processed successfully." | SUCCESS |
| Transaction Success (with workflow) | "Transaction with reference id XXXX submitted successfully and is pending for Processing" | SUCCESS |
| Invalid Corp ID / User ID / Partner ID | "Invalid Corp Id or User Id or Partner Id is passed" | FAILURE |
| Unregistered User | "User is not registered" | FAILURE |
| Debit account not passed | "Debit Account is mandatory" | FAILURE |
| Invalid debit account passed | "Debit Account is invalid" | FAILURE |
| Credit account not passed | "Credit Account is mandatory" | FAILURE |
| IFSC code not passed | "Bank Identifier is mandatory for adhoc payment" | FAILURE |
| Amount not passed | "Amount not entered" | FAILURE |
| Currency not passed | "Invalid Currency" | FAILURE |
| Invalid txn date format | "Entered Date Format is Invalid" | FAILURE |
| Txn type not passed | "Transaction Type is mandatory" | FAILURE |
| Payee name not passed | "Payee name is mandatory" | FAILURE |
| Partner ID not passed | "Invalid Corp Id or User Id or Partner Id is passed" | FAILURE |
| REQUESTFROM / REQUESTTYPE not passed | "Invalid Partner Request" | FAILURE |
| Special characters in BENE LEI | "Field can contain numbers or alphabets." | FAILURE |
| 20 characters not passed in BENE LEI | "Please enter a valid 20 digit Alphanumeric Beneficiary LEI." | FAILURE |
| Remitter LEI not available / not 20 chars / expired | "Large value transaction of more than INR 500000000 can't be processed as Legal entity identifier is not available/expired as per bank records. Kindly visit nearest ICICI Bank branch to update the same in your account" | FAILURE |

---

## 9. Transaction Processing & Workflow Behaviour

- The E-Banking application validates whether the user is registered. If not registered, a **failure** is returned.
- Once the transaction request is processed, a reference number is sent to the Partner. If approvals are pending per the associated workflows, the message indicates approvals are pending.
- The workflow for the user is the same as those configured at CIB.
- If a workflow is associated, the transaction is available in CIB for approvers to approve. Approvers must log in to the E-Banking application to approve.
- If the user does not receive a response from CIB, the same request may be fired again — CIB returns the reference ID with the appropriate status. **Duplicate transactions will not be posted.**
- The transaction flow follows the configurations in E-Banking (network properties, network maintenance, debit/credit account number validations, transaction type validations) for the initiating user.
- Transaction status is provided either through a Transaction Status Update batch or the Transaction Inquiry web service.

### 9.1 STP vs. Non-STP Processing Response

| Workflow | NEFT | RTGS | FT | IMPS | Remarks |
|---|---|---|---|---|---|
| **STP** | Synchronous | Synchronous | Synchronous | Synchronous | Responses are only for the account debit. If the txn fails at RBI/NPCI/destination bank, the account is credited back manually; however, CIB is **not** updated and will continue to show success |
| **NON-STP** | Asynchronous | Asynchronous | Asynchronous | Asynchronous | |

---

## 10. Sample Request Packet

> Highlighted values are constant.

```json
{
  "CORPID": "<corp id>",
  "USERID": "<user id>",
  "DEBITACC": "<Debit acc no.>",
  "CREDITACC": "<Credit Acc no.>",
  "IFSC": "<IFSC code>",
  "AMOUNT": "<Amount>",
  "CURRENCY": "<currency>",
  "TXNTYPE": "<Payment type>",
  "REMARKS": "<Remarks>",
  "AGGRID": "<Partnerid>",
  "PAYEENAME": "<payee name>",
  "UNIQUEID": "<Aggreg. Unique ID>",
  "URN": "<URN>"
}
```

---

## 11. Integration Checklist (UAT)

1. Share static IP / IP range with ICICI for whitelisting.
2. Generate a 4096-bit X.509 public/private key pair (OpenSSL) and share the public certificate (`.cer` / `.crt` / `.txt`).
3. Obtain ICICI's public key certificate for encrypting request payloads.
4. Provide SSL/domain certificate for callback communication.
5. Complete one-time **Registration** and note the `URN`.
6. Verify **Registration Status**.
7. Test **Balance Fetch** on the registered account.
8. Test **Payment** initiation for each required mode (NEFT/RTGS/IMPS/FT/VAP).
9. Implement **Transaction Status** inquiry with correct Response/Status interpretation logic (Section 6).
10. Implement retry logic: re-initiate inquiry on `Pending` / `Pending For Processing`; re-initiate transaction on `Success + Failure`; never re-initiate when amount is debited under `UNCERTAIN`.
11. Validate reconciliation on the **first 15 characters** of `UNIQUEID`.

---

## 12. Key Reminders

- **Reconciliation** is performed on the first 15 characters of the Unique ID.
- **Duplicate Unique IDs** are rejected (`DUPLICATE`) — never reuse a Unique ID.
- For **PENDING / SUSPECT** transactions, retry inquiry only after **2 hours**.
- Under `UNCERTAIN` status (FT/NEFT/RTGS), **do not re-initiate** if the amount is debited.
- ICICI FT requires static IFSC `ICIC0000011`; VAP requires `ICIC0000103` / `ICIC0000104` / `ICIC0000106`.
- IMPS & RTGS are not permitted for virtual account payments.
- NEFT `REMARKS` are capped at 32 characters; special characters are not allowed in `PAYEENAME` or `REMARKS`.

---

*This document is for technical integration reference for the ICICI Bank CIB Payment API (UAT). Field definitions, IFSC constants, cut-off behaviour, and status semantics are as provided in the API specification.*
