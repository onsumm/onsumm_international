# Crypto Rail — API Contract (binding for the build)

Status: binding for the hackathon build · as of 29 Oct 2026 · names follow [00_DECISIONS.md](00_DECISIONS.md)

Every endpoint the crypto rail adds, with request and response shapes. Backend and frontend are built against this file. [04_LLD.md](04_LLD.md) §8 shows fuller examples of some of these; where the two differ, this file wins.

## Conventions

- All paths are under the existing FastAPI app. Auth is the existing bearer token unless marked **public**.
- Money in USDC is always a **string of integer token units** (`"3600000000"` = 3,600 USDC). Fiat amounts are strings with 2 decimals (`"3600.00"`).
- Addresses are returned checksummed. Inputs are accepted in any case.
- Timestamps are ISO 8601 UTC strings.
- Errors use one shape. A policy refusal is `403`, a bad request `400`, a missing row `404`, a conflict `409`, and an unconfigured chain `503` with `error: "CHAIN_NOT_CONFIGURED"`.

```json
{ "detail": { "error": "POLICY_REFUSED", "codes": ["WALLET_COOLING"], "messages": ["Vendor wallet is cooling until 05:00 UTC"] } }
```

(`detail` is FastAPI's `HTTPException` envelope.)

## 0. Jurisdiction (core, every tenant)

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| `GET` | `/api/tenant/entitlements` | — | existing payload plus `base_currency`, `country_code`, `is_resident_india`, `jurisdiction: Jurisdiction` |
| `GET` | `/api/tenant/jurisdiction` | — | `{jurisdiction, defaults, overrides, editable_packs}` |
| `PUT` | `/api/tenant/jurisdiction` | `{packs?: {gst?: bool \| null, ...}, locale?, fiscal_year_start_month?, tax_id_label?, indirect_tax_label?}` (admin; `null` resets a pack to default) | same as `GET` |

```text
Jurisdiction {profile: "india" | "international", country_code, base_currency, is_resident_india, locale, timezone,
              fiscal_year_start_month, tax_id_label, indirect_tax_label,
              packs: {gst, pan, tds_india, bank_india, imps, msme}, rails: ["IMPS"] | ["USDC"]}
```

## 1. Tenant onboarding and policy (`crypto_payouts`)

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| `GET` | `/api/onboarding/profile` | — | `Profile` |
| `POST` | `/api/onboarding/kyb` | `{legal_name, country_code, registration_number, tax_residency, base_currency, documents: [{name, note?}]}` | `Profile` (`kyb_status: "SUBMITTED"`) |
| `POST` | `/api/onboarding/kyb/decision` | `{decision: "APPROVED" \| "REJECTED", note?}` (admin) | `Profile` |
| `GET` | `/api/onboarding/ubos` | — | `{ubos: [Ubo]}` |
| `POST` | `/api/onboarding/ubos` | `{name, country_code, ownership_pct}` | `Ubo` (screened on save) |
| `DELETE` | `/api/onboarding/ubos/{ubo_id}` | — | `{deleted: true}` |
| `GET` | `/api/treasury/wallets` | — | `{wallets: [TreasuryWallet]}` |
| `POST` | `/api/treasury/wallets` | `{role: "TREASURY" \| "TAX", chain, address}` | `TreasuryWallet`; for a Safe, owners and threshold are read on-chain |
| `POST` | `/api/treasury/wallets/{wallet_id}/refresh` | — | `TreasuryWallet` re-read on-chain; raises `SAFE_CONFIG_CHANGED` if different |
| `GET` | `/api/tenant/rail-policy` | — | `RailPolicy` |
| `PUT` | `/api/tenant/rail-policy` | `RailPolicy` (any subset) | `RailPolicy`; `crypto_enabled: true` refused with `CRYPTO_DISABLED_RESIDENT_INDIA` for resident tenants |

```text
Profile        {tenant_id, company_name, legal_name, country_code, base_currency, jurisdiction_profile,
                is_resident_india, kyb_status, registration_number, tax_residency, crypto_enabled}
Ubo            {id, name, country_code, ownership_pct, screening_status, screened_at}
TreasuryWallet {id, role, chain, address, custody: "SAFE" | "EOA", safe_threshold, safe_owners: [address],
                status, usdc_balance_units, explorer}
RailPolicy     {crypto_enabled, allowed_chains: [str], allowed_tokens: [str], per_tx_limit_usd, daily_limit_usd,
                dual_control_above_usd, cooling_minutes, require_test_transfer}
```

## 2. Vendor wallets and contract rates (`crypto_payouts`)

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| `POST` | `/api/vendors/{supplier_id}/wallet-invites` | `{email?}` | `{invite_url, token, expires_at, emailed_to}` |
| `GET` | `/api/vendors/{supplier_id}/wallets` | — | `{wallets: [SupplierWallet]}` |
| `POST` | `/api/vendors/{supplier_id}/wallets/{wallet_id}/approve` | — | `SupplierWallet` (`ACTIVE`); refused while cooling or on a screening hit |
| `POST` | `/api/vendors/{supplier_id}/wallets/{wallet_id}/freeze` | `{reason}` | `SupplierWallet` (`FROZEN`) |
| `GET` | `/api/vendors/{supplier_id}/contract-rates` | — | `{rates: [ContractRate]}` |
| `PUT` | `/api/vendors/{supplier_id}/contract-rates` | `{rates: [ContractRate without id]}` | `{rates: [ContractRate]}` (replaces) |

```text
SupplierWallet {id, supplier_id, chain, asset, address, is_primary, status, screening_status, verified_at,
                cooling_until, approved_by, replaced_by, explorer}
ContractRate   {id, item_key, unit_price, currency_code, valid_from, valid_to}
```

## 3. Vendor portal (**public**, authenticated by the link token)

The token is an HMAC-signed `{tenant_id, supplier_id, exp}` string, valid 72 hours.

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| `GET` | `/api/vendor-portal/{token}` | — | `{supplier_name, tenant_name, chain, chain_id, expires_at, wallets: [SupplierWallet]}` |
| `GET` | `/api/vendor-portal/{token}/wallet/challenge?chain=&address=` | — | `{message, nonce, expires_at}` (EIP-4361 text) |
| `POST` | `/api/vendor-portal/{token}/wallet/verify` | `{message, signature}` | `{wallet_id, status, screening, cooling_until, needs_admin_approval}` |
| `POST` | `/api/vendor-portal/{token}/tax-docs` | `{doc_type: "W-8BEN-E" \| "TRC" \| "FORM_10F" \| "NO_PE", reference}` | `{tax_docs}` |

An expired or tampered token gets `401` with `error: "PORTAL_LINK_INVALID"`.

## 4. Payouts (`disbursement` + `crypto_payouts`)

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| `GET` | `/api/withholding/preview?invoice_id=&rail=USDC&amount=` | — | `WithholdingPreview` (`crypto_tds`) |
| `POST` | `/api/bills/{invoice_id}/disbursements` | `{rail: "USDC", amount: "3600.00"}` | `PayoutPreview` + `{otp_sent_to, demo_otp, otp_expires_at, otp_ttl_seconds}`; status `PENDING_OTP` |
| `POST` | `/api/disbursements/{disbursement_id}/verify-otp` | `{otp}` | `{disbursement_id, status: "PENDING", sub_status: "PROPOSED", safe_tx_hash, nonce}` |
| `GET` | `/api/disbursements/{disbursement_id}/safe-tx` | — | `SafeTxView` (see [04_LLD.md](04_LLD.md) §8) |
| `POST` | `/api/disbursements/{disbursement_id}/signatures` | `{signer, signature}` | `{sub_status, signatures, threshold, tx_hash?, explorer?}`; the relayer submits automatically when the threshold is met |
| `GET` | `/api/disbursements/{disbursement_id}/crypto-status` | — | `CryptoStatus` |
| `POST` | `/api/disbursements/{disbursement_id}/cancel` | `{reason}` | `CryptoStatus` (`FAILED` / `REJECTED`); only before `SUBMITTED` |

Any existing IMPS request (no `rail`, or `rail: "IMPS"`) behaves exactly as before.

```text
PayoutPreview      {disbursement_id, status, sub_status, rail: "USDC",
                    bill: {invoice_id, invoice_number, billed, approved, currency},
                    quote: {fx_snapshot_id, token_usd, usd_inr, usd_functional},
                    withholding: {rule_pack, rule_code, rate, amount_units, requires_signoff},
                    gross_units, net_units,
                    to: {supplier, address, chain}, from: {safe, threshold, owners},
                    tax_wallet?: address, estimated_gas_fee, policy: {allowed, codes, messages}}
WithholdingPreview {rule_pack, rule_code, rate, base_amount, base_currency, amount_units, net_units,
                    requires_signoff, policy: {allowed, codes, messages},
                    alternative?: {rail: "IMPS", rule_pack: "IN-195", rule_code, rate, amount, currency, note}}
CryptoStatus       {disbursement_id, invoice_id, status, sub_status, amount, currency, gross_units, net_units,
                    withholding_units, safe, safe_tx_hash, nonce, signatures: [{signer, signed_at}], threshold,
                    tx_hash, explorer, block_number, confirmations, confirmations_target, failure_reason,
                    transfers: [ChainTransfer]}
```

## 5. Bill checks (`crypto_payouts`)

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| `POST` | `/api/bills/{invoice_id}/crypto-checks` | — | `{invoice_id, findings: [{code, severity, title, details}]}`; runs `WALLET_MISMATCH` and `OVERBILLED_VS_CONTRACT` and files them in the Review Hub |

## 6. On-chain ledger (`crypto_payouts`)

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| `GET` | `/api/onchain/status` | — | `{configured, chain, chain_id, head_block, cursor_block, lag_blocks, watched_addresses: [address]}` |
| `GET` | `/api/onchain/transfers?limit=50&status=` | — | `{transfers: [ChainTransfer]}` |
| `POST` | `/api/onchain/rescan` | `{from_block?}` | `{processed, cursor_block}` (admin; runs one watcher tick and the matcher now) |
| `GET` | `/api/onchain/statements/{date}` | — | `{date, wallets: [{address, role, opening_units, inflow_units, outflow_units, closing_units_ledger, closing_units_chain, reconciled}], lines: [ChainTransfer]}` |
| `GET` | `/api/onchain/postings` | — | `{postings: [{id, source_type, source_id, target_system, external_ref, status, error, created_at}]}` |
| `GET` | `/api/onchain/postings/{posting_id}/voucher` | — | Tally voucher XML (`application/xml`) |

```text
ChainTransfer {id, chain, tx_hash, log_index, block_number, block_time, direction, from_address, to_address,
               amount_units, match_type, matched_disbursement_id, matched_invoice_id, status, confirmations,
               removed, explorer}
```

## 7. Audit anchor (`audit_anchor`)

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| `GET` | `/api/audit/log?limit=50` | — | `{rows: [{log_id, seq, table_name, entity_id, action, created_at, anchor_id}]}` |
| `GET` | `/api/audit/verify-chain` | — | `{ok, checked, first_broken_seq}` for the caller's tenant |
| `POST` | `/api/audit/anchor` | — | `Anchor` (anchors every unanchored row of every tenant) |
| `GET` | `/api/audit/anchors` | — | `{anchors: [Anchor]}` |
| `GET` | `/api/audit/proof/{log_id}` | — | `ProofBundle` |
| `GET` | `/api/public/proofs/{share_token}` | — | `ProofBundle` (**public**) |
| `GET` | `/api/public/anchors/{anchor_id}` | — | `Anchor` (**public**) |

```text
Anchor      {anchor_id, merkle_root, from_seq, to_seq, tenant_count, chain_id, mode: "calldata" | "contract",
             contract, tx_hash, explorer, status, anchored_at}
ProofBundle {version: 1, canonical, record, prev_hash, row_hash, salt, leaf, tenant_id, tenant_path: [hex],
             tenant_root, top_leaf, top_path: [hex], top_root, anchor: Anchor, share_url}
```

`canonical` is the exact UTF-8 text that was hashed: `row_hash = sha256(prev_hash_bytes || canonical_bytes)`. Leaves, nodes and the top level follow [04_LLD.md](04_LLD.md) §6g: `leaf = sha256(0x00 || salt || row_hash)`, `node = sha256(0x01 || min(a,b) || max(a,b))`, `top_leaf = sha256(0x02 || tenant_uuid_16_bytes || tenant_root)`.

### Anchor transaction format

In `calldata` mode (the hackathon default; no contract needed) the anchor wallet sends a 0-value transaction **to itself** whose input is:

```text
0x 4f4e534d            "ONSM" magic, 4 bytes
   01                  version, 1 byte
   <merkle_root>       32 bytes
   <from_seq>          8 bytes, big-endian
   <to_seq>            8 bytes, big-endian
```

In `contract` mode the same root goes to `OnsummAnchor.anchor(root, fromSeq, toSeq)`. The `/verify` page reads the transaction by hash from a public RPC and checks the root in either format.
