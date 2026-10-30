# Full Database Schema Documentation

## Documentation Objective
This document reflects the live PostgreSQL (AWS RDS) schema as of the last schema pull. It covers all public tables, enum types, columns, keys, constraints, and indexes.

---

## Enum Types

### `account_type`
Allowed values:
- `ASSET`
- `LIABILITY`
- `INCOME`
- `EXPENSE`
- `EQUITY`

### `audit_action`
Allowed values:
- `INSERT`
- `UPDATE`
- `DELETE`

### `bank_account_type`
Allowed values:
- `CURRENT`
- `SAVINGS`
- `OVERDRAFT`

### `bill_verification_flag`
Allowed values:
- `GREEN`
- `YELLOW`
- `RED`

### `billing_discrepancy_status`
Allowed values:
- `OPEN`
- `ACKNOWLEDGED`
- `RESOLVED`
- `DISPUTED_WITH_VENDOR`
- `IGNORED`

### `billing_discrepancy_type`
Allowed values:
- `QTY_MISMATCH`
- `RATE_MISMATCH`
- `COST_MISMATCH`
- `MISSING_IN_NOVA`
- `MISSING_IN_INVOICE`
- `UNMAPPED_LINE_ITEM`
- `ENTITY_UNRESOLVED`
- `NOVA_SNAPSHOT_UNAVAILABLE`
- `NOVA_SELF_INCONSISTENT`
- `RATE_CHANGED_MOM`

### `billing_reconciliation_status`
Allowed values:
- `PENDING`
- `RUNNING`
- `COMPLETED`
- `COMPLETED_WITH_DISCREPANCIES`
- `FAILED`

### `conflict_resolution`
Allowed values:
- `OUTBOUND_WRITE`
- `SKIP`
- `LOG_CONFLICT`

### `connection_status`
Allowed values:
- `ACTIVE`
- `PAUSED`
- `ERROR`
- `DISCONNECTED`
- `PENDING`
- `REVOKED`

### `data_source`
Allowed values:
- `TALLY`
- `ZOHO`
- `QBO`
- `XERO`
- `FRESHBOOKS`
- `SAGE`
- `MYOB`
- `WAVE`
- `RAZORPAY`
- `STRIPE`
- `UPLOAD`
- `MANUAL`
- `GENERIC_REST`
- `SAP`

### `entity_type`
Allowed values:
- `INVOICE`
- `PAYMENT`
- `BILL`
- `CREDIT_NOTE`
- `CUSTOMER`
- `SUPPLIER`
- `JOURNAL`

### `invoice_status`
Allowed values:
- `DRAFT`
- `SENT`
- `PARTIAL`
- `PAID`
- `OVERDUE`
- `VOID`
- `PENDING_APPROVER`
- `PENDING_ACCOUNTANT`
- `PENDING_FC`
- `NEEDS_REVISION`
- `PENDING`
- `APPROVED`
- `REJECTED`
- `CANCELLED`

### `nudge_channel`
Allowed values:
- `EMAIL`
- `SMS`
- `WHATSAPP`
- `SLACK`
- `IN_APP`

### `nudge_status`
Allowed values:
- `PENDING`
- `SENT`
- `FAILED`
- `CANCELLED`

### `ocr_engine`
Allowed values:
- `PADDLEOCR`
- `SERVAM`
- `MERGED`
- `NONE`

### `payment_cycle_type`
Allowed values:
- `PAYABLE`
- `RECEIVABLE`

### `payment_method`
Allowed values:
- `BANK_TRANSFER`
- `UPI`
- `CARD`
- `CHEQUE`
- `CASH`
- `GATEWAY`

### `payment_status`
Allowed values:
- `PENDING`
- `CLEARED`
- `BOUNCED`
- `REFUNDED`

### `plan_tier`
Allowed values:
- `FREE`
- `STARTER`
- `GROWTH`
- `ENTERPRISE`

### `risk_bucket`
Allowed values:
- `LOW`
- `MEDIUM`
- `HIGH`
- `CRITICAL`

### `schedule_status`
Allowed values:
- `PENDING`
- `PARTIAL`
- `PAID`
- `OVERDUE`
- `WAIVED`
- `DISPUTED`

### `severity_level`
Allowed values:
- `LOW`
- `MEDIUM`
- `HIGH`
- `CRITICAL`

### `supplier_status`
Allowed values:
- `ACTIVE`
- `SUSPENDED`
- `CHURNED`

### `sync_job_status`
Allowed values:
- `RUNNING`
- `COMPLETED`
- `FAILED`
- `PARTIAL`

### `sync_status`
Allowed values:
- `PENDING`
- `SYNCED`
- `ERROR`

### `tally_mode`
Allowed values:
- `TDL_HTTP`
- `XML_SOCKET`
- `ODBC`

### `tax_type`
Allowed values:
- `GST`
- `IGST`
- `CGST`
- `SGST`
- `CESS`
- `VAT`
- `OTHER`

### `txn_status`
Allowed values:
- `INITIATED`
- `PROCESSING`
- `COMPLETED`
- `FAILED`
- `BOUNCED`
- `REVERSED`
- `CANCELLED`

### `upload_file_type`
Allowed values:
- `PDF`
- `PNG`
- `JPG`
- `TIFF`
- `XLSX`
- `CSV`

### `upload_status`
Allowed values:
- `RECEIVED`
- `PREPROCESSING`
- `OCR_PRIMARY`
- `OCR_FALLBACK`
- `VALIDATING`
- `DONE`
- `ERROR`
- `PREPROCESS_ERROR`

### `validation_flag`
Allowed values:
- `OK`
- `MISMATCH`
- `DUPLICATE`
- `REVIEW`

### `wf_entity_type`
Allowed values:
- `INVOICE`
- `CUSTOMER`
- `PAYMENT`
- `SUPPLIER`
- `BILL`
- `BILLING_DISCREPANCY`

### `wf_task_status`
Allowed values:
- `PENDING`
- `IN_PROGRESS`
- `DONE`
- `SKIPPED`

### `wf_task_type`
Allowed values:
- `NUDGE`
- `ESCALATE`
- `APPROVE`
- `REVIEW`
- `ALERT`

---

## Table: `agent_draft_bills`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `supplier_id` | `uuid` | `YES` | `NULL` |
| `contact_id` | `uuid` | `YES` | `NULL` |
| `thread_id` | `text` | `YES` | `NULL` |
| `message_gmail_id` | `text` | `YES` | `NULL` |
| `from_email` | `text` | `YES` | `NULL` |
| `subject` | `text` | `YES` | `NULL` |
| `source_file` | `text` | `NO` | `NULL` |
| `ocr_payload` | `jsonb` | `NO` | `NULL` |
| `status` | `text` | `NO` | `'pending'::text` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `analysis` | `text` | `YES` | `NULL` |
| `suggestion` | `text` | `YES` | `NULL` |
| `related_invoice` | `text` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:** none
**Indexes:**
- `agent_draft_bills_pkey`: `CREATE UNIQUE INDEX agent_draft_bills_pkey ON public.agent_draft_bills USING btree (id)`
- `idx_adb_tenant_status`: `CREATE INDEX idx_adb_tenant_status ON public.agent_draft_bills USING btree (tenant_id, status)`
- `idx_adb_thread`: `CREATE INDEX idx_adb_thread ON public.agent_draft_bills USING btree (tenant_id, thread_id)`

---

## Table: `agent_email_events`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `supplier_id` | `uuid` | `YES` | `NULL` |
| `contact_id` | `uuid` | `YES` | `NULL` |
| `message_gmail_id` | `text` | `NO` | `NULL` |
| `thread_id` | `text` | `YES` | `NULL` |
| `from_email` | `text` | `YES` | `NULL` |
| `subject` | `text` | `YES` | `NULL` |
| `action` | `text` | `NO` | `NULL` |
| `detail` | `text` | `YES` | `NULL` |
| `draft_id` | `uuid` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:**
- `agent_email_events_tenant_id_message_gmail_id_key`: (`tenant_id`, `message_gmail_id`)
**Indexes:**
- `agent_email_events_pkey`: `CREATE UNIQUE INDEX agent_email_events_pkey ON public.agent_email_events USING btree (id)`
- `agent_email_events_tenant_id_message_gmail_id_key`: `CREATE UNIQUE INDEX agent_email_events_tenant_id_message_gmail_id_key ON public.agent_email_events USING btree (tenant_id, message_gmail_id)`
- `idx_aee_tenant_time`: `CREATE INDEX idx_aee_tenant_time ON public.agent_email_events USING btree (tenant_id, created_at DESC)`

---

## Table: `agent_settings`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `enabled` | `boolean` | `NO` | `true` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `tenant_id`
**Foreign keys:**
none
**Unique constraints:** none
**Indexes:**
- `agent_settings_pkey`: `CREATE UNIQUE INDEX agent_settings_pkey ON public.agent_settings USING btree (tenant_id)`

---

## Table: `ai_anomalies`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `anomaly_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `entity_type` | `entity_type` | `NO` | `NULL` |
| `entity_id` | `uuid` | `NO` | `NULL` |
| `anomaly_type` | `text` | `NO` | `NULL` |
| `severity` | `severity_level` | `NO` | `NULL` |
| `description` | `text` | `YES` | `NULL` |
| `features_jsonb` | `jsonb` | `NO` | `'{}'::jsonb` |
| `is_resolved` | `boolean` | `NO` | `false` |
| `resolved_by` | `uuid` | `YES` | `NULL` |
| `resolved_at` | `timestamp with time zone` | `YES` | `NULL` |
| `detected_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `anomaly_id`
**Foreign keys:**
- `resolved_by` -> `org_users.user_id` (ON DELETE SET NULL)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
**Unique constraints:** none
**Indexes:**
- `ai_anomalies_pkey`: `CREATE UNIQUE INDEX ai_anomalies_pkey ON public.ai_anomalies USING btree (anomaly_id)`

---

## Table: `ai_payment_predictions`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `prediction_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `stg_invoice_id` | `uuid` | `NO` | `NULL` |
| `predicted_pay_date` | `date` | `YES` | `NULL` |
| `predicted_pay_date_confidence` | `numeric` | `YES` | `NULL` |
| `predicted_amount` | `numeric` | `YES` | `NULL` |
| `amount_confidence` | `numeric` | `YES` | `NULL` |
| `model_version` | `text` | `NO` | `NULL` |
| `features_jsonb` | `jsonb` | `NO` | `'{}'::jsonb` |
| `computed_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `prediction_id`
**Foreign keys:**
- `stg_invoice_id` -> `stg_invoices.stg_invoice_id` (ON DELETE RESTRICT)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
**Unique constraints:** none
**Indexes:**
- `ai_payment_predictions_pkey`: `CREATE UNIQUE INDEX ai_payment_predictions_pkey ON public.ai_payment_predictions USING btree (prediction_id)`

---

## Table: `ai_risk_scores`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `score_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `end_customer_id` | `uuid` | `NO` | `NULL` |
| `score` | `numeric` | `NO` | `NULL` |
| `risk_bucket` | `risk_bucket` | `NO` | `NULL` |
| `model_version` | `text` | `NO` | `NULL` |
| `features_jsonb` | `jsonb` | `NO` | `NULL` |
| `explanation_jsonb` | `jsonb` | `NO` | `'{}'::jsonb` |
| `computed_at` | `timestamp with time zone` | `NO` | `now()` |
| `valid_until` | `timestamp with time zone` | `YES` | `NULL` |
| `is_current` | `boolean` | `NO` | `true` |

### Keys and Constraints
**Primary keys:** `score_id`
**Foreign keys:**
- `end_customer_id` -> `dim_end_customers.end_customer_id` (ON DELETE RESTRICT)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
**Unique constraints:** none
**Indexes:**
- `ai_risk_scores_pkey`: `CREATE UNIQUE INDEX ai_risk_scores_pkey ON public.ai_risk_scores USING btree (score_id)`
- `idx_ai_risk_bucket`: `CREATE INDEX idx_ai_risk_bucket ON public.ai_risk_scores USING btree (risk_bucket)`
- `idx_ai_risk_current`: `CREATE INDEX idx_ai_risk_current ON public.ai_risk_scores USING btree (tenant_id, end_customer_id, is_current)`

---

## Table: `auth_refresh_tokens`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `token_hash` | `text` | `NO` | `NULL` |
| `username` | `text` | `NO` | `NULL` |
| `company_id` | `uuid` | `NO` | `NULL` |
| `issued_at` | `timestamp with time zone` | `NO` | `now()` |
| `expires_at` | `timestamp with time zone` | `NO` | `NULL` |
| `revoked` | `boolean` | `NO` | `false` |
| `revoked_at` | `timestamp with time zone` | `YES` | `NULL` |
| `replaced_by` | `text` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:**
- `auth_refresh_tokens_token_hash_key`: (`token_hash`)
**Indexes:**
- `auth_refresh_tokens_pkey`: `CREATE UNIQUE INDEX auth_refresh_tokens_pkey ON public.auth_refresh_tokens USING btree (id)`
- `auth_refresh_tokens_token_hash_key`: `CREATE UNIQUE INDEX auth_refresh_tokens_token_hash_key ON public.auth_refresh_tokens USING btree (token_hash)`
- `idx_refresh_username`: `CREATE INDEX idx_refresh_username ON public.auth_refresh_tokens USING btree (username)`

---

## Table: `bill_approval_chain`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `bigint` | `NO` | `nextval('bill_approval_chain_id_seq'::regclass)` |
| `invoice_id` | `uuid` | `NO` | `NULL` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `step` | `smallint` | `NO` | `NULL` |
| `actor_role` | `character varying` | `YES` | `NULL` |
| `actor_name` | `character varying` | `YES` | `NULL` |
| `action` | `character varying` | `NO` | `NULL` |
| `comment` | `text` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `YES` | `now()` |
| `target_step` | `integer` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:** none
**Indexes:**
- `bill_approval_chain_pkey`: `CREATE UNIQUE INDEX bill_approval_chain_pkey ON public.bill_approval_chain USING btree (id)`

---

## Table: `bill_attachment_edits`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `attachment_id` | `uuid` | `NO` | `NULL` |
| `invoice_id` | `uuid` | `NO` | `NULL` |
| `edited_by` | `character varying` | `YES` | `NULL` |
| `edited_at` | `timestamp with time zone` | `YES` | `now()` |
| `diff_json` | `jsonb` | `NO` | `NULL` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:** none
**Indexes:**
- `bill_attachment_edits_pkey`: `CREATE UNIQUE INDEX bill_attachment_edits_pkey ON public.bill_attachment_edits USING btree (id)`

---

## Table: `bill_attachments`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `invoice_id` | `uuid` | `NO` | `NULL` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `file_path` | `text` | `NO` | `NULL` |
| `file_name` | `text` | `NO` | `NULL` |
| `file_type` | `text` | `YES` | `NULL` |
| `file_size` | `bigint` | `YES` | `NULL` |
| `uploaded_by` | `text` | `YES` | `NULL` |
| `uploaded_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:** none
**Indexes:**
- `bill_attachments_pkey`: `CREATE UNIQUE INDEX bill_attachments_pkey ON public.bill_attachments USING btree (id)`
- `idx_bill_attachments_invoice`: `CREATE INDEX idx_bill_attachments_invoice ON public.bill_attachments USING btree (invoice_id)`

---

## Table: `billing_bill_reconciliation`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `invoice_id` | `uuid` | `NO` | `NULL` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `result` | `jsonb` | `NO` | `NULL` |
| `computed_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `invoice_id`
**Foreign keys:**
- `invoice_id` -> `fact_invoices.invoice_id` (ON DELETE CASCADE)
**Unique constraints:** none
**Indexes:**
- `billing_bill_reconciliation_pkey`: `CREATE UNIQUE INDEX billing_bill_reconciliation_pkey ON public.billing_bill_reconciliation USING btree (invoice_id)`

---

## Table: `billing_nova_usage_lines`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `usage_line_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `snapshot_id` | `uuid` | `NO` | `NULL` |
| `nova_usage_slug` | `text` | `NO` | `NULL` |
| `api_call_count` | `integer` | `NO` | `NULL` |
| `billable_operations` | `integer` | `NO` | `NULL` |
| `cost_per_operation` | `numeric` | `NO` | `NULL` |
| `total_cost_rupees` | `numeric` | `NO` | `NULL` |

### Keys and Constraints
**Primary keys:** `usage_line_id`
**Foreign keys:**
- `snapshot_id` -> `billing_nova_usage_snapshots.snapshot_id` (ON DELETE CASCADE)
**Unique constraints:**
- `billing_nova_usage_lines_snapshot_id_nova_usage_slug_key`: (`snapshot_id`, `nova_usage_slug`)
**Indexes:**
- `billing_nova_usage_lines_pkey`: `CREATE UNIQUE INDEX billing_nova_usage_lines_pkey ON public.billing_nova_usage_lines USING btree (usage_line_id)`
- `billing_nova_usage_lines_snapshot_id_nova_usage_slug_key`: `CREATE UNIQUE INDEX billing_nova_usage_lines_snapshot_id_nova_usage_slug_key ON public.billing_nova_usage_lines USING btree (snapshot_id, nova_usage_slug)`

---

## Table: `billing_nova_usage_snapshots`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `snapshot_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `vendor_name` | `text` | `NO` | `NULL` |
| `billing_month` | `text` | `NO` | `NULL` |
| `total_billable_operations` | `integer` | `NO` | `NULL` |
| `total_cost_rupees` | `numeric` | `NO` | `NULL` |
| `raw_response_jsonb` | `jsonb` | `NO` | `NULL` |
| `nova_reference_id` | `text` | `YES` | `NULL` |
| `fetched_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `snapshot_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
**Unique constraints:**
- `billing_nova_usage_snapshots_tenant_id_vendor_name_billing__key`: (`tenant_id`, `vendor_name`, `billing_month`)
**Indexes:**
- `billing_nova_usage_snapshots_pkey`: `CREATE UNIQUE INDEX billing_nova_usage_snapshots_pkey ON public.billing_nova_usage_snapshots USING btree (snapshot_id)`
- `billing_nova_usage_snapshots_tenant_id_vendor_name_billing__key`: `CREATE UNIQUE INDEX billing_nova_usage_snapshots_tenant_id_vendor_name_billing__key ON public.billing_nova_usage_snapshots USING btree (tenant_id, vendor_name, billing_month)`

---

## Table: `billing_reconciliation_discrepancies`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `discrepancy_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `run_id` | `uuid` | `NO` | `NULL` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `nova_module` | `text` | `YES` | `NULL` |
| `discrepancy_type` | `billing_discrepancy_type` | `NO` | `NULL` |
| `severity` | `severity_level` | `NO` | `NULL` |
| `invoice_value` | `jsonb` | `YES` | `NULL` |
| `nova_value` | `jsonb` | `YES` | `NULL` |
| `variance_pct` | `numeric` | `YES` | `NULL` |
| `variance_amount` | `numeric` | `YES` | `NULL` |
| `status` | `billing_discrepancy_status` | `NO` | `'OPEN'::billing_discrepancy_status` |
| `resolution_note` | `text` | `YES` | `NULL` |
| `resolved_by` | `uuid` | `YES` | `NULL` |
| `resolved_at` | `timestamp with time zone` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `discrepancy_id`
**Foreign keys:**
- `resolved_by` -> `org_users.user_id` (ON DELETE NO ACTION)
- `run_id` -> `billing_reconciliation_runs.run_id` (ON DELETE CASCADE)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
**Unique constraints:** none
**Indexes:**
- `billing_reconciliation_discrepancies_pkey`: `CREATE UNIQUE INDEX billing_reconciliation_discrepancies_pkey ON public.billing_reconciliation_discrepancies USING btree (discrepancy_id)`
- `idx_billing_disc_run`: `CREATE INDEX idx_billing_disc_run ON public.billing_reconciliation_discrepancies USING btree (run_id)`
- `idx_billing_disc_tenant_severity`: `CREATE INDEX idx_billing_disc_tenant_severity ON public.billing_reconciliation_discrepancies USING btree (tenant_id, severity)`
- `idx_billing_disc_tenant_status`: `CREATE INDEX idx_billing_disc_tenant_status ON public.billing_reconciliation_discrepancies USING btree (tenant_id, status)`

---

## Table: `billing_reconciliation_runs`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `run_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `vendor_name` | `text` | `NO` | `NULL` |
| `billing_month` | `text` | `NO` | `NULL` |
| `vendor_invoice_id` | `uuid` | `YES` | `NULL` |
| `snapshot_id` | `uuid` | `YES` | `NULL` |
| `status` | `billing_reconciliation_status` | `NO` | `'PENDING'::billing_reconciliation_status` |
| `total_invoice_amount` | `numeric` | `YES` | `NULL` |
| `total_nova_cost` | `numeric` | `YES` | `NULL` |
| `variance_amount` | `numeric` | `YES` | `NULL` |
| `started_at` | `timestamp with time zone` | `NO` | `now()` |
| `completed_at` | `timestamp with time zone` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `run_id`
**Foreign keys:**
- `snapshot_id` -> `billing_nova_usage_snapshots.snapshot_id` (ON DELETE NO ACTION)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
- `vendor_invoice_id` -> `billing_vendor_invoices.vendor_invoice_id` (ON DELETE NO ACTION)
**Unique constraints:**
- `billing_reconciliation_runs_tenant_vendor_month_key`: (`tenant_id`, `vendor_name`, `billing_month`)
**Indexes:**
- `billing_reconciliation_runs_pkey`: `CREATE UNIQUE INDEX billing_reconciliation_runs_pkey ON public.billing_reconciliation_runs USING btree (run_id)`
- `billing_reconciliation_runs_tenant_vendor_month_key`: `CREATE UNIQUE INDEX billing_reconciliation_runs_tenant_vendor_month_key ON public.billing_reconciliation_runs USING btree (tenant_id, vendor_name, billing_month)`

---

## Table: `billing_vendor_accounts`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `vendor_account_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `vendor_name` | `text` | `NO` | `NULL` |
| `legal_entity_name` | `text` | `NO` | `NULL` |
| `gstin` | `character` | `YES` | `NULL` |
| `nova_sub_account_id` | `text` | `YES` | `NULL` |
| `nova_api_key_ref` | `text` | `NO` | `NULL` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `supplier_id` | `uuid` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `vendor_account_id`
**Foreign keys:**
- `supplier_id` -> `dim_suppliers.supplier_id` (ON DELETE NO ACTION)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
**Unique constraints:**
- `billing_vendor_accounts_tenant_id_vendor_name_key`: (`tenant_id`, `vendor_name`)
**Indexes:**
- `billing_vendor_accounts_pkey`: `CREATE UNIQUE INDEX billing_vendor_accounts_pkey ON public.billing_vendor_accounts USING btree (vendor_account_id)`
- `billing_vendor_accounts_tenant_id_vendor_name_key`: `CREATE UNIQUE INDEX billing_vendor_accounts_tenant_id_vendor_name_key ON public.billing_vendor_accounts USING btree (tenant_id, vendor_name)`

---

## Table: `billing_vendor_invoice_lines`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `line_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `vendor_invoice_id` | `uuid` | `NO` | `NULL` |
| `line_number` | `smallint` | `NO` | `NULL` |
| `description` | `text` | `NO` | `NULL` |
| `hsn_sac` | `text` | `YES` | `NULL` |
| `quantity` | `numeric` | `NO` | `NULL` |
| `rate` | `numeric` | `NO` | `NULL` |
| `tax_pct` | `numeric` | `NO` | `0` |
| `amount` | `numeric` | `NO` | `NULL` |
| `nova_module` | `text` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `line_id`
**Foreign keys:**
- `vendor_invoice_id` -> `billing_vendor_invoices.vendor_invoice_id` (ON DELETE CASCADE)
**Unique constraints:** none
**Indexes:**
- `billing_vendor_invoice_lines_pkey`: `CREATE UNIQUE INDEX billing_vendor_invoice_lines_pkey ON public.billing_vendor_invoice_lines USING btree (line_id)`

---

## Table: `billing_vendor_invoices`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `vendor_invoice_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `vendor_account_id` | `uuid` | `NO` | `NULL` |
| `upload_id` | `uuid` | `YES` | `NULL` |
| `bill_id` | `uuid` | `YES` | `NULL` |
| `invoice_number` | `text` | `NO` | `NULL` |
| `invoice_date` | `date` | `NO` | `NULL` |
| `billing_period` | `text` | `NO` | `NULL` |
| `subtotal` | `numeric` | `NO` | `NULL` |
| `tax_amount` | `numeric` | `NO` | `0` |
| `total_amount` | `numeric` | `NO` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `verification_flag` | `bill_verification_flag` | `NO` | `'YELLOW'::bill_verification_flag` |
| `verification_detail` | `text` | `YES` | `NULL` |
| `verified_at` | `timestamp with time zone` | `YES` | `NULL` |
| `invoice_id` | `uuid` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `vendor_invoice_id`
**Foreign keys:**
- `bill_id` -> `fact_bills.bill_id` (ON DELETE NO ACTION)
- `invoice_id` -> `fact_invoices.invoice_id` (ON DELETE NO ACTION)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
- `upload_id` -> `raw_invoice_uploads.upload_id` (ON DELETE NO ACTION)
- `vendor_account_id` -> `billing_vendor_accounts.vendor_account_id` (ON DELETE NO ACTION)
**Unique constraints:**
- `billing_vendor_invoices_vendor_account_id_invoice_number_key`: (`vendor_account_id`, `invoice_number`)
**Indexes:**
- `billing_vendor_invoices_pkey`: `CREATE UNIQUE INDEX billing_vendor_invoices_pkey ON public.billing_vendor_invoices USING btree (vendor_invoice_id)`
- `billing_vendor_invoices_vendor_account_id_invoice_number_key`: `CREATE UNIQUE INDEX billing_vendor_invoices_vendor_account_id_invoice_number_key ON public.billing_vendor_invoices USING btree (vendor_account_id, invoice_number)`

---

## Table: `billing_vendor_module_alias`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `alias_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `vendor_name` | `text` | `NO` | `NULL` |
| `invoice_line_pattern` | `text` | `YES` | `NULL` |
| `nova_module` | `text` | `NO` | `NULL` |
| `nova_usage_slug` | `text` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `billing_basis` | `text` | `NO` | `'BILLABLE_OPERATIONS'::text` |

### Keys and Constraints
**Primary keys:** `alias_id`
**Foreign keys:**
none
**Unique constraints:**
- `billing_vendor_module_alias_pattern_key`: (`vendor_name`, `nova_module`, `invoice_line_pattern`)
**Indexes:**
- `billing_vendor_module_alias_pattern_key`: `CREATE UNIQUE INDEX billing_vendor_module_alias_pattern_key ON public.billing_vendor_module_alias USING btree (vendor_name, nova_module, invoice_line_pattern)`
- `billing_vendor_module_alias_pkey`: `CREATE UNIQUE INDEX billing_vendor_module_alias_pkey ON public.billing_vendor_module_alias USING btree (alias_id)`

---

## Table: `brands`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `name` | `character varying` | `NO` | `NULL` |
| `logo_path` | `text` | `YES` | `NULL` |
| `is_default` | `boolean` | `NO` | `false` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:** none
**Indexes:**
- `brands_pkey`: `CREATE UNIQUE INDEX brands_pkey ON public.brands USING btree (id)`
- `idx_brands_tenant`: `CREATE INDEX idx_brands_tenant ON public.brands USING btree (tenant_id)`

---

## Table: `coa_rules`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `exp_head` | `text` | `NO` | `NULL` |
| `vendor_name` | `text` | `YES` | `NULL` |
| `category` | `text` | `YES` | `NULL` |
| `priority` | `integer` | `NO` | `0` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:** none
**Indexes:**
- `coa_rules_pkey`: `CREATE UNIQUE INDEX coa_rules_pkey ON public.coa_rules USING btree (id)`
- `idx_coa_rules_tenant`: `CREATE INDEX idx_coa_rules_tenant ON public.coa_rules USING btree (tenant_id)`

---

## Table: `cost_centers`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `brand_id` | `uuid` | `NO` | `NULL` |
| `name` | `character varying` | `NO` | `NULL` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:** none
**Indexes:**
- `cost_centers_pkey`: `CREATE UNIQUE INDEX cost_centers_pkey ON public.cost_centers USING btree (id)`
- `idx_cost_centers_tenant_brand`: `CREATE INDEX idx_cost_centers_tenant_brand ON public.cost_centers USING btree (tenant_id, brand_id)`
- `uq_cost_centers_tenant_brand_name`: `CREATE UNIQUE INDEX uq_cost_centers_tenant_brand_name ON public.cost_centers USING btree (tenant_id, brand_id, name)`

---

## Table: `dim_bank_accounts`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `bank_account_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `source_account_id` | `text` | `NO` | `NULL` |
| `account_name` | `text` | `NO` | `NULL` |
| `account_number_enc` | `text` | `YES` | `NULL` |
| `bank_name` | `text` | `YES` | `NULL` |
| `ifsc_code` | `text` | `YES` | `NULL` |
| `swift_code` | `text` | `YES` | `NULL` |
| `account_type` | `bank_account_type` | `NO` | `'CURRENT'::bank_account_type` |
| `currency_code` | `character` | `NO` | `'INR'::bpchar` |
| `is_primary` | `boolean` | `NO` | `false` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `bank_account_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
**Unique constraints:**
- `dim_bank_accounts_tenant_id_source_source_account_id_key`: (`tenant_id`, `source`, `source_account_id`)
**Indexes:**
- `dim_bank_accounts_pkey`: `CREATE UNIQUE INDEX dim_bank_accounts_pkey ON public.dim_bank_accounts USING btree (bank_account_id)`
- `dim_bank_accounts_tenant_id_source_source_account_id_key`: `CREATE UNIQUE INDEX dim_bank_accounts_tenant_id_source_source_account_id_key ON public.dim_bank_accounts USING btree (tenant_id, source, source_account_id)`
- `idx_dim_bank_accounts_tenant`: `CREATE INDEX idx_dim_bank_accounts_tenant ON public.dim_bank_accounts USING btree (tenant_id)`

---

## Table: `dim_categories`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `name` | `text` | `NO` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `exp_head` | `text` | `YES` | `NULL` |
| `is_builtin` | `boolean` | `NO` | `false` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:**
- `dim_categories_tenant_id_name_key`: (`tenant_id`, `name`)
**Indexes:**
- `dim_categories_pkey`: `CREATE UNIQUE INDEX dim_categories_pkey ON public.dim_categories USING btree (id)`
- `dim_categories_tenant_id_name_key`: `CREATE UNIQUE INDEX dim_categories_tenant_id_name_key ON public.dim_categories USING btree (tenant_id, name)`
- `idx_dim_categories_tenant`: `CREATE INDEX idx_dim_categories_tenant ON public.dim_categories USING btree (tenant_id)`

---

## Table: `dim_charges`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `charge_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `name` | `text` | `NO` | `NULL` |
| `hsn_sac` | `text` | `YES` | `NULL` |
| `default_ledger_id` | `uuid` | `YES` | `NULL` |
| `default_tax_rate` | `numeric` | `YES` | `NULL` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `charge_id`
**Foreign keys:**
- `default_ledger_id` -> `dim_ledger_accounts.account_id` (ON DELETE SET NULL)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
**Unique constraints:**
- `dim_charges_tenant_id_name_key`: (`tenant_id`, `name`)
**Indexes:**
- `dim_charges_pkey`: `CREATE UNIQUE INDEX dim_charges_pkey ON public.dim_charges USING btree (charge_id)`
- `dim_charges_tenant_id_name_key`: `CREATE UNIQUE INDEX dim_charges_tenant_id_name_key ON public.dim_charges USING btree (tenant_id, name)`

---

## Table: `dim_cost_centres`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `cost_centre_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `source_cc_id` | `text` | `NO` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `name` | `text` | `NO` | `NULL` |
| `parent_cc_id` | `uuid` | `YES` | `NULL` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `cost_centre_id`
**Foreign keys:**
- `parent_cc_id` -> `dim_cost_centres.cost_centre_id` (ON DELETE NO ACTION)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
**Unique constraints:**
- `dim_cost_centres_tenant_id_source_source_cc_id_key`: (`tenant_id`, `source`, `source_cc_id`)
**Indexes:**
- `dim_cost_centres_pkey`: `CREATE UNIQUE INDEX dim_cost_centres_pkey ON public.dim_cost_centres USING btree (cost_centre_id)`
- `dim_cost_centres_tenant_id_source_source_cc_id_key`: `CREATE UNIQUE INDEX dim_cost_centres_tenant_id_source_source_cc_id_key ON public.dim_cost_centres USING btree (tenant_id, source, source_cc_id)`

---

## Table: `dim_currencies`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `currency_code` | `character` | `NO` | `NULL` |
| `name` | `text` | `NO` | `NULL` |
| `symbol` | `text` | `NO` | `NULL` |
| `decimal_places` | `smallint` | `NO` | `2` |
| `is_active` | `boolean` | `NO` | `true` |

### Keys and Constraints
**Primary keys:** `currency_code`
**Foreign keys:**
none
**Unique constraints:** none
**Indexes:**
- `dim_currencies_pkey`: `CREATE UNIQUE INDEX dim_currencies_pkey ON public.dim_currencies USING btree (currency_code)`

---

## Table: `dim_customers`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `customer_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `source_customer_id` | `text` | `NO` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `name` | `text` | `NO` | `NULL` |
| `email` | `text` | `YES` | `NULL` |
| `gstn` | `character` | `NO` | `NULL` |
| `phone` | `text` | `YES` | `NULL` |
| `address_jsonb` | `jsonb` | `NO` | `'{}'::jsonb` |
| `pii_jsonb` | `jsonb` | `NO` | `'{}'::jsonb` |
| `payment_terms_days` | `smallint` | `NO` | `30` |
| `risk_score` | `numeric` | `NO` | `50` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `customer_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
**Unique constraints:**
- `dim_customers_tenant_id_source_source_customer_id_key`: (`tenant_id`, `source`, `source_customer_id`)
**Indexes:**
- `dim_customers_pkey`: `CREATE UNIQUE INDEX dim_customers_pkey ON public.dim_customers USING btree (customer_id)`
- `dim_customers_tenant_id_source_source_customer_id_key`: `CREATE UNIQUE INDEX dim_customers_tenant_id_source_source_customer_id_key ON public.dim_customers USING btree (tenant_id, source, source_customer_id)`
- `idx_dim_customers_email`: `CREATE INDEX idx_dim_customers_email ON public.dim_customers USING btree (tenant_id, email) WHERE (email IS NOT NULL)`
- `idx_dim_customers_gstn`: `CREATE INDEX idx_dim_customers_gstn ON public.dim_customers USING btree (gstn) WHERE (gstn IS NOT NULL)`
- `idx_dim_customers_name_trgm`: `CREATE INDEX idx_dim_customers_name_trgm ON public.dim_customers USING gin (name gin_trgm_ops)`
- `idx_dim_customers_tenant`: `CREATE INDEX idx_dim_customers_tenant ON public.dim_customers USING btree (tenant_id)`

---

## Table: `dim_end_customer_contacts`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `contact_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `end_customer_id` | `uuid` | `NO` | `NULL` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `full_name` | `text` | `NO` | `NULL` |
| `email` | `text` | `YES` | `NULL` |
| `phone` | `text` | `YES` | `NULL` |
| `designation` | `text` | `YES` | `NULL` |
| `is_primary` | `boolean` | `NO` | `false` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `contact_id`
**Foreign keys:**
- `end_customer_id` -> `dim_end_customers.end_customer_id` (ON DELETE CASCADE)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
**Unique constraints:** none
**Indexes:**
- `dim_end_customer_contacts_pkey`: `CREATE UNIQUE INDEX dim_end_customer_contacts_pkey ON public.dim_end_customer_contacts USING btree (contact_id)`
- `uq_primary_contact`: `CREATE UNIQUE INDEX uq_primary_contact ON public.dim_end_customer_contacts USING btree (end_customer_id) WHERE (is_primary = true)`

---

## Table: `dim_end_customers`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `end_customer_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `source_customer_id` | `text` | `NO` | `NULL` |
| `company_name` | `text` | `NO` | `NULL` |
| `trade_name` | `text` | `YES` | `NULL` |
| `gstin` | `character` | `YES` | `NULL` |
| `pan` | `character` | `YES` | `NULL` |
| `email` | `text` | `YES` | `NULL` |
| `phone` | `text` | `YES` | `NULL` |
| `billing_address_jsonb` | `jsonb` | `NO` | `'{}'::jsonb` |
| `shipping_address_jsonb` | `jsonb` | `YES` | `NULL` |
| `payment_terms_days` | `smallint` | `NO` | `30` |
| `credit_limit` | `numeric` | `YES` | `NULL` |
| `currency_code` | `character` | `NO` | `'INR'::bpchar` |
| `risk_score` | `numeric` | `NO` | `50.00` |
| `risk_bucket` | `risk_bucket` | `YES` | `NULL` |
| `kyc_status` | `text` | `NO` | `'PENDING'::text` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |
| `bank_account_no` | `text` | `YES` | `NULL` |
| `bank_ifsc` | `text` | `YES` | `NULL` |
| `bank_verified` | `boolean` | `NO` | `false` |

### Keys and Constraints
**Primary keys:** `end_customer_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
**Unique constraints:**
- `uq_end_customer_source`: (`tenant_id`, `source`, `source_customer_id`)
**Indexes:**
- `dim_end_customers_pkey`: `CREATE UNIQUE INDEX dim_end_customers_pkey ON public.dim_end_customers USING btree (end_customer_id)`
- `idx_end_customers_gstin`: `CREATE INDEX idx_end_customers_gstin ON public.dim_end_customers USING btree (gstin)`
- `idx_end_customers_risk`: `CREATE INDEX idx_end_customers_risk ON public.dim_end_customers USING btree (risk_bucket)`
- `idx_end_customers_tenant`: `CREATE INDEX idx_end_customers_tenant ON public.dim_end_customers USING btree (tenant_id)`
- `uq_end_customer_source`: `CREATE UNIQUE INDEX uq_end_customer_source ON public.dim_end_customers USING btree (tenant_id, source, source_customer_id)`

---

## Table: `dim_fx_rates`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `rate_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `rate_date` | `date` | `NO` | `NULL` |
| `currency_code` | `character` | `NO` | `NULL` |
| `inr_per_unit` | `numeric` | `NO` | `NULL` |
| `source` | `text` | `NO` | `NULL` |
| `is_business_day` | `boolean` | `NO` | `true` |
| `fetched_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `rate_id`
**Foreign keys:**
- `currency_code` -> `dim_currencies.currency_code` (ON DELETE NO ACTION)
**Unique constraints:**
- `dim_fx_rates_rate_date_currency_code_key`: (`rate_date`, `currency_code`)
**Indexes:**
- `dim_fx_rates_pkey`: `CREATE UNIQUE INDEX dim_fx_rates_pkey ON public.dim_fx_rates USING btree (rate_id)`
- `dim_fx_rates_rate_date_currency_code_key`: `CREATE UNIQUE INDEX dim_fx_rates_rate_date_currency_code_key ON public.dim_fx_rates USING btree (rate_date, currency_code)`
- `idx_fx_rates_currency_date`: `CREATE INDEX idx_fx_rates_currency_date ON public.dim_fx_rates USING btree (currency_code, rate_date DESC)`
- `idx_fx_rates_date`: `CREATE INDEX idx_fx_rates_date ON public.dim_fx_rates USING btree (rate_date DESC)`

---

## Table: `dim_ledger_accounts`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `account_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `source_account_id` | `text` | `NO` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `account_code` | `text` | `YES` | `NULL` |
| `account_name` | `text` | `NO` | `NULL` |
| `account_type` | `text` | `NO` | `NULL` |
| `parent_account_id` | `uuid` | `YES` | `NULL` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `account_id`
**Foreign keys:**
- `parent_account_id` -> `dim_ledger_accounts.account_id` (ON DELETE NO ACTION)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
**Unique constraints:**
- `dim_ledger_accounts_tenant_id_source_source_account_id_key`: (`tenant_id`, `source`, `source_account_id`)
**Indexes:**
- `dim_ledger_accounts_pkey`: `CREATE UNIQUE INDEX dim_ledger_accounts_pkey ON public.dim_ledger_accounts USING btree (account_id)`
- `dim_ledger_accounts_tenant_id_source_source_account_id_key`: `CREATE UNIQUE INDEX dim_ledger_accounts_tenant_id_source_source_account_id_key ON public.dim_ledger_accounts USING btree (tenant_id, source, source_account_id)`
- `idx_dim_ledger_tenant`: `CREATE INDEX idx_dim_ledger_tenant ON public.dim_ledger_accounts USING btree (tenant_id)`
- `idx_ledger_parent`: `CREATE INDEX idx_ledger_parent ON public.dim_ledger_accounts USING btree (parent_account_id)`
- `idx_ledger_tenant`: `CREATE INDEX idx_ledger_tenant ON public.dim_ledger_accounts USING btree (tenant_id)`

---

## Table: `dim_products`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `product_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `source_product_id` | `text` | `NO` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `name` | `text` | `NO` | `NULL` |
| `unit` | `text` | `YES` | `NULL` |
| `hsn_code` | `text` | `YES` | `NULL` |
| `description` | `text` | `YES` | `NULL` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `product_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
**Unique constraints:**
- `dim_products_tenant_id_source_source_product_id_key`: (`tenant_id`, `source`, `source_product_id`)
**Indexes:**
- `dim_products_pkey`: `CREATE UNIQUE INDEX dim_products_pkey ON public.dim_products USING btree (product_id)`
- `dim_products_tenant_id_source_source_product_id_key`: `CREATE UNIQUE INDEX dim_products_tenant_id_source_source_product_id_key ON public.dim_products USING btree (tenant_id, source, source_product_id)`
- `idx_products_hsn`: `CREATE INDEX idx_products_hsn ON public.dim_products USING btree (hsn_code)`
- `idx_products_tenant`: `CREATE INDEX idx_products_tenant ON public.dim_products USING btree (tenant_id)`

---

## Table: `dim_suppliers`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `supplier_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `source_supplier_id` | `text` | `NO` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `name` | `text` | `NO` | `NULL` |
| `gstn` | `character` | `YES` | `NULL` |
| `email` | `text` | `YES` | `NULL` |
| `payment_terms_days` | `smallint` | `NO` | `30` |
| `status` | `text` | `NO` | `'UNVERIFIED'::text` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `category` | `character varying` | `YES` | `NULL` |
| `bank_account` | `text` | `YES` | `NULL` |
| `bank_ifsc` | `text` | `YES` | `NULL` |
| `bank_verified` | `boolean` | `NO` | `false` |
| `pan` | `text` | `YES` | `NULL` |
| `vendor_category_id` | `uuid` | `YES` | `NULL` |
| `bank_name` | `text` | `YES` | `NULL` |
| `brand_id` | `uuid` | `YES` | `NULL` |
| `pan_dup_deferred` | `boolean` | `NO` | `false` |

### Keys and Constraints
**Primary keys:** `supplier_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
- `vendor_category_id` -> `dim_vendor_categories.category_id` (ON DELETE NO ACTION)
**Unique constraints:**
- `dim_suppliers_tenant_id_source_source_supplier_id_key`: (`tenant_id`, `source`, `source_supplier_id`)
**Indexes:**
- `dim_suppliers_pkey`: `CREATE UNIQUE INDEX dim_suppliers_pkey ON public.dim_suppliers USING btree (supplier_id)`
- `dim_suppliers_tenant_id_source_source_supplier_id_key`: `CREATE UNIQUE INDEX dim_suppliers_tenant_id_source_source_supplier_id_key ON public.dim_suppliers USING btree (tenant_id, source, source_supplier_id)`
- `idx_dim_suppliers_gstn`: `CREATE INDEX idx_dim_suppliers_gstn ON public.dim_suppliers USING btree (gstn) WHERE (gstn IS NOT NULL)`
- `idx_dim_suppliers_name_trgm`: `CREATE INDEX idx_dim_suppliers_name_trgm ON public.dim_suppliers USING gin (name gin_trgm_ops)`
- `idx_dim_suppliers_tenant`: `CREATE INDEX idx_dim_suppliers_tenant ON public.dim_suppliers USING btree (tenant_id)`

---

## Table: `dim_tax_rates`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `tax_rate_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `source` | `data_source` | `NO` | `'TALLY'::data_source` |
| `tax_name` | `text` | `NO` | `NULL` |
| `tax_type` | `tax_type` | `NO` | `NULL` |
| `rate_percent` | `numeric` | `NO` | `NULL` |
| `effective_from` | `date` | `YES` | `NULL` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `tax_rate_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
**Unique constraints:** none
**Indexes:**
- `dim_tax_rates_pkey`: `CREATE UNIQUE INDEX dim_tax_rates_pkey ON public.dim_tax_rates USING btree (tax_rate_id)`

---

## Table: `dim_tds_mapping_rules`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `rule_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `priority` | `integer` | `NO` | `NULL` |
| `vendor_category_id` | `uuid` | `YES` | `NULL` |
| `additional_condition` | `jsonb` | `YES` | `NULL` |
| `tds_section_id` | `uuid` | `NO` | `NULL` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `rate_override` | `numeric` | `YES` | `NULL` |
| `description` | `text` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `rule_id`
**Foreign keys:**
- `tds_section_id` -> `dim_tds_sections.tds_section_id` (ON DELETE NO ACTION)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
- `vendor_category_id` -> `dim_vendor_categories.category_id` (ON DELETE NO ACTION)
**Unique constraints:**
- `dim_tds_mapping_rules_tenant_id_priority_key`: (`tenant_id`, `priority`)
**Indexes:**
- `dim_tds_mapping_rules_pkey`: `CREATE UNIQUE INDEX dim_tds_mapping_rules_pkey ON public.dim_tds_mapping_rules USING btree (rule_id)`
- `dim_tds_mapping_rules_tenant_id_priority_key`: `CREATE UNIQUE INDEX dim_tds_mapping_rules_tenant_id_priority_key ON public.dim_tds_mapping_rules USING btree (tenant_id, priority)`
- `idx_tds_mapping_tenant_pri`: `CREATE INDEX idx_tds_mapping_tenant_pri ON public.dim_tds_mapping_rules USING btree (tenant_id, priority) WHERE (is_active = true)`

---

## Table: `dim_tds_sections`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `tds_section_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `return_code` | `text` | `NO` | `NULL` |
| `old_section_label` | `text` | `YES` | `NULL` |
| `new_section_label` | `text` | `YES` | `NULL` |
| `nature_of_payment` | `text` | `NO` | `NULL` |
| `side` | `text` | `NO` | `'AP'::text` |
| `default_rate` | `numeric` | `NO` | `NULL` |
| `has_split_rate` | `boolean` | `NO` | `false` |
| `rate_individual_huf` | `numeric` | `YES` | `NULL` |
| `rate_others` | `numeric` | `YES` | `NULL` |
| `pan_missing_rate` | `numeric` | `NO` | `20.0` |
| `threshold_type` | `text` | `NO` | `'NONE'::text` |
| `threshold_amount` | `numeric` | `YES` | `NULL` |
| `threshold_amount_2` | `numeric` | `YES` | `NULL` |
| `threshold_2_type` | `text` | `YES` | `NULL` |
| `base_calculation` | `text` | `NO` | `'TAXABLE'::text` |
| `effective_from` | `date` | `YES` | `NULL` |
| `effective_to` | `date` | `YES` | `NULL` |
| `is_active` | `boolean` | `NO` | `true` |
| `display_order` | `integer` | `NO` | `0` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `tds_section_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
**Unique constraints:**
- `dim_tds_sections_tenant_id_return_code_effective_from_key`: (`tenant_id`, `return_code`, `effective_from`)
**Indexes:**
- `dim_tds_sections_pkey`: `CREATE UNIQUE INDEX dim_tds_sections_pkey ON public.dim_tds_sections USING btree (tds_section_id)`
- `dim_tds_sections_tenant_id_return_code_effective_from_key`: `CREATE UNIQUE INDEX dim_tds_sections_tenant_id_return_code_effective_from_key ON public.dim_tds_sections USING btree (tenant_id, return_code, effective_from)`
- `idx_dim_tds_sections_tenant`: `CREATE INDEX idx_dim_tds_sections_tenant ON public.dim_tds_sections USING btree (tenant_id, is_active)`

---

## Table: `dim_tenants`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `tenant_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `company_name` | `text` | `NO` | `NULL` |
| `gstn` | `character` | `YES` | `NULL` |
| `plan_tier` | `text` | `NO` | `'free'::text` |
| `timezone` | `text` | `NO` | `'Asia/Kolkata'::text` |
| `region` | `text` | `NO` | `'ap-south-1'::text` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `settings_jsonb` | `jsonb` | `NO` | `'{}'::jsonb` |
| `sap_matching_enabled` | `boolean` | `NO` | `false` |

### Keys and Constraints
**Primary keys:** `tenant_id`
**Foreign keys:**
none
**Unique constraints:**
- `dim_tenants_gstn_key`: (`gstn`)
**Indexes:**
- `dim_tenants_gstn_key`: `CREATE UNIQUE INDEX dim_tenants_gstn_key ON public.dim_tenants USING btree (gstn)`
- `dim_tenants_pkey`: `CREATE UNIQUE INDEX dim_tenants_pkey ON public.dim_tenants USING btree (tenant_id)`

---

## Table: `dim_vendor_categories`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `category_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `name` | `text` | `NO` | `NULL` |
| `allowed_entity_types` | `ARRAY` | `NO` | `'{INDIVIDUAL_HUF,OTHERS}'::text[]` |
| `display_order` | `integer` | `NO` | `0` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `description` | `text` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `category_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
**Unique constraints:**
- `dim_vendor_categories_tenant_id_name_key`: (`tenant_id`, `name`)
**Indexes:**
- `dim_vendor_categories_pkey`: `CREATE UNIQUE INDEX dim_vendor_categories_pkey ON public.dim_vendor_categories USING btree (category_id)`
- `dim_vendor_categories_tenant_id_name_key`: `CREATE UNIQUE INDEX dim_vendor_categories_tenant_id_name_key ON public.dim_vendor_categories USING btree (tenant_id, name)`
- `idx_dim_vendor_cat_tenant`: `CREATE INDEX idx_dim_vendor_cat_tenant ON public.dim_vendor_categories USING btree (tenant_id)`

---

## Table: `dim_vendor_tds_overrides`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `override_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `supplier_id` | `uuid` | `NO` | `NULL` |
| `category_id` | `uuid` | `YES` | `NULL` |
| `entity_type` | `text` | `NO` | `'OTHERS'::text` |
| `override_section_id` | `uuid` | `YES` | `NULL` |
| `lower_cert_rate` | `numeric` | `YES` | `NULL` |
| `lower_cert_number` | `text` | `YES` | `NULL` |
| `lower_cert_valid_from` | `date` | `YES` | `NULL` |
| `lower_cert_valid_to` | `date` | `YES` | `NULL` |
| `pan_verified` | `boolean` | `NO` | `false` |
| `notes` | `text` | `YES` | `NULL` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |
| `tds_mode` | `text` | `NO` | `'cumulative'::text` |
| `mode_set_by` | `uuid` | `YES` | `NULL` |
| `mode_set_on` | `timestamp with time zone` | `YES` | `NULL` |
| `mode_reason` | `text` | `YES` | `NULL` |
| `effective_from` | `date` | `NO` | `CURRENT_DATE` |
| `override_rate` | `numeric` | `YES` | `NULL` |
| `certificate_no` | `text` | `YES` | `NULL` |
| `tds_section_id` | `uuid` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `override_id`
**Foreign keys:**
- `category_id` -> `dim_vendor_categories.category_id` (ON DELETE NO ACTION)
- `override_section_id` -> `dim_tds_sections.tds_section_id` (ON DELETE NO ACTION)
- `supplier_id` -> `dim_suppliers.supplier_id` (ON DELETE NO ACTION)
- `tds_section_id` -> `dim_tds_sections.tds_section_id` (ON DELETE NO ACTION)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
**Unique constraints:**
- `dim_vendor_tds_overrides_tenant_id_supplier_id_key`: (`tenant_id`, `supplier_id`)
**Indexes:**
- `dim_vendor_tds_overrides_pkey`: `CREATE UNIQUE INDEX dim_vendor_tds_overrides_pkey ON public.dim_vendor_tds_overrides USING btree (override_id)`
- `dim_vendor_tds_overrides_tenant_id_supplier_id_key`: `CREATE UNIQUE INDEX dim_vendor_tds_overrides_tenant_id_supplier_id_key ON public.dim_vendor_tds_overrides USING btree (tenant_id, supplier_id)`
- `idx_vendor_tds_override_supplier`: `CREATE INDEX idx_vendor_tds_override_supplier ON public.dim_vendor_tds_overrides USING btree (tenant_id, supplier_id, is_active)`

---

## Table: `disbursement_overrides`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `override_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `invoice_id` | `uuid` | `NO` | `NULL` |
| `supplier_id` | `uuid` | `YES` | `NULL` |
| `overridden_by` | `text` | `YES` | `NULL` |
| `overridden_by_role` | `text` | `YES` | `NULL` |
| `reason` | `text` | `NO` | `NULL` |
| `original_account_number` | `text` | `YES` | `NULL` |
| `original_ifsc` | `text` | `YES` | `NULL` |
| `original_gstin_verified` | `boolean` | `YES` | `NULL` |
| `override_account_number` | `text` | `NO` | `NULL` |
| `override_ifsc` | `text` | `NO` | `NULL` |
| `override_beneficiary_name` | `text` | `NO` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `override_id`
**Foreign keys:**
none
**Unique constraints:** none
**Indexes:**
- `disbursement_overrides_pkey`: `CREATE UNIQUE INDEX disbursement_overrides_pkey ON public.disbursement_overrides USING btree (override_id)`
- `idx_dover_tenant_supplier`: `CREATE INDEX idx_dover_tenant_supplier ON public.disbursement_overrides USING btree (tenant_id, supplier_id)`

---

## Table: `fact_bills`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `bill_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `stg_bill_id` | `uuid` | `NO` | `NULL` |
| `supplier_id` | `uuid` | `YES` | `NULL` |
| `bill_number` | `text` | `NO` | `NULL` |
| `bill_date` | `date` | `NO` | `NULL` |
| `due_date` | `date` | `YES` | `NULL` |
| `fiscal_year` | `text` | `NO` | `NULL` |
| `fiscal_quarter` | `text` | `NO` | `NULL` |
| `fiscal_month` | `text` | `NO` | `NULL` |
| `currency_code` | `character` | `NO` | `'INR'::bpchar` |
| `subtotal` | `numeric` | `NO` | `0` |
| `tax_amount` | `numeric` | `NO` | `0` |
| `total_amount` | `numeric` | `NO` | `NULL` |
| `amount_paid` | `numeric` | `NO` | `0` |
| `outstanding_amount` | `numeric` | `YES` | `NULL` |
| `status` | `invoice_status` | `NO` | `'DRAFT'::invoice_status` |
| `source` | `data_source` | `NO` | `NULL` |
| `source_updated_at` | `timestamp with time zone` | `NO` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |
| `tds_section_code` | `text` | `YES` | `NULL` |
| `tds_rate_percent` | `numeric` | `YES` | `NULL` |
| `tds_base_amount` | `numeric` | `YES` | `NULL` |
| `tds_amount` | `numeric` | `YES` | `NULL` |
| `net_payable` | `numeric` | `YES` | `NULL` |
| `tds_status` | `text` | `YES` | `'PENDING'::text` |
| `tds_section_id` | `uuid` | `YES` | `NULL` |
| `tds_rate` | `numeric` | `YES` | `NULL` |
| `original_amount` | `numeric` | `YES` | `NULL` |
| `fx_rate_id` | `uuid` | `YES` | `NULL` |
| `inr_amount_current` | `numeric` | `YES` | `NULL` |
| `fx_gain_loss` | `numeric` | `YES` | `NULL` |
| `fx_actual_inr` | `numeric` | `YES` | `NULL` |
| `fx_effective_rate` | `numeric` | `YES` | `NULL` |
| `fx_realized_cost` | `numeric` | `YES` | `NULL` |
| `fx_agent_note` | `text` | `YES` | `NULL` |
| `fx_agent_note_at` | `timestamp with time zone` | `YES` | `NULL` |
| `verification_flag` | `bill_verification_flag` | `YES` | `NULL` |
| `verification_detail` | `text` | `YES` | `NULL` |
| `verified_at` | `timestamp with time zone` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `bill_id`
**Foreign keys:**
- `fx_rate_id` -> `dim_fx_rates.rate_id` (ON DELETE NO ACTION)
- `stg_bill_id` -> `stg_bills.stg_bill_id` (ON DELETE NO ACTION)
- `supplier_id` -> `dim_suppliers.supplier_id` (ON DELETE NO ACTION)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
**Unique constraints:**
- `fact_bills_stg_bill_id_key`: (`stg_bill_id`)
**Indexes:**
- `fact_bills_pkey`: `CREATE UNIQUE INDEX fact_bills_pkey ON public.fact_bills USING btree (bill_id)`
- `fact_bills_stg_bill_id_key`: `CREATE UNIQUE INDEX fact_bills_stg_bill_id_key ON public.fact_bills USING btree (stg_bill_id)`
- `idx_fact_bills_status`: `CREATE INDEX idx_fact_bills_status ON public.fact_bills USING btree (tenant_id, status, due_date)`
- `idx_fact_bills_supplier`: `CREATE INDEX idx_fact_bills_supplier ON public.fact_bills USING btree (supplier_id)`
- `idx_fact_bills_tenant`: `CREATE INDEX idx_fact_bills_tenant ON public.fact_bills USING btree (tenant_id)`

---

## Table: `fact_cashflow_daily`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `cashflow_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `cashflow_date` | `date` | `NO` | `NULL` |
| `currency_code` | `character` | `NO` | `'INR'::bpchar` |
| `inflows_actual` | `numeric` | `NO` | `0` |
| `outflows_actual` | `numeric` | `NO` | `0` |
| `net_cashflow` | `numeric` | `YES` | `NULL` |
| `inflows_forecast` | `numeric` | `NO` | `0` |
| `outflows_forecast` | `numeric` | `NO` | `0` |
| `opening_balance` | `numeric` | `YES` | `NULL` |
| `closing_balance` | `numeric` | `YES` | `NULL` |
| `ar_outstanding` | `numeric` | `YES` | `NULL` |
| `ap_outstanding` | `numeric` | `YES` | `NULL` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |
| `invoice_id` | `uuid` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `cashflow_id`
**Foreign keys:**
- `invoice_id` -> `fact_invoices.invoice_id` (ON DELETE NO ACTION)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
**Unique constraints:** none
**Indexes:**
- `fact_cashflow_daily_pkey`: `CREATE UNIQUE INDEX fact_cashflow_daily_pkey ON public.fact_cashflow_daily USING btree (cashflow_id)`
- `uq_cashflow_date_idx`: `CREATE INDEX uq_cashflow_date_idx ON public.fact_cashflow_daily USING btree (tenant_id, cashflow_date)`
- `uq_cashflow_invoice`: `CREATE UNIQUE INDEX uq_cashflow_invoice ON public.fact_cashflow_daily USING btree (tenant_id, invoice_id)`

---

## Table: `fact_disbursements`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `disbursement_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `invoice_id` | `uuid` | `NO` | `NULL` |
| `supplier_id` | `uuid` | `YES` | `NULL` |
| `amount` | `numeric` | `NO` | `NULL` |
| `beneficiary_name` | `text` | `NO` | `NULL` |
| `beneficiary_account` | `text` | `NO` | `NULL` |
| `beneficiary_ifsc` | `text` | `NO` | `NULL` |
| `beneficiary_mobile` | `text` | `YES` | `NULL` |
| `remitter_account_number` | `text` | `NO` | `NULL` |
| `bank_provider` | `text` | `NO` | `'ICICI'::text` |
| `tran_ref_no` | `text` | `NO` | `NULL` |
| `bank_tran_ref_no` | `text` | `YES` | `NULL` |
| `bank_rrn` | `text` | `YES` | `NULL` |
| `status` | `text` | `NO` | `'PENDING_OTP'::text` |
| `status_message` | `text` | `YES` | `NULL` |
| `act_code` | `text` | `YES` | `NULL` |
| `otp_code_hash` | `text` | `YES` | `NULL` |
| `otp_expires_at` | `double precision` | `YES` | `NULL` |
| `otp_verified_at` | `timestamp with time zone` | `YES` | `NULL` |
| `otp_attempts` | `integer` | `NO` | `0` |
| `initiated_by` | `text` | `YES` | `NULL` |
| `raw_response_jsonb` | `jsonb` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |
| `is_overridden` | `boolean` | `NO` | `false` |
| `override_id` | `uuid` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `disbursement_id`
**Foreign keys:**
none
**Unique constraints:**
- `fact_disbursements_tenant_id_tran_ref_no_key`: (`tenant_id`, `tran_ref_no`)
**Indexes:**
- `fact_disbursements_pkey`: `CREATE UNIQUE INDEX fact_disbursements_pkey ON public.fact_disbursements USING btree (disbursement_id)`
- `fact_disbursements_tenant_id_tran_ref_no_key`: `CREATE UNIQUE INDEX fact_disbursements_tenant_id_tran_ref_no_key ON public.fact_disbursements USING btree (tenant_id, tran_ref_no)`
- `idx_fdis_tenant_invoice`: `CREATE INDEX idx_fdis_tenant_invoice ON public.fact_disbursements USING btree (tenant_id, invoice_id)`
- `idx_fdis_tenant_status`: `CREATE INDEX idx_fdis_tenant_status ON public.fact_disbursements USING btree (tenant_id, status)`

---

## Table: `fact_graph_edges`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `edge_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `src_node_id` | `uuid` | `NO` | `NULL` |
| `dst_node_id` | `uuid` | `NO` | `NULL` |
| `relation` | `text` | `NO` | `NULL` |
| `weight` | `double precision` | `NO` | `1.0` |
| `properties` | `jsonb` | `NO` | `'{}'::jsonb` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `edge_id`
**Foreign keys:**
- `dst_node_id` -> `fact_graph_nodes.node_id` (ON DELETE CASCADE)
- `src_node_id` -> `fact_graph_nodes.node_id` (ON DELETE CASCADE)
**Unique constraints:** none
**Indexes:**
- `fact_graph_edges_pkey`: `CREATE UNIQUE INDEX fact_graph_edges_pkey ON public.fact_graph_edges USING btree (edge_id)`
- `idx_fge_tenant_dst`: `CREATE INDEX idx_fge_tenant_dst ON public.fact_graph_edges USING btree (tenant_id, dst_node_id)`
- `idx_fge_tenant_src`: `CREATE INDEX idx_fge_tenant_src ON public.fact_graph_edges USING btree (tenant_id, src_node_id)`
- `uq_fge_triple`: `CREATE UNIQUE INDEX uq_fge_triple ON public.fact_graph_edges USING btree (tenant_id, src_node_id, dst_node_id, relation)`

---

## Table: `fact_graph_nodes`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `node_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `node_type` | `text` | `NO` | `NULL` |
| `ref_id` | `uuid` | `YES` | `NULL` |
| `label` | `text` | `NO` | `NULL` |
| `properties` | `jsonb` | `NO` | `'{}'::jsonb` |
| `embedding` | `vector` | `YES` | `NULL` |
| `content_hash` | `text` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `node_id`
**Foreign keys:**
none
**Unique constraints:** none
**Indexes:**
- `fact_graph_nodes_pkey`: `CREATE UNIQUE INDEX fact_graph_nodes_pkey ON public.fact_graph_nodes USING btree (node_id)`
- `idx_fgn_embedding_hnsw`: `CREATE INDEX idx_fgn_embedding_hnsw ON public.fact_graph_nodes USING hnsw (embedding vector_cosine_ops)`
- `idx_fgn_tenant_type`: `CREATE INDEX idx_fgn_tenant_type ON public.fact_graph_nodes USING btree (tenant_id, node_type)`
- `uq_fgn_entity`: `CREATE UNIQUE INDEX uq_fgn_entity ON public.fact_graph_nodes USING btree (tenant_id, node_type, ref_id) WHERE (ref_id IS NOT NULL)`
- `uq_fgn_fact`: `CREATE UNIQUE INDEX uq_fgn_fact ON public.fact_graph_nodes USING btree (tenant_id, node_type, content_hash) WHERE ((ref_id IS NULL) AND (content_hash IS NOT NULL))`

---

## Table: `fact_invoices`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `invoice_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `stg_invoice_id` | `uuid` | `YES` | `NULL` |
| `end_customer_id` | `uuid` | `YES` | `NULL` |
| `account_id` | `uuid` | `YES` | `NULL` |
| `invoice_number` | `text` | `NO` | `NULL` |
| `invoice_date` | `date` | `NO` | `NULL` |
| `due_date` | `date` | `YES` | `NULL` |
| `fiscal_year` | `smallint` | `NO` | `NULL` |
| `fiscal_quarter` | `smallint` | `NO` | `NULL` |
| `fiscal_month` | `smallint` | `NO` | `NULL` |
| `currency_code` | `character` | `NO` | `'INR'::bpchar` |
| `subtotal` | `numeric` | `NO` | `NULL` |
| `tax_amount` | `numeric` | `NO` | `0` |
| `total_amount` | `numeric` | `NO` | `NULL` |
| `amount_paid` | `numeric` | `NO` | `0` |
| `outstanding_amount` | `numeric` | `YES` | `NULL` |
| `status` | `invoice_status` | `NO` | `NULL` |
| `risk_bucket` | `risk_bucket` | `YES` | `NULL` |
| `dso_contribution` | `numeric` | `YES` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `source_updated_at` | `timestamp with time zone` | `NO` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |
| `invoice_type` | `text` | `NO` | `'AR'::text` |
| `stg_bill_id` | `uuid` | `YES` | `NULL` |
| `supplier_id` | `uuid` | `YES` | `NULL` |
| `upload_id` | `uuid` | `YES` | `NULL` |
| `transaction_type` | `text` | `YES` | `NULL` |
| `category` | `character varying` | `YES` | `NULL` |
| `payment_status` | `character varying` | `YES` | `'unpaid'::character varying` |
| `tally_push_pending` | `boolean` | `NO` | `false` |
| `manual_priority` | `character varying` | `YES` | `NULL::character varying` |
| `department` | `character varying` | `YES` | `NULL` |
| `brand_id` | `uuid` | `YES` | `NULL` |
| `soft_status` | `character varying` | `NO` | `'active'::character varying` |
| `archived_stg_bill_id` | `uuid` | `YES` | `NULL` |
| `original_amount` | `numeric` | `YES` | `NULL` |
| `fx_rate_id` | `uuid` | `YES` | `NULL` |
| `inr_amount_current` | `numeric` | `YES` | `NULL` |
| `fx_gain_loss` | `numeric` | `YES` | `NULL` |
| `fx_actual_inr` | `numeric` | `YES` | `NULL` |
| `fx_effective_rate` | `numeric` | `YES` | `NULL` |
| `fx_realized_cost` | `numeric` | `YES` | `NULL` |
| `verification_flag` | `bill_verification_flag` | `YES` | `NULL` |
| `verification_detail` | `text` | `YES` | `NULL` |
| `verified_at` | `timestamp with time zone` | `YES` | `NULL` |
| `cost_center_id` | `uuid` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `invoice_id`
**Foreign keys:**
- `account_id` -> `dim_ledger_accounts.account_id` (ON DELETE SET NULL)
- `end_customer_id` -> `dim_end_customers.end_customer_id` (ON DELETE RESTRICT)
- `fx_rate_id` -> `dim_fx_rates.rate_id` (ON DELETE NO ACTION)
- `stg_bill_id` -> `stg_bills.stg_bill_id` (ON DELETE NO ACTION)
- `stg_invoice_id` -> `stg_invoices.stg_invoice_id` (ON DELETE RESTRICT)
- `supplier_id` -> `dim_suppliers.supplier_id` (ON DELETE NO ACTION)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
- `upload_id` -> `raw_invoice_uploads.upload_id` (ON DELETE NO ACTION)
**Unique constraints:**
- `uq_fact_invoice_stg`: (`stg_invoice_id`)
**Indexes:**
- `fact_invoices_ap_bill_uniq`: `CREATE UNIQUE INDEX fact_invoices_ap_bill_uniq ON public.fact_invoices USING btree (tenant_id, stg_bill_id) WHERE (stg_bill_id IS NOT NULL)`
- `fact_invoices_pkey`: `CREATE UNIQUE INDEX fact_invoices_pkey ON public.fact_invoices USING btree (invoice_id)`
- `idx_fact_inv_customer`: `CREATE INDEX idx_fact_inv_customer ON public.fact_invoices USING btree (end_customer_id)`
- `idx_fact_inv_due`: `CREATE INDEX idx_fact_inv_due ON public.fact_invoices USING btree (due_date)`
- `idx_fact_inv_fiscal`: `CREATE INDEX idx_fact_inv_fiscal ON public.fact_invoices USING btree (tenant_id, fiscal_year, fiscal_quarter)`
- `idx_fact_inv_risk`: `CREATE INDEX idx_fact_inv_risk ON public.fact_invoices USING btree (risk_bucket)`
- `idx_fact_inv_status`: `CREATE INDEX idx_fact_inv_status ON public.fact_invoices USING btree (status)`
- `idx_fi_invoice_number`: `CREATE INDEX idx_fi_invoice_number ON public.fact_invoices USING btree (tenant_id, invoice_number)`
- `idx_fi_soft_status`: `CREATE INDEX idx_fi_soft_status ON public.fact_invoices USING btree (tenant_id, soft_status)`
- `idx_fi_tenant_created`: `CREATE INDEX idx_fi_tenant_created ON public.fact_invoices USING btree (tenant_id, created_at DESC)`
- `idx_fi_tenant_status`: `CREATE INDEX idx_fi_tenant_status ON public.fact_invoices USING btree (tenant_id, status)`
- `idx_fi_tenant_supplier`: `CREATE INDEX idx_fi_tenant_supplier ON public.fact_invoices USING btree (tenant_id, supplier_id)`
- `uq_fact_invoice_stg`: `CREATE UNIQUE INDEX uq_fact_invoice_stg ON public.fact_invoices USING btree (stg_invoice_id)`

---

## Table: `fact_payments`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `payment_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `stg_payment_id` | `uuid` | `NO` | `NULL` |
| `invoice_id` | `uuid` | `YES` | `NULL` |
| `end_customer_id` | `uuid` | `NO` | `NULL` |
| `bank_account_id` | `uuid` | `YES` | `NULL` |
| `payment_date` | `date` | `NO` | `NULL` |
| `fiscal_year` | `smallint` | `NO` | `NULL` |
| `fiscal_quarter` | `smallint` | `NO` | `NULL` |
| `amount` | `numeric` | `NO` | `NULL` |
| `currency_code` | `character` | `NO` | `'INR'::bpchar` |
| `payment_method` | `payment_method` | `YES` | `NULL` |
| `transaction_ref` | `text` | `YES` | `NULL` |
| `gateway` | `text` | `YES` | `NULL` |
| `status` | `payment_status` | `NO` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `source_updated_at` | `timestamp with time zone` | `NO` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `payment_id`
**Foreign keys:**
- `bank_account_id` -> `dim_bank_accounts.bank_account_id` (ON DELETE SET NULL)
- `end_customer_id` -> `dim_end_customers.end_customer_id` (ON DELETE RESTRICT)
- `invoice_id` -> `fact_invoices.invoice_id` (ON DELETE SET NULL)
- `stg_payment_id` -> `stg_payments.stg_payment_id` (ON DELETE RESTRICT)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
**Unique constraints:**
- `uq_fact_payment_stg`: (`stg_payment_id`)
**Indexes:**
- `fact_payments_pkey`: `CREATE UNIQUE INDEX fact_payments_pkey ON public.fact_payments USING btree (payment_id)`
- `uq_fact_payment_stg`: `CREATE UNIQUE INDEX uq_fact_payment_stg ON public.fact_payments USING btree (stg_payment_id)`

---

## Table: `fact_tds_ledger`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `ledger_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `supplier_id` | `uuid` | `NO` | `NULL` |
| `fiscal_year` | `text` | `NO` | `NULL` |
| `tds_section_code` | `text` | `NO` | `NULL` |
| `ytd_base_amount` | `numeric` | `NO` | `0` |
| `ytd_tds_deducted` | `numeric` | `NO` | `0` |
| `txn_count` | `integer` | `NO` | `0` |
| `threshold_crossed` | `boolean` | `NO` | `false` |
| `last_updated_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `ledger_id`
**Foreign keys:**
- `supplier_id` -> `dim_suppliers.supplier_id` (ON DELETE NO ACTION)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
**Unique constraints:**
- `fact_tds_ledger_tenant_id_supplier_id_fiscal_year_tds_secti_key`: (`tenant_id`, `supplier_id`, `fiscal_year`, `tds_section_code`)
**Indexes:**
- `fact_tds_ledger_pkey`: `CREATE UNIQUE INDEX fact_tds_ledger_pkey ON public.fact_tds_ledger USING btree (ledger_id)`
- `fact_tds_ledger_tenant_id_supplier_id_fiscal_year_tds_secti_key`: `CREATE UNIQUE INDEX fact_tds_ledger_tenant_id_supplier_id_fiscal_year_tds_secti_key ON public.fact_tds_ledger USING btree (tenant_id, supplier_id, fiscal_year, tds_section_code)`

---

## Table: `fx_agent_notes`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `note_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `target_type` | `text` | `NO` | `NULL` |
| `target_id` | `uuid` | `YES` | `NULL` |
| `currency_code` | `character` | `NO` | `NULL` |
| `severity` | `text` | `NO` | `NULL` |
| `note_text` | `text` | `NO` | `NULL` |
| `confidence` | `numeric` | `YES` | `NULL` |
| `context_snapshot` | `jsonb` | `YES` | `NULL` |
| `rate_at_generation` | `numeric` | `YES` | `NULL` |
| `is_current` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `note_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
**Unique constraints:** none
**Indexes:**
- `fx_agent_notes_pkey`: `CREATE UNIQUE INDEX fx_agent_notes_pkey ON public.fx_agent_notes USING btree (note_id)`
- `idx_fx_notes_target`: `CREATE INDEX idx_fx_notes_target ON public.fx_agent_notes USING btree (target_type, target_id, is_current) WHERE (is_current = true)`
- `idx_fx_notes_tenant`: `CREATE INDEX idx_fx_notes_tenant ON public.fx_agent_notes USING btree (tenant_id, target_type, created_at DESC)`

---

## Table: `fx_bill_payments`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `payment_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `invoice_id` | `uuid` | `NO` | `NULL` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `payment_date` | `date` | `NO` | `NULL` |
| `foreign_amount` | `numeric` | `NO` | `NULL` |
| `actual_inr` | `numeric` | `NO` | `NULL` |
| `effective_rate` | `numeric` | `NO` | `NULL` |
| `rbi_rate` | `numeric` | `YES` | `NULL` |
| `bank_spread` | `numeric` | `YES` | `NULL` |
| `bank_spread_pct` | `numeric` | `YES` | `NULL` |
| `reference_no` | `text` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `currency_code` | `character` | `YES` | `NULL` |
| `bank_name` | `text` | `YES` | `NULL` |
| `notes` | `text` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `payment_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
**Unique constraints:** none
**Indexes:**
- `fx_bill_payments_pkey`: `CREATE UNIQUE INDEX fx_bill_payments_pkey ON public.fx_bill_payments USING btree (payment_id)`
- `idx_fx_payments_bill`: `CREATE INDEX idx_fx_payments_bill ON public.fx_bill_payments USING btree (invoice_id)`
- `idx_fx_payments_tenant`: `CREATE INDEX idx_fx_payments_tenant ON public.fx_bill_payments USING btree (tenant_id, payment_date DESC)`

---

## Table: `fx_rate_alerts`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `alert_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `alert_type` | `text` | `NO` | `NULL` |
| `currency_code` | `character` | `NO` | `'USD'::bpchar` |
| `rate_date` | `date` | `NO` | `NULL` |
| `rate_inr` | `numeric` | `NO` | `NULL` |
| `threshold_inr` | `numeric` | `YES` | `NULL` |
| `direction` | `text` | `YES` | `NULL` |
| `recommendation` | `text` | `NO` | `NULL` |
| `affected_bill_ids` | `ARRAY` | `YES` | `NULL` |
| `affected_invoice_ids` | `ARRAY` | `YES` | `NULL` |
| `exposure_foreign` | `numeric` | `YES` | `NULL` |
| `exposure_inr` | `numeric` | `YES` | `NULL` |
| `status` | `text` | `NO` | `'ACTIVE'::text` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `dismissed_at` | `timestamp with time zone` | `YES` | `NULL` |
| `dismissed_by` | `text` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `alert_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
**Unique constraints:**
- `fx_rate_alerts_tenant_id_alert_type_currency_code_rate_date_key`: (`tenant_id`, `alert_type`, `currency_code`, `rate_date`)
**Indexes:**
- `fx_rate_alerts_pkey`: `CREATE UNIQUE INDEX fx_rate_alerts_pkey ON public.fx_rate_alerts USING btree (alert_id)`
- `fx_rate_alerts_tenant_id_alert_type_currency_code_rate_date_key`: `CREATE UNIQUE INDEX fx_rate_alerts_tenant_id_alert_type_currency_code_rate_date_key ON public.fx_rate_alerts USING btree (tenant_id, alert_type, currency_code, rate_date)`
- `idx_fx_alerts_active`: `CREATE INDEX idx_fx_alerts_active ON public.fx_rate_alerts USING btree (tenant_id, status, created_at DESC)`

---

## Table: `int_conflict_log`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `conflict_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `connection_id` | `uuid` | `YES` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `entity_type` | `text` | `NO` | `NULL` |
| `source_entity_id` | `text` | `NO` | `NULL` |
| `db_timestamp` | `timestamp with time zone` | `NO` | `NULL` |
| `event_timestamp` | `timestamp with time zone` | `NO` | `NULL` |
| `resolution` | `conflict_resolution` | `NO` | `NULL` |
| `db_payload` | `jsonb` | `YES` | `NULL` |
| `event_payload` | `jsonb` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `conflict_id`
**Foreign keys:**
- `connection_id` -> `int_connections.connection_id` (ON DELETE NO ACTION)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
**Unique constraints:** none
**Indexes:**
- `idx_int_conflict_log_tenant`: `CREATE INDEX idx_int_conflict_log_tenant ON public.int_conflict_log USING btree (tenant_id, created_at DESC)`
- `int_conflict_log_pkey`: `CREATE UNIQUE INDEX int_conflict_log_pkey ON public.int_conflict_log USING btree (conflict_id)`

---

## Table: `int_connections`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `connection_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `apideck_consumer_id` | `text` | `YES` | `NULL` |
| `apideck_service_id` | `text` | `YES` | `NULL` |
| `tally_agent_id` | `uuid` | `YES` | `NULL` |
| `tally_company_id` | `text` | `YES` | `NULL` |
| `tally_mode` | `tally_mode` | `YES` | `NULL` |
| `status` | `connection_status` | `NO` | `'DISCONNECTED'::connection_status` |
| `last_sync_at` | `timestamp with time zone` | `YES` | `NULL` |
| `last_cursor` | `text` | `YES` | `NULL` |
| `error_message` | `text` | `YES` | `NULL` |
| `consecutive_failures` | `smallint` | `NO` | `0` |
| `sync_config_jsonb` | `jsonb` | `NO` | `'{}'::jsonb` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `agent_api_key_hash` | `text` | `YES` | `NULL` |
| `agent_pairing_token` | `text` | `YES` | `NULL` |
| `agent_pairing_expires_at` | `timestamp with time zone` | `YES` | `NULL` |
| `agent_version` | `text` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `connection_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
**Unique constraints:**
- `int_connections_tenant_id_source_key`: (`tenant_id`, `source`)
**Indexes:**
- `idx_int_conn_tenant`: `CREATE INDEX idx_int_conn_tenant ON public.int_connections USING btree (tenant_id)`
- `idx_int_connections_active`: `CREATE INDEX idx_int_connections_active ON public.int_connections USING btree (tenant_id, status)`
- `idx_int_connections_agent_api_key_hash`: `CREATE INDEX idx_int_connections_agent_api_key_hash ON public.int_connections USING btree (agent_api_key_hash) WHERE (agent_api_key_hash IS NOT NULL)`
- `int_connections_pkey`: `CREATE UNIQUE INDEX int_connections_pkey ON public.int_connections USING btree (connection_id)`
- `int_connections_tenant_id_source_key`: `CREATE UNIQUE INDEX int_connections_tenant_id_source_key ON public.int_connections USING btree (tenant_id, source)`

---

## Table: `int_connector_limits`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `connector` | `text` | `NO` | `NULL` |
| `per_minute_limit` | `integer` | `YES` | `NULL` |
| `per_day_limit` | `integer` | `YES` | `NULL` |
| `concurrency_limit` | `integer` | `YES` | `4` |
| `safety_margin_pct` | `numeric` | `NO` | `15.0` |
| `retry_after_default_s` | `integer` | `NO` | `60` |
| `notes` | `text` | `YES` | `NULL` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `connector`
**Foreign keys:**
none
**Unique constraints:** none
**Indexes:**
- `int_connector_limits_pkey`: `CREATE UNIQUE INDEX int_connector_limits_pkey ON public.int_connector_limits USING btree (connector)`

---

## Table: `int_field_mappings`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `mapping_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `source_table` | `text` | `NO` | `NULL` |
| `source_field` | `text` | `NO` | `NULL` |
| `target_table` | `text` | `NO` | `NULL` |
| `target_field` | `text` | `NO` | `NULL` |
| `transform_fn` | `text` | `YES` | `NULL` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `mapping_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
**Unique constraints:** none
**Indexes:**
- `int_field_mappings_pkey`: `CREATE UNIQUE INDEX int_field_mappings_pkey ON public.int_field_mappings USING btree (mapping_id)`

---

## Table: `int_sync_log`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `log_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `connection_id` | `uuid` | `YES` | `NULL` |
| `job_id` | `text` | `YES` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `entity_type` | `text` | `YES` | `NULL` |
| `started_at` | `timestamp with time zone` | `NO` | `NULL` |
| `completed_at` | `timestamp with time zone` | `YES` | `NULL` |
| `records_fetched` | `integer` | `NO` | `0` |
| `records_inserted` | `integer` | `NO` | `0` |
| `records_updated` | `integer` | `NO` | `0` |
| `records_errored` | `integer` | `NO` | `0` |
| `status` | `sync_job_status` | `NO` | `'RUNNING'::sync_job_status` |
| `last_cursor` | `text` | `YES` | `NULL` |
| `error_message` | `text` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `log_id`
**Foreign keys:**
- `connection_id` -> `int_connections.connection_id` (ON DELETE NO ACTION)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
**Unique constraints:** none
**Indexes:**
- `idx_int_sync_log_tenant`: `CREATE INDEX idx_int_sync_log_tenant ON public.int_sync_log USING btree (tenant_id, created_at DESC)`
- `idx_int_sync_tenant`: `CREATE INDEX idx_int_sync_tenant ON public.int_sync_log USING btree (tenant_id)`
- `int_sync_log_pkey`: `CREATE UNIQUE INDEX int_sync_log_pkey ON public.int_sync_log USING btree (log_id)`

---

## Table: `ledger_mapping_log`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `supplier_id` | `uuid` | `YES` | `NULL` |
| `hsn_code` | `text` | `YES` | `NULL` |
| `description` | `text` | `YES` | `NULL` |
| `account_id` | `uuid` | `NO` | `NULL` |
| `source` | `text` | `NO` | `'user'::text` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:** none
**Indexes:**
- `idx_lml_tenant_hsn`: `CREATE INDEX idx_lml_tenant_hsn ON public.ledger_mapping_log USING btree (tenant_id, hsn_code)`
- `idx_lml_tenant_supplier`: `CREATE INDEX idx_lml_tenant_supplier ON public.ledger_mapping_log USING btree (tenant_id, supplier_id)`
- `ledger_mapping_log_pkey`: `CREATE UNIQUE INDEX ledger_mapping_log_pkey ON public.ledger_mapping_log USING btree (id)`

---

## Table: `mapping_review_queue`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `review_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `line_item_id` | `uuid` | `NO` | `NULL` |
| `line_item_kind` | `text` | `NO` | `NULL` |
| `query_text` | `text` | `NO` | `NULL` |
| `candidates` | `jsonb` | `NO` | `NULL` |
| `status` | `text` | `NO` | `'PENDING'::text` |
| `chosen_item_code` | `text` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `resolved_at` | `timestamp with time zone` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `review_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
**Unique constraints:** none
**Indexes:**
- `idx_mapping_review_status`: `CREATE INDEX idx_mapping_review_status ON public.mapping_review_queue USING btree (tenant_id, status, created_at DESC)`
- `mapping_review_queue_pkey`: `CREATE UNIQUE INDEX mapping_review_queue_pkey ON public.mapping_review_queue USING btree (review_id)`

---

## Table: `org_user_access_levels`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `access_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `user_id` | `uuid` | `NO` | `NULL` |
| `access_level` | `text` | `NO` | `NULL` |
| `can_read` | `boolean` | `NO` | `false` |
| `can_edit` | `boolean` | `NO` | `false` |
| `can_approve_workflow` | `boolean` | `NO` | `false` |
| `can_export_data` | `boolean` | `NO` | `false` |
| `can_update` | `boolean` | `NO` | `false` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `access_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
- `user_id` -> `org_users.user_id` (ON DELETE CASCADE)
**Unique constraints:**
- `uq_org_user_access`: (`tenant_id`, `user_id`, `access_level`)
**Indexes:**
- `org_user_access_levels_pkey`: `CREATE UNIQUE INDEX org_user_access_levels_pkey ON public.org_user_access_levels USING btree (access_id)`
- `uq_org_user_access`: `CREATE UNIQUE INDEX uq_org_user_access ON public.org_user_access_levels USING btree (tenant_id, user_id, access_level)`

---

## Table: `org_users`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `user_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `full_name` | `text` | `NO` | `NULL` |
| `work_email` | `text` | `NO` | `NULL` |
| `employee_id` | `text` | `YES` | `NULL` |
| `office_branch` | `text` | `YES` | `NULL` |
| `contact_number` | `text` | `YES` | `NULL` |
| `designation` | `text` | `YES` | `NULL` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `user_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
**Unique constraints:**
- `uq_org_users_tenant_email`: (`tenant_id`, `work_email`)
- `uq_org_users_tenant_employee`: (`tenant_id`, `employee_id`)
**Indexes:**
- `org_users_pkey`: `CREATE UNIQUE INDEX org_users_pkey ON public.org_users USING btree (user_id)`
- `uq_org_users_tenant_email`: `CREATE UNIQUE INDEX uq_org_users_tenant_email ON public.org_users USING btree (tenant_id, work_email)`
- `uq_org_users_tenant_employee`: `CREATE UNIQUE INDEX uq_org_users_tenant_employee ON public.org_users USING btree (tenant_id, employee_id)`

---

## Table: `payment_schedules`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `schedule_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `cycle_type` | `payment_cycle_type` | `NO` | `NULL` |
| `stg_bill_id` | `uuid` | `YES` | `NULL` |
| `stg_invoice_id` | `uuid` | `YES` | `NULL` |
| `supplier_id` | `uuid` | `YES` | `NULL` |
| `end_customer_id` | `uuid` | `YES` | `NULL` |
| `installment_number` | `smallint` | `NO` | `NULL` |
| `due_date` | `date` | `NO` | `NULL` |
| `scheduled_amount` | `numeric` | `NO` | `NULL` |
| `paid_amount` | `numeric` | `NO` | `0` |
| `outstanding_amount` | `numeric` | `YES` | `NULL` |
| `payment_term_days` | `smallint` | `NO` | `NULL` |
| `currency_code` | `character` | `NO` | `'INR'::bpchar` |
| `status` | `schedule_status` | `NO` | `'PENDING'::schedule_status` |
| `notes` | `text` | `YES` | `NULL` |
| `reminder_sent_at` | `timestamp with time zone` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `schedule_id`
**Foreign keys:**
- `end_customer_id` -> `dim_end_customers.end_customer_id` (ON DELETE RESTRICT)
- `stg_bill_id` -> `stg_bills.stg_bill_id` (ON DELETE RESTRICT)
- `stg_invoice_id` -> `stg_invoices.stg_invoice_id` (ON DELETE RESTRICT)
- `supplier_id` -> `dim_suppliers.supplier_id` (ON DELETE RESTRICT)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
**Unique constraints:** none
**Indexes:**
- `idx_psched_bill`: `CREATE INDEX idx_psched_bill ON public.payment_schedules USING btree (stg_bill_id)`
- `idx_psched_customer`: `CREATE INDEX idx_psched_customer ON public.payment_schedules USING btree (end_customer_id)`
- `idx_psched_due`: `CREATE INDEX idx_psched_due ON public.payment_schedules USING btree (due_date)`
- `idx_psched_invoice`: `CREATE INDEX idx_psched_invoice ON public.payment_schedules USING btree (stg_invoice_id)`
- `idx_psched_status`: `CREATE INDEX idx_psched_status ON public.payment_schedules USING btree (status)`
- `idx_psched_supplier`: `CREATE INDEX idx_psched_supplier ON public.payment_schedules USING btree (supplier_id)`
- `idx_psched_tenant_cycle`: `CREATE INDEX idx_psched_tenant_cycle ON public.payment_schedules USING btree (tenant_id, cycle_type, status, due_date)`
- `payment_schedules_pkey`: `CREATE UNIQUE INDEX payment_schedules_pkey ON public.payment_schedules USING btree (schedule_id)`
- `uq_schedule_bill_installment`: `CREATE UNIQUE INDEX uq_schedule_bill_installment ON public.payment_schedules USING btree (stg_bill_id, installment_number) WHERE (stg_bill_id IS NOT NULL)`
- `uq_schedule_invoice_installment`: `CREATE UNIQUE INDEX uq_schedule_invoice_installment ON public.payment_schedules USING btree (stg_invoice_id, installment_number) WHERE (stg_invoice_id IS NOT NULL)`

---

## Table: `payment_transactions`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `txn_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `schedule_id` | `uuid` | `NO` | `NULL` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `cycle_type` | `payment_cycle_type` | `NO` | `NULL` |
| `paid_amount` | `numeric` | `NO` | `NULL` |
| `payment_date` | `date` | `NO` | `NULL` |
| `value_date` | `date` | `YES` | `NULL` |
| `payment_mode` | `payment_method` | `NO` | `NULL` |
| `reference_number` | `character varying` | `YES` | `NULL` |
| `bank_account_id` | `uuid` | `YES` | `NULL` |
| `status` | `txn_status` | `NO` | `'INITIATED'::txn_status` |
| `failure_reason` | `text` | `YES` | `NULL` |
| `notes` | `text` | `YES` | `NULL` |
| `recorded_by` | `uuid` | `NO` | `NULL` |
| `approved_by` | `uuid` | `YES` | `NULL` |
| `approved_at` | `timestamp with time zone` | `YES` | `NULL` |
| `is_reconciled` | `boolean` | `NO` | `false` |
| `stg_statement_id` | `uuid` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `txn_id`
**Foreign keys:**
- `approved_by` -> `org_users.user_id` (ON DELETE SET NULL)
- `bank_account_id` -> `dim_bank_accounts.bank_account_id` (ON DELETE SET NULL)
- `recorded_by` -> `org_users.user_id` (ON DELETE RESTRICT)
- `schedule_id` -> `payment_schedules.schedule_id` (ON DELETE RESTRICT)
- `stg_statement_id` -> `stg_bank_statements.stg_statement_id` (ON DELETE SET NULL)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
**Unique constraints:** none
**Indexes:**
- `idx_ptxn_cycle`: `CREATE INDEX idx_ptxn_cycle ON public.payment_transactions USING btree (cycle_type)`
- `idx_ptxn_date`: `CREATE INDEX idx_ptxn_date ON public.payment_transactions USING btree (payment_date)`
- `idx_ptxn_reconciled`: `CREATE INDEX idx_ptxn_reconciled ON public.payment_transactions USING btree (is_reconciled)`
- `idx_ptxn_ref`: `CREATE INDEX idx_ptxn_ref ON public.payment_transactions USING btree (reference_number)`
- `idx_ptxn_schedule`: `CREATE INDEX idx_ptxn_schedule ON public.payment_transactions USING btree (schedule_id)`
- `idx_ptxn_status`: `CREATE INDEX idx_ptxn_status ON public.payment_transactions USING btree (status)`
- `payment_transactions_pkey`: `CREATE UNIQUE INDEX payment_transactions_pkey ON public.payment_transactions USING btree (txn_id)`

---

## Table: `poc_email_messages`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `message_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `supplier_id` | `uuid` | `YES` | `NULL` |
| `contact_id` | `uuid` | `YES` | `NULL` |
| `direction` | `text` | `NO` | `NULL` |
| `gmail_msg_id` | `text` | `YES` | `NULL` |
| `thread_id` | `text` | `YES` | `NULL` |
| `from_email` | `text` | `YES` | `NULL` |
| `to_email` | `text` | `YES` | `NULL` |
| `subject` | `text` | `YES` | `NULL` |
| `body` | `text` | `YES` | `NULL` |
| `sent_at` | `timestamp with time zone` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `embedded_at` | `timestamp with time zone` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `message_id`
**Foreign keys:**
none
**Unique constraints:** none
**Indexes:**
- `idx_pem_tenant_supplier`: `CREATE INDEX idx_pem_tenant_supplier ON public.poc_email_messages USING btree (tenant_id, supplier_id)`
- `idx_pem_thread`: `CREATE INDEX idx_pem_thread ON public.poc_email_messages USING btree (tenant_id, thread_id)`
- `poc_email_messages_pkey`: `CREATE UNIQUE INDEX poc_email_messages_pkey ON public.poc_email_messages USING btree (message_id)`
- `uq_pem_gmail`: `CREATE UNIQUE INDEX uq_pem_gmail ON public.poc_email_messages USING btree (tenant_id, gmail_msg_id, direction) WHERE (gmail_msg_id IS NOT NULL)`

---

## Table: `raw_api_payloads`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `payload_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `sync_job_id` | `text` | `YES` | `NULL` |
| `entity_type` | `entity_type` | `NO` | `NULL` |
| `raw_payload` | `jsonb` | `NO` | `NULL` |
| `is_processed` | `boolean` | `NO` | `false` |
| `received_at` | `timestamp with time zone` | `NO` | `now()` |
| `retry_count` | `integer` | `NO` | `0` |

### Keys and Constraints
**Primary keys:** `payload_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
**Unique constraints:** none
**Indexes:**
- `idx_raw_payload_entity`: `CREATE INDEX idx_raw_payload_entity ON public.raw_api_payloads USING btree (entity_type)`
- `idx_raw_payload_processed`: `CREATE INDEX idx_raw_payload_processed ON public.raw_api_payloads USING btree (tenant_id, is_processed)`
- `raw_api_payloads_pkey`: `CREATE UNIQUE INDEX raw_api_payloads_pkey ON public.raw_api_payloads USING btree (payload_id)`

---

## Table: `raw_ingestion_errors`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `error_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `payload_id` | `uuid` | `YES` | `NULL` |
| `upload_id` | `uuid` | `YES` | `NULL` |
| `webhook_event_id` | `uuid` | `YES` | `NULL` |
| `stage` | `text` | `NO` | `NULL` |
| `error_type` | `text` | `NO` | `NULL` |
| `validation_errors` | `jsonb` | `NO` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `error_id`
**Foreign keys:**
- `payload_id` -> `raw_api_payloads.payload_id` (ON DELETE SET NULL)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
- `upload_id` -> `raw_invoice_uploads.upload_id` (ON DELETE SET NULL)
- `webhook_event_id` -> `raw_webhook_events.event_id` (ON DELETE SET NULL)
**Unique constraints:** none
**Indexes:**
- `raw_ingestion_errors_pkey`: `CREATE UNIQUE INDEX raw_ingestion_errors_pkey ON public.raw_ingestion_errors USING btree (error_id)`

---

## Table: `raw_invoice_uploads`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `upload_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `file_name` | `text` | `NO` | `NULL` |
| `file_type` | `upload_file_type` | `NO` | `NULL` |
| `file_size_bytes` | `integer` | `YES` | `NULL` |
| `storage_path` | `text` | `NO` | `NULL` |
| `status` | `upload_status` | `NO` | `'RECEIVED'::upload_status` |
| `ocr_engine_used` | `ocr_engine` | `YES` | `NULL` |
| `error_message` | `text` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `uploaded_by_name` | `character varying` | `YES` | `NULL` |
| `uploaded_by_role` | `character varying` | `YES` | `NULL` |
| `file_data` | `bytea` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `upload_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
**Unique constraints:**
- `raw_invoice_uploads_tenant_file_uniq`: (`tenant_id`, `file_name`)
**Indexes:**
- `idx_raw_uploads_status`: `CREATE INDEX idx_raw_uploads_status ON public.raw_invoice_uploads USING btree (tenant_id, status)`
- `raw_invoice_uploads_pkey`: `CREATE UNIQUE INDEX raw_invoice_uploads_pkey ON public.raw_invoice_uploads USING btree (upload_id)`
- `raw_invoice_uploads_tenant_file_uniq`: `CREATE UNIQUE INDEX raw_invoice_uploads_tenant_file_uniq ON public.raw_invoice_uploads USING btree (tenant_id, file_name)`

---

## Table: `raw_ocr_results`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `ocr_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `upload_id` | `uuid` | `NO` | `NULL` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `engine` | `ocr_engine` | `NO` | `NULL` |
| `page_number` | `smallint` | `NO` | `1` |
| `raw_response_jsonb` | `jsonb` | `NO` | `NULL` |
| `confidence_jsonb` | `jsonb` | `NO` | `'{}'::jsonb` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `ocr_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
- `upload_id` -> `raw_invoice_uploads.upload_id` (ON DELETE CASCADE)
**Unique constraints:** none
**Indexes:**
- `idx_raw_ocr_upload`: `CREATE INDEX idx_raw_ocr_upload ON public.raw_ocr_results USING btree (upload_id)`
- `raw_ocr_results_pkey`: `CREATE UNIQUE INDEX raw_ocr_results_pkey ON public.raw_ocr_results USING btree (ocr_id)`

---

## Table: `raw_webhook_events`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `event_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `YES` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `event_type` | `text` | `NO` | `NULL` |
| `raw_payload` | `jsonb` | `NO` | `NULL` |
| `is_signature_verified` | `boolean` | `NO` | `false` |
| `correlation_id` | `uuid` | `YES` | `NULL` |
| `received_at` | `timestamp with time zone` | `NO` | `now()` |
| `is_processed` | `boolean` | `NO` | `false` |
| `processing_error` | `text` | `YES` | `NULL` |
| `retry_count` | `integer` | `NO` | `0` |

### Keys and Constraints
**Primary keys:** `event_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE SET NULL)
**Unique constraints:** none
**Indexes:**
- `idx_webhook_processed`: `CREATE INDEX idx_webhook_processed ON public.raw_webhook_events USING btree (tenant_id, is_processed)`
- `raw_webhook_events_pkey`: `CREATE UNIQUE INDEX raw_webhook_events_pkey ON public.raw_webhook_events USING btree (event_id)`

---

## Table: `remitter_accounts`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `account_number` | `text` | `NO` | `NULL` |
| `bank_name` | `text` | `NO` | `NULL` |
| `ifsc` | `text` | `YES` | `NULL` |
| `bank_provider` | `text` | `NO` | `'ICICI'::text` |
| `balance` | `numeric` | `NO` | `5000000` |
| `notify_email` | `text` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:**
- `remitter_accounts_tenant_id_account_number_key`: (`tenant_id`, `account_number`)
**Indexes:**
- `remitter_accounts_pkey`: `CREATE UNIQUE INDEX remitter_accounts_pkey ON public.remitter_accounts USING btree (id)`
- `remitter_accounts_tenant_id_account_number_key`: `CREATE UNIQUE INDEX remitter_accounts_tenant_id_account_number_key ON public.remitter_accounts USING btree (tenant_id, account_number)`

---

## Table: `sap_item_aliases`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `alias_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `alias_text` | `text` | `NO` | `NULL` |
| `item_code` | `text` | `NO` | `NULL` |
| `source` | `text` | `NO` | `'operator'::text` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `alias_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
**Unique constraints:**
- `sap_item_aliases_tenant_id_alias_text_key`: (`tenant_id`, `alias_text`)
**Indexes:**
- `sap_item_aliases_pkey`: `CREATE UNIQUE INDEX sap_item_aliases_pkey ON public.sap_item_aliases USING btree (alias_id)`
- `sap_item_aliases_tenant_id_alias_text_key`: `CREATE UNIQUE INDEX sap_item_aliases_tenant_id_alias_text_key ON public.sap_item_aliases USING btree (tenant_id, alias_text)`

---

## Table: `sap_item_catalog`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `catalog_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `item_code` | `text` | `NO` | `NULL` |
| `material_desc` | `text` | `NO` | `NULL` |
| `hsn_code` | `text` | `YES` | `NULL` |
| `embedding_text` | `text` | `NO` | `NULL` |
| `text_hash` | `text` | `NO` | `NULL` |
| `is_active` | `boolean` | `NO` | `true` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `catalog_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
**Unique constraints:**
- `sap_item_catalog_tenant_code_desc_key`: (`tenant_id`, `item_code`, `material_desc`)
- `sap_item_catalog_tenant_id_item_code_material_desc_key`: (`tenant_id`, `item_code`, `material_desc`)
**Indexes:**
- `idx_sap_catalog_hsn`: `CREATE INDEX idx_sap_catalog_hsn ON public.sap_item_catalog USING btree (tenant_id, hsn_code)`
- `idx_sap_catalog_item`: `CREATE INDEX idx_sap_catalog_item ON public.sap_item_catalog USING btree (tenant_id, item_code)`
- `idx_sap_catalog_tenant`: `CREATE INDEX idx_sap_catalog_tenant ON public.sap_item_catalog USING btree (tenant_id)`
- `sap_item_catalog_pkey`: `CREATE UNIQUE INDEX sap_item_catalog_pkey ON public.sap_item_catalog USING btree (catalog_id)`
- `sap_item_catalog_tenant_code_desc_key`: `CREATE UNIQUE INDEX sap_item_catalog_tenant_code_desc_key ON public.sap_item_catalog USING btree (tenant_id, item_code, material_desc)`
- `sap_item_catalog_tenant_id_item_code_material_desc_key`: `CREATE UNIQUE INDEX sap_item_catalog_tenant_id_item_code_material_desc_key ON public.sap_item_catalog USING btree (tenant_id, item_code, material_desc)`

---

## Table: `sap_item_embeddings`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `catalog_id` | `uuid` | `NO` | `NULL` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `model` | `text` | `NO` | `NULL` |
| `dim` | `integer` | `NO` | `NULL` |
| `embedding` | `ARRAY` | `NO` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `catalog_id`
**Foreign keys:**
- `catalog_id` -> `sap_item_catalog.catalog_id` (ON DELETE CASCADE)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
**Unique constraints:** none
**Indexes:**
- `idx_sap_emb_tenant`: `CREATE INDEX idx_sap_emb_tenant ON public.sap_item_embeddings USING btree (tenant_id)`
- `sap_item_embeddings_pkey`: `CREATE UNIQUE INDEX sap_item_embeddings_pkey ON public.sap_item_embeddings USING btree (catalog_id)`

---

## Table: `sap_item_vendors`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `item_code` | `text` | `NO` | `NULL` |
| `vendor_gstn` | `text` | `NO` | `''::text` |
| `supplier_code` | `text` | `NO` | `NULL` |
| `supplier_name` | `text` | `YES` | `NULL` |
| `freq` | `integer` | `NO` | `1` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
**Unique constraints:**
- `sap_item_vendors_tenant_id_item_code_vendor_gstn_supplier_c_key`: (`tenant_id`, `item_code`, `vendor_gstn`, `supplier_code`)
**Indexes:**
- `idx_sap_itemvend_gstn`: `CREATE INDEX idx_sap_itemvend_gstn ON public.sap_item_vendors USING btree (tenant_id, vendor_gstn)`
- `idx_sap_itemvend_item`: `CREATE INDEX idx_sap_itemvend_item ON public.sap_item_vendors USING btree (tenant_id, item_code)`
- `sap_item_vendors_pkey`: `CREATE UNIQUE INDEX sap_item_vendors_pkey ON public.sap_item_vendors USING btree (id)`
- `sap_item_vendors_tenant_id_item_code_vendor_gstn_supplier_c_key`: `CREATE UNIQUE INDEX sap_item_vendors_tenant_id_item_code_vendor_gstn_supplier_c_key ON public.sap_item_vendors USING btree (tenant_id, item_code, vendor_gstn, supplier_code)`

---

## Table: `stg_bank_statements`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `stg_statement_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `source_txn_id` | `text` | `NO` | `NULL` |
| `source_updated_at` | `timestamp with time zone` | `NO` | `NULL` |
| `bank_account_id` | `uuid` | `YES` | `NULL` |
| `transaction_date` | `date` | `NO` | `NULL` |
| `value_date` | `date` | `YES` | `NULL` |
| `description` | `text` | `YES` | `NULL` |
| `credit_amount` | `numeric` | `NO` | `0` |
| `debit_amount` | `numeric` | `NO` | `0` |
| `running_balance` | `numeric` | `YES` | `NULL` |
| `reference` | `text` | `YES` | `NULL` |
| `is_reconciled` | `boolean` | `NO` | `false` |
| `stg_payment_id` | `uuid` | `YES` | `NULL` |
| `sync_status` | `sync_status` | `NO` | `'PENDING'::sync_status` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `stg_statement_id`
**Foreign keys:**
- `bank_account_id` -> `dim_bank_accounts.bank_account_id` (ON DELETE SET NULL)
- `stg_payment_id` -> `stg_payments.stg_payment_id` (ON DELETE SET NULL)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
**Unique constraints:**
- `uq_stg_bank_stmt_source`: (`tenant_id`, `source`, `source_txn_id`)
**Indexes:**
- `idx_stg_stmt_bank`: `CREATE INDEX idx_stg_stmt_bank ON public.stg_bank_statements USING btree (bank_account_id)`
- `idx_stg_stmt_reconciled`: `CREATE INDEX idx_stg_stmt_reconciled ON public.stg_bank_statements USING btree (is_reconciled)`
- `stg_bank_statements_pkey`: `CREATE UNIQUE INDEX stg_bank_statements_pkey ON public.stg_bank_statements USING btree (stg_statement_id)`
- `uq_stg_bank_stmt_source`: `CREATE UNIQUE INDEX uq_stg_bank_stmt_source ON public.stg_bank_statements USING btree (tenant_id, source, source_txn_id)`

---

## Table: `stg_bill_line_items`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `line_item_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `stg_bill_id` | `uuid` | `NO` | `NULL` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `line_number` | `smallint` | `NO` | `NULL` |
| `product_id` | `uuid` | `YES` | `NULL` |
| `product_name` | `text` | `NO` | `NULL` |
| `hsn_code` | `text` | `YES` | `NULL` |
| `quantity` | `numeric` | `YES` | `NULL` |
| `unit` | `text` | `YES` | `NULL` |
| `unit_price` | `numeric` | `YES` | `NULL` |
| `discount_amount` | `numeric` | `NO` | `0` |
| `tax_rate_percent` | `numeric` | `NO` | `0` |
| `tax_amount` | `numeric` | `NO` | `0` |
| `line_total` | `numeric` | `NO` | `NULL` |
| `cost_centre_id` | `uuid` | `YES` | `NULL` |
| `account_id` | `uuid` | `YES` | `NULL` |
| `sap_item_code` | `text` | `YES` | `NULL` |
| `sap_material_desc` | `text` | `YES` | `NULL` |
| `sap_map_score` | `numeric` | `YES` | `NULL` |
| `sap_map_status` | `text` | `YES` | `NULL` |
| `service_month` | `text` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `line_item_id`
**Foreign keys:**
- `account_id` -> `dim_ledger_accounts.account_id` (ON DELETE SET NULL)
- `cost_centre_id` -> `dim_cost_centres.cost_centre_id` (ON DELETE SET NULL)
- `product_id` -> `dim_products.product_id` (ON DELETE SET NULL)
- `stg_bill_id` -> `stg_bills.stg_bill_id` (ON DELETE CASCADE)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
**Unique constraints:** none
**Indexes:**
- `stg_bill_line_items_pkey`: `CREATE UNIQUE INDEX stg_bill_line_items_pkey ON public.stg_bill_line_items USING btree (line_item_id)`

---

## Table: `stg_bills`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `stg_bill_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `source_bill_id` | `text` | `NO` | `NULL` |
| `source_updated_at` | `timestamp with time zone` | `NO` | `NULL` |
| `raw_payload_id` | `uuid` | `YES` | `NULL` |
| `bill_number` | `text` | `NO` | `NULL` |
| `bill_date` | `date` | `NO` | `NULL` |
| `due_date` | `date` | `YES` | `NULL` |
| `supplier_id` | `uuid` | `NO` | `NULL` |
| `currency_code` | `character` | `NO` | `'INR'::bpchar` |
| `subtotal` | `numeric` | `NO` | `0` |
| `discount_amount` | `numeric` | `NO` | `0` |
| `tax_amount` | `numeric` | `NO` | `0` |
| `total_amount` | `numeric` | `NO` | `NULL` |
| `amount_paid` | `numeric` | `NO` | `0` |
| `outstanding_amount` | `numeric` | `YES` | `NULL` |
| `status` | `invoice_status` | `NO` | `'DRAFT'::invoice_status` |
| `gstin_seller` | `character` | `YES` | `NULL` |
| `validation_flag` | `validation_flag` | `NO` | `'OK'::validation_flag` |
| `sync_status` | `sync_status` | `NO` | `'PENDING'::sync_status` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |
| `upload_id` | `uuid` | `YES` | `NULL` |
| `category` | `character varying` | `YES` | `NULL` |
| `tds_section_code` | `text` | `YES` | `NULL` |
| `tds_rate_percent` | `numeric` | `YES` | `NULL` |
| `tds_base_amount` | `numeric` | `YES` | `NULL` |
| `tds_amount` | `numeric` | `YES` | `NULL` |
| `net_payable` | `numeric` | `YES` | `NULL` |
| `tds_status` | `text` | `YES` | `'PENDING'::text` |
| `tds_section_id` | `uuid` | `YES` | `NULL` |
| `tds_rate` | `numeric` | `YES` | `NULL` |
| `original_amount` | `numeric` | `YES` | `NULL` |
| `fx_rate_id` | `uuid` | `YES` | `NULL` |
| `vendor_sap_code` | `text` | `YES` | `NULL` |
| `service_month` | `text` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `stg_bill_id`
**Foreign keys:**
- `fx_rate_id` -> `dim_fx_rates.rate_id` (ON DELETE NO ACTION)
- `raw_payload_id` -> `raw_api_payloads.payload_id` (ON DELETE SET NULL)
- `supplier_id` -> `dim_suppliers.supplier_id` (ON DELETE RESTRICT)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
- `upload_id` -> `raw_invoice_uploads.upload_id` (ON DELETE NO ACTION)
**Unique constraints:**
- `uq_stg_bill_source`: (`tenant_id`, `source`, `source_bill_id`)
**Indexes:**
- `idx_stg_bill_status`: `CREATE INDEX idx_stg_bill_status ON public.stg_bills USING btree (status)`
- `idx_stg_bill_supplier`: `CREATE INDEX idx_stg_bill_supplier ON public.stg_bills USING btree (supplier_id)`
- `idx_stg_bill_tenant`: `CREATE INDEX idx_stg_bill_tenant ON public.stg_bills USING btree (tenant_id)`
- `idx_stg_bills_supplier`: `CREATE INDEX idx_stg_bills_supplier ON public.stg_bills USING btree (tenant_id, supplier_id)`
- `idx_stg_bills_tenant_status`: `CREATE INDEX idx_stg_bills_tenant_status ON public.stg_bills USING btree (tenant_id, status)`
- `stg_bills_pkey`: `CREATE UNIQUE INDEX stg_bills_pkey ON public.stg_bills USING btree (stg_bill_id)`
- `uq_stg_bill_source`: `CREATE UNIQUE INDEX uq_stg_bill_source ON public.stg_bills USING btree (tenant_id, source, source_bill_id)`

---

## Table: `stg_credit_notes`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `stg_credit_note_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `source_cn_id` | `text` | `NO` | `NULL` |
| `source_updated_at` | `timestamp with time zone` | `NO` | `NULL` |
| `raw_payload_id` | `uuid` | `YES` | `NULL` |
| `stg_invoice_id` | `uuid` | `YES` | `NULL` |
| `credit_note_number` | `text` | `NO` | `NULL` |
| `credit_note_date` | `date` | `NO` | `NULL` |
| `end_customer_id` | `uuid` | `NO` | `NULL` |
| `currency_code` | `character` | `NO` | `'INR'::bpchar` |
| `amount` | `numeric` | `NO` | `NULL` |
| `reason` | `text` | `YES` | `NULL` |
| `status` | `invoice_status` | `NO` | `'DRAFT'::invoice_status` |
| `sync_status` | `sync_status` | `NO` | `'PENDING'::sync_status` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `stg_credit_note_id`
**Foreign keys:**
- `end_customer_id` -> `dim_end_customers.end_customer_id` (ON DELETE RESTRICT)
- `raw_payload_id` -> `raw_api_payloads.payload_id` (ON DELETE SET NULL)
- `stg_invoice_id` -> `stg_invoices.stg_invoice_id` (ON DELETE SET NULL)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
**Unique constraints:**
- `uq_stg_cn_source`: (`tenant_id`, `source`, `source_cn_id`)
**Indexes:**
- `stg_credit_notes_pkey`: `CREATE UNIQUE INDEX stg_credit_notes_pkey ON public.stg_credit_notes USING btree (stg_credit_note_id)`
- `uq_stg_cn_source`: `CREATE UNIQUE INDEX uq_stg_cn_source ON public.stg_credit_notes USING btree (tenant_id, source, source_cn_id)`

---

## Table: `stg_invoice_line_items`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `line_item_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `stg_invoice_id` | `uuid` | `NO` | `NULL` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `line_number` | `smallint` | `NO` | `NULL` |
| `product_id` | `uuid` | `YES` | `NULL` |
| `product_name` | `text` | `NO` | `NULL` |
| `hsn_code` | `text` | `YES` | `NULL` |
| `description` | `text` | `YES` | `NULL` |
| `quantity` | `numeric` | `NO` | `NULL` |
| `unit` | `text` | `YES` | `NULL` |
| `unit_price` | `numeric` | `NO` | `NULL` |
| `discount_percent` | `numeric` | `NO` | `0` |
| `discount_amount` | `numeric` | `NO` | `0` |
| `tax_rate_id` | `uuid` | `YES` | `NULL` |
| `tax_rate_percent` | `numeric` | `NO` | `0` |
| `tax_amount` | `numeric` | `NO` | `0` |
| `line_total` | `numeric` | `NO` | `NULL` |
| `cost_centre_id` | `uuid` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `line_item_id`
**Foreign keys:**
- `cost_centre_id` -> `dim_cost_centres.cost_centre_id` (ON DELETE SET NULL)
- `product_id` -> `dim_products.product_id` (ON DELETE SET NULL)
- `stg_invoice_id` -> `stg_invoices.stg_invoice_id` (ON DELETE CASCADE)
- `tax_rate_id` -> `dim_tax_rates.tax_rate_id` (ON DELETE SET NULL)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
**Unique constraints:** none
**Indexes:**
- `stg_invoice_line_items_pkey`: `CREATE UNIQUE INDEX stg_invoice_line_items_pkey ON public.stg_invoice_line_items USING btree (line_item_id)`

---

## Table: `stg_invoices`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `stg_invoice_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `source_invoice_id` | `text` | `NO` | `NULL` |
| `source_updated_at` | `timestamp with time zone` | `NO` | `NULL` |
| `raw_payload_id` | `uuid` | `YES` | `NULL` |
| `raw_webhook_event_id` | `uuid` | `YES` | `NULL` |
| `upload_id` | `uuid` | `YES` | `NULL` |
| `invoice_number` | `text` | `NO` | `NULL` |
| `invoice_date` | `date` | `NO` | `NULL` |
| `due_date` | `date` | `YES` | `NULL` |
| `end_customer_id` | `uuid` | `NO` | `NULL` |
| `account_id` | `uuid` | `YES` | `NULL` |
| `currency_code` | `character` | `NO` | `'INR'::bpchar` |
| `subtotal` | `numeric` | `NO` | `0` |
| `discount_amount` | `numeric` | `NO` | `0` |
| `tax_amount` | `numeric` | `NO` | `0` |
| `total_amount` | `numeric` | `NO` | `NULL` |
| `amount_paid` | `numeric` | `NO` | `0` |
| `outstanding_amount` | `numeric` | `YES` | `NULL` |
| `status` | `invoice_status` | `NO` | `'DRAFT'::invoice_status` |
| `gstin_seller` | `character` | `YES` | `NULL` |
| `gstin_buyer` | `character` | `YES` | `NULL` |
| `place_of_supply` | `text` | `YES` | `NULL` |
| `reverse_charge` | `boolean` | `NO` | `false` |
| `validation_flag` | `validation_flag` | `NO` | `'OK'::validation_flag` |
| `ocr_confidence` | `numeric` | `YES` | `NULL` |
| `ocr_engine_used` | `ocr_engine` | `YES` | `NULL` |
| `sync_status` | `sync_status` | `NO` | `'PENDING'::sync_status` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |
| `narration` | `text` | `YES` | `NULL` |
| `tcs_section_code` | `text` | `YES` | `NULL` |
| `tcs_rate_percent` | `numeric` | `YES` | `NULL` |
| `tcs_amount` | `numeric` | `YES` | `NULL` |
| `tds_section_id` | `uuid` | `YES` | `NULL` |
| `tds_rate` | `numeric` | `YES` | `NULL` |
| `tds_amount` | `numeric` | `YES` | `0` |
| `tds_base_amount` | `numeric` | `YES` | `NULL` |
| `tds_status` | `text` | `YES` | `'PENDING'::text` |
| `original_amount` | `numeric` | `YES` | `NULL` |
| `fx_rate_id` | `uuid` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `stg_invoice_id`
**Foreign keys:**
- `account_id` -> `dim_ledger_accounts.account_id` (ON DELETE SET NULL)
- `end_customer_id` -> `dim_end_customers.end_customer_id` (ON DELETE RESTRICT)
- `fx_rate_id` -> `dim_fx_rates.rate_id` (ON DELETE NO ACTION)
- `raw_payload_id` -> `raw_api_payloads.payload_id` (ON DELETE SET NULL)
- `raw_webhook_event_id` -> `raw_webhook_events.event_id` (ON DELETE SET NULL)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
- `upload_id` -> `raw_invoice_uploads.upload_id` (ON DELETE SET NULL)
**Unique constraints:**
- `uq_stg_invoice_source`: (`tenant_id`, `source`, `source_invoice_id`)
**Indexes:**
- `idx_stg_inv_customer`: `CREATE INDEX idx_stg_inv_customer ON public.stg_invoices USING btree (end_customer_id)`
- `idx_stg_inv_due`: `CREATE INDEX idx_stg_inv_due ON public.stg_invoices USING btree (due_date)`
- `idx_stg_inv_status`: `CREATE INDEX idx_stg_inv_status ON public.stg_invoices USING btree (status)`
- `idx_stg_inv_sync`: `CREATE INDEX idx_stg_inv_sync ON public.stg_invoices USING btree (sync_status)`
- `idx_stg_inv_tenant`: `CREATE INDEX idx_stg_inv_tenant ON public.stg_invoices USING btree (tenant_id)`
- `stg_invoices_pkey`: `CREATE UNIQUE INDEX stg_invoices_pkey ON public.stg_invoices USING btree (stg_invoice_id)`
- `uq_stg_invoice_source`: `CREATE UNIQUE INDEX uq_stg_invoice_source ON public.stg_invoices USING btree (tenant_id, source, source_invoice_id)`

---

## Table: `stg_journal_entries`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `stg_journal_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `source_journal_id` | `text` | `NO` | `NULL` |
| `source_updated_at` | `timestamp with time zone` | `NO` | `NULL` |
| `raw_payload_id` | `uuid` | `YES` | `NULL` |
| `journal_number` | `text` | `YES` | `NULL` |
| `journal_date` | `date` | `NO` | `NULL` |
| `narration` | `text` | `YES` | `NULL` |
| `total_debit` | `numeric` | `NO` | `0` |
| `total_credit` | `numeric` | `NO` | `0` |
| `is_balanced` | `boolean` | `YES` | `NULL` |
| `sync_status` | `sync_status` | `NO` | `'PENDING'::sync_status` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `stg_journal_id`
**Foreign keys:**
- `raw_payload_id` -> `raw_api_payloads.payload_id` (ON DELETE SET NULL)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
**Unique constraints:**
- `uq_stg_journal_source`: (`tenant_id`, `source`, `source_journal_id`)
**Indexes:**
- `stg_journal_entries_pkey`: `CREATE UNIQUE INDEX stg_journal_entries_pkey ON public.stg_journal_entries USING btree (stg_journal_id)`
- `uq_stg_journal_source`: `CREATE UNIQUE INDEX uq_stg_journal_source ON public.stg_journal_entries USING btree (tenant_id, source, source_journal_id)`

---

## Table: `stg_journal_entry_lines`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `entry_line_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `stg_journal_id` | `uuid` | `NO` | `NULL` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `line_number` | `smallint` | `NO` | `NULL` |
| `account_id` | `uuid` | `NO` | `NULL` |
| `cost_centre_id` | `uuid` | `YES` | `NULL` |
| `debit_amount` | `numeric` | `NO` | `0` |
| `credit_amount` | `numeric` | `NO` | `0` |
| `narration` | `text` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `entry_line_id`
**Foreign keys:**
- `account_id` -> `dim_ledger_accounts.account_id` (ON DELETE RESTRICT)
- `cost_centre_id` -> `dim_cost_centres.cost_centre_id` (ON DELETE SET NULL)
- `stg_journal_id` -> `stg_journal_entries.stg_journal_id` (ON DELETE CASCADE)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
**Unique constraints:** none
**Indexes:**
- `stg_journal_entry_lines_pkey`: `CREATE UNIQUE INDEX stg_journal_entry_lines_pkey ON public.stg_journal_entry_lines USING btree (entry_line_id)`

---

## Table: `stg_payments`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `stg_payment_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `source` | `data_source` | `NO` | `NULL` |
| `source_payment_id` | `text` | `NO` | `NULL` |
| `source_updated_at` | `timestamp with time zone` | `NO` | `NULL` |
| `stg_invoice_id` | `uuid` | `YES` | `NULL` |
| `end_customer_id` | `uuid` | `NO` | `NULL` |
| `bank_account_id` | `uuid` | `YES` | `NULL` |
| `payment_date` | `date` | `NO` | `NULL` |
| `amount` | `numeric` | `NO` | `NULL` |
| `currency_code` | `character` | `NO` | `'INR'::bpchar` |
| `payment_method` | `payment_method` | `YES` | `NULL` |
| `transaction_ref` | `text` | `YES` | `NULL` |
| `gateway` | `text` | `YES` | `NULL` |
| `status` | `payment_status` | `NO` | `'PENDING'::payment_status` |
| `is_reconciled` | `boolean` | `NO` | `false` |
| `sync_status` | `sync_status` | `NO` | `'PENDING'::sync_status` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `stg_payment_id`
**Foreign keys:**
- `bank_account_id` -> `dim_bank_accounts.bank_account_id` (ON DELETE SET NULL)
- `end_customer_id` -> `dim_end_customers.end_customer_id` (ON DELETE RESTRICT)
- `stg_invoice_id` -> `stg_invoices.stg_invoice_id` (ON DELETE SET NULL)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
**Unique constraints:**
- `uq_stg_payment_source`: (`tenant_id`, `source`, `source_payment_id`)
**Indexes:**
- `stg_payments_pkey`: `CREATE UNIQUE INDEX stg_payments_pkey ON public.stg_payments USING btree (stg_payment_id)`
- `uq_stg_payment_source`: `CREATE UNIQUE INDEX uq_stg_payment_source ON public.stg_payments USING btree (tenant_id, source, source_payment_id)`

---

## Table: `supplier_bank_accounts`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `supplier_id` | `uuid` | `NO` | `NULL` |
| `account_number` | `text` | `NO` | `NULL` |
| `ifsc` | `text` | `YES` | `NULL` |
| `bank_name` | `text` | `YES` | `NULL` |
| `is_primary` | `boolean` | `NO` | `false` |
| `verified` | `boolean` | `NO` | `false` |
| `name_at_bank` | `text` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:** none
**Indexes:**
- `idx_sba_supplier_primary`: `CREATE INDEX idx_sba_supplier_primary ON public.supplier_bank_accounts USING btree (supplier_id, is_primary)`
- `idx_sba_tenant_supplier`: `CREATE INDEX idx_sba_tenant_supplier ON public.supplier_bank_accounts USING btree (tenant_id, supplier_id)`
- `supplier_bank_accounts_pkey`: `CREATE UNIQUE INDEX supplier_bank_accounts_pkey ON public.supplier_bank_accounts USING btree (id)`

---

## Table: `supplier_contacts`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `supplier_id` | `uuid` | `NO` | `NULL` |
| `name` | `text` | `NO` | `NULL` |
| `role` | `text` | `YES` | `NULL` |
| `email` | `text` | `YES` | `NULL` |
| `phone` | `text` | `YES` | `NULL` |
| `is_primary` | `boolean` | `NO` | `false` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |
| `agent_enabled` | `boolean` | `NO` | `false` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:** none
**Indexes:**
- `idx_sc_tenant_supplier`: `CREATE INDEX idx_sc_tenant_supplier ON public.supplier_contacts USING btree (tenant_id, supplier_id)`
- `supplier_contacts_pkey`: `CREATE UNIQUE INDEX supplier_contacts_pkey ON public.supplier_contacts USING btree (id)`

---

## Table: `supplier_gstins`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `supplier_id` | `uuid` | `NO` | `NULL` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `gstin` | `text` | `NO` | `NULL` |
| `pan` | `text` | `YES` | `NULL` |
| `is_primary` | `boolean` | `NO` | `false` |
| `status` | `text` | `NO` | `'UNVERIFIED'::text` |
| `trade_name` | `text` | `YES` | `NULL` |
| `legal_name` | `text` | `YES` | `NULL` |
| `gstin_status` | `text` | `YES` | `NULL` |
| `error_message` | `text` | `YES` | `NULL` |
| `verified_at` | `timestamp with time zone` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
- `supplier_id` -> `dim_suppliers.supplier_id` (ON DELETE CASCADE)
**Unique constraints:**
- `supplier_gstins_supplier_id_gstin_key`: (`supplier_id`, `gstin`)
**Indexes:**
- `idx_supplier_gstins_supplier`: `CREATE INDEX idx_supplier_gstins_supplier ON public.supplier_gstins USING btree (supplier_id)`
- `supplier_gstins_pkey`: `CREATE UNIQUE INDEX supplier_gstins_pkey ON public.supplier_gstins USING btree (id)`
- `supplier_gstins_supplier_id_gstin_key`: `CREATE UNIQUE INDEX supplier_gstins_supplier_id_gstin_key ON public.supplier_gstins USING btree (supplier_id, gstin)`

---

## Table: `supplier_gstns`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `supplier_id` | `uuid` | `NO` | `NULL` |
| `gstn` | `character` | `NO` | `NULL` |
| `label` | `text` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:**
- `supplier_gstns_tenant_id_gstn_key`: (`tenant_id`, `gstn`)
**Indexes:**
- `idx_sgstns_gstn`: `CREATE INDEX idx_sgstns_gstn ON public.supplier_gstns USING btree (tenant_id, gstn)`
- `idx_sgstns_supplier`: `CREATE INDEX idx_sgstns_supplier ON public.supplier_gstns USING btree (supplier_id)`
- `supplier_gstns_pkey`: `CREATE UNIQUE INDEX supplier_gstns_pkey ON public.supplier_gstns USING btree (id)`
- `supplier_gstns_tenant_id_gstn_key`: `CREATE UNIQUE INDEX supplier_gstns_tenant_id_gstn_key ON public.supplier_gstns USING btree (tenant_id, gstn)`

---

## Table: `tenant_chain_config`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `bigint` | `NO` | `nextval('tenant_chain_config_id_seq'::regclass)` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `step_order` | `smallint` | `NO` | `NULL` |
| `label` | `character varying` | `NO` | `NULL` |
| `role` | `character varying` | `NO` | `NULL` |
| `locked` | `boolean` | `YES` | `false` |
| `updated_at` | `timestamp with time zone` | `YES` | `now()` |
| `department` | `character varying` | `YES` | `NULL` |
| `is_snapshot` | `boolean` | `YES` | `false` |
| `assignees` | `jsonb` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:** none
**Indexes:**
- `tenant_chain_config_pkey`: `CREATE UNIQUE INDEX tenant_chain_config_pkey ON public.tenant_chain_config USING btree (id)`
- `tenant_chain_config_unique_idx`: `CREATE UNIQUE INDEX tenant_chain_config_unique_idx ON public.tenant_chain_config USING btree (tenant_id, COALESCE(department, ''::character varying), step_order, is_snapshot)`

---

## Table: `user_email_connections`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `username` | `text` | `NO` | `NULL` |
| `provider` | `text` | `NO` | `'google'::text` |
| `email` | `text` | `NO` | `NULL` |
| `access_token` | `text` | `NO` | `NULL` |
| `refresh_token` | `text` | `YES` | `NULL` |
| `token_expires_at` | `timestamp with time zone` | `YES` | `NULL` |
| `scopes` | `text` | `YES` | `NULL` |
| `status` | `text` | `NO` | `'active'::text` |
| `connected_at` | `timestamp with time zone` | `NO` | `now()` |
| `updated_at` | `timestamp with time zone` | `NO` | `now()` |
| `agent_last_scan_at` | `timestamp with time zone` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:**
- `user_email_connections_tenant_id_username_provider_key`: (`tenant_id`, `username`, `provider`)
**Indexes:**
- `idx_uec_user`: `CREATE INDEX idx_uec_user ON public.user_email_connections USING btree (tenant_id, username)`
- `user_email_connections_pkey`: `CREATE UNIQUE INDEX user_email_connections_pkey ON public.user_email_connections USING btree (id)`
- `user_email_connections_tenant_id_username_provider_key`: `CREATE UNIQUE INDEX user_email_connections_tenant_id_username_provider_key ON public.user_email_connections USING btree (tenant_id, username, provider)`

---

## Table: `users`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `integer` | `NO` | `nextval('users_id_seq'::regclass)` |
| `username` | `character varying` | `NO` | `NULL` |
| `password_hash` | `text` | `NO` | `NULL` |
| `role` | `character varying` | `NO` | `NULL` |
| `name` | `character varying` | `YES` | `NULL` |
| `email` | `character varying` | `YES` | `NULL` |
| `company_id` | `uuid` | `NO` | `NULL` |
| `department` | `character varying` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:**
- `users_username_key`: (`username`)
**Indexes:**
- `users_pkey`: `CREATE UNIQUE INDEX users_pkey ON public.users USING btree (id)`
- `users_username_key`: `CREATE UNIQUE INDEX users_username_key ON public.users USING btree (username)`

---

## Table: `vendor_documents`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `supplier_id` | `uuid` | `NO` | `NULL` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `file_name` | `character varying` | `NO` | `NULL` |
| `file_path` | `text` | `NO` | `NULL` |
| `file_size` | `bigint` | `YES` | `NULL` |
| `uploaded_by` | `character varying` | `YES` | `NULL` |
| `uploaded_at` | `timestamp with time zone` | `YES` | `now()` |
| `brand_id` | `uuid` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
none
**Unique constraints:** none
**Indexes:**
- `vendor_documents_pkey`: `CREATE UNIQUE INDEX vendor_documents_pkey ON public.vendor_documents USING btree (id)`

---

## Table: `vendor_outreach_log`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `supplier_id` | `uuid` | `NO` | `NULL` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `requested_by` | `text` | `YES` | `NULL` |
| `requested_at` | `timestamp with time zone` | `NO` | `now()` |
| `email_to` | `text` | `YES` | `NULL` |
| `subject` | `text` | `YES` | `NULL` |
| `body` | `text` | `YES` | `NULL` |
| `status` | `text` | `NO` | `'sent'::text` |
| `requested_fields` | `ARRAY` | `YES` | `NULL` |
| `sent_at` | `timestamp with time zone` | `YES` | `NULL` |
| `sent_by` | `text` | `YES` | `NULL` |
| `response_received_at` | `timestamp with time zone` | `YES` | `NULL` |
| `thread_id` | `text` | `YES` | `NULL` |
| `message_id` | `text` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `id`
**Foreign keys:**
- `supplier_id` -> `dim_suppliers.supplier_id` (ON DELETE CASCADE)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
**Unique constraints:** none
**Indexes:**
- `idx_outreach_pending_sent_at`: `CREATE INDEX idx_outreach_pending_sent_at ON public.vendor_outreach_log USING btree (tenant_id, status, sent_at) WHERE (status = 'PENDING'::text)`
- `idx_vol_supplier`: `CREATE INDEX idx_vol_supplier ON public.vendor_outreach_log USING btree (supplier_id)`
- `vendor_outreach_log_pkey`: `CREATE UNIQUE INDEX vendor_outreach_log_pkey ON public.vendor_outreach_log USING btree (id)`

---

## Table: `vendor_tds_config_log`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `log_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `override_id` | `uuid` | `NO` | `NULL` |
| `changed_by` | `uuid` | `YES` | `NULL` |
| `changed_at` | `timestamp with time zone` | `NO` | `now()` |
| `field_changed` | `text` | `NO` | `NULL` |
| `old_value` | `text` | `YES` | `NULL` |
| `new_value` | `text` | `YES` | `NULL` |
| `reason` | `text` | `YES` | `NULL` |

### Keys and Constraints
**Primary keys:** `log_id`
**Foreign keys:**
- `override_id` -> `dim_vendor_tds_overrides.override_id` (ON DELETE NO ACTION)
**Unique constraints:** none
**Indexes:**
- `vendor_tds_config_log_pkey`: `CREATE UNIQUE INDEX vendor_tds_config_log_pkey ON public.vendor_tds_config_log USING btree (log_id)`

---

## Table: `vendor_tds_fy_state`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `state_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `supplier_id` | `uuid` | `NO` | `NULL` |
| `tds_section_id` | `uuid` | `NO` | `NULL` |
| `fy` | `text` | `NO` | `NULL` |
| `cumulative_billed` | `numeric` | `YES` | `0` |
| `cumulative_tds_deducted` | `numeric` | `YES` | `0` |
| `threshold_crossed` | `boolean` | `YES` | `false` |
| `threshold_crossed_on_bill_id` | `uuid` | `YES` | `NULL` |
| `catchup_pending` | `numeric` | `YES` | `0` |
| `catchup_recovered` | `numeric` | `YES` | `0` |
| `catchup_decision` | `text` | `YES` | `NULL` |
| `catchup_decided_by` | `uuid` | `YES` | `NULL` |
| `catchup_decided_on` | `timestamp with time zone` | `YES` | `NULL` |
| `replay_in_progress` | `boolean` | `YES` | `false` |
| `updated_at` | `timestamp with time zone` | `YES` | `now()` |

### Keys and Constraints
**Primary keys:** `state_id`
**Foreign keys:**
- `supplier_id` -> `dim_suppliers.supplier_id` (ON DELETE NO ACTION)
- `tds_section_id` -> `dim_tds_sections.tds_section_id` (ON DELETE NO ACTION)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE NO ACTION)
**Unique constraints:**
- `vendor_tds_fy_state_tenant_id_supplier_id_tds_section_id_fy_key`: (`tenant_id`, `supplier_id`, `tds_section_id`, `fy`)
**Indexes:**
- `vendor_tds_fy_state_pkey`: `CREATE UNIQUE INDEX vendor_tds_fy_state_pkey ON public.vendor_tds_fy_state USING btree (state_id)`
- `vendor_tds_fy_state_tenant_id_supplier_id_tds_section_id_fy_key`: `CREATE UNIQUE INDEX vendor_tds_fy_state_tenant_id_supplier_id_tds_section_id_fy_key ON public.vendor_tds_fy_state USING btree (tenant_id, supplier_id, tds_section_id, fy)`

---

## Table: `wf_audit_log`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `log_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `table_name` | `text` | `NO` | `NULL` |
| `entity_id` | `uuid` | `NO` | `NULL` |
| `action` | `audit_action` | `NO` | `NULL` |
| `user_id` | `uuid` | `YES` | `NULL` |
| `correlation_id` | `uuid` | `YES` | `NULL` |
| `old_values_jsonb` | `jsonb` | `YES` | `NULL` |
| `new_values_jsonb` | `jsonb` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `log_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
**Unique constraints:** none
**Indexes:**
- `idx_audit_tenant_time`: `CREATE INDEX idx_audit_tenant_time ON public.wf_audit_log USING btree (tenant_id, created_at)`
- `idx_wf_audit_entity`: `CREATE INDEX idx_wf_audit_entity ON public.wf_audit_log USING btree (tenant_id, table_name, entity_id)`
- `wf_audit_log_pkey`: `CREATE UNIQUE INDEX wf_audit_log_pkey ON public.wf_audit_log USING btree (log_id)`

---

## Table: `wf_escalations`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `escalation_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `task_id` | `uuid` | `NO` | `NULL` |
| `escalated_to` | `uuid` | `YES` | `NULL` |
| `reason` | `text` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `escalation_id`
**Foreign keys:**
- `task_id` -> `wf_tasks.task_id` (ON DELETE CASCADE)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
**Unique constraints:** none
**Indexes:**
- `wf_escalations_pkey`: `CREATE UNIQUE INDEX wf_escalations_pkey ON public.wf_escalations USING btree (escalation_id)`

---

## Table: `wf_nudges`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `nudge_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `task_id` | `uuid` | `YES` | `NULL` |
| `end_customer_id` | `uuid` | `YES` | `NULL` |
| `stg_invoice_id` | `uuid` | `YES` | `NULL` |
| `contact_id` | `uuid` | `YES` | `NULL` |
| `channel` | `nudge_channel` | `NO` | `NULL` |
| `template_id` | `text` | `YES` | `NULL` |
| `content_jsonb` | `jsonb` | `NO` | `'{}'::jsonb` |
| `status` | `nudge_status` | `NO` | `'PENDING'::nudge_status` |
| `sent_at` | `timestamp with time zone` | `YES` | `NULL` |
| `delivered_at` | `timestamp with time zone` | `YES` | `NULL` |
| `error_message` | `text` | `YES` | `NULL` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `nudge_id`
**Foreign keys:**
- `contact_id` -> `dim_end_customer_contacts.contact_id` (ON DELETE SET NULL)
- `end_customer_id` -> `dim_end_customers.end_customer_id` (ON DELETE SET NULL)
- `stg_invoice_id` -> `stg_invoices.stg_invoice_id` (ON DELETE SET NULL)
- `task_id` -> `wf_tasks.task_id` (ON DELETE SET NULL)
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE RESTRICT)
**Unique constraints:** none
**Indexes:**
- `wf_nudges_pkey`: `CREATE UNIQUE INDEX wf_nudges_pkey ON public.wf_nudges USING btree (nudge_id)`

---

## Table: `wf_tasks`

### Columns
| Column | Type | Nullable | Default |
|---|---|---|---|
| `task_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `task_type` | `wf_task_type` | `NO` | `NULL` |
| `entity_type` | `wf_entity_type` | `NO` | `NULL` |
| `entity_id` | `uuid` | `NO` | `NULL` |
| `assignee_user_id` | `uuid` | `YES` | `NULL` |
| `status` | `wf_task_status` | `NO` | `'PENDING'::wf_task_status` |
| `priority` | `smallint` | `NO` | `3` |
| `due_at` | `timestamp with time zone` | `YES` | `NULL` |
| `completed_at` | `timestamp with time zone` | `YES` | `NULL` |
| `metadata_jsonb` | `jsonb` | `NO` | `'{}'::jsonb` |
| `created_at` | `timestamp with time zone` | `NO` | `now()` |

### Keys and Constraints
**Primary keys:** `task_id`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
**Unique constraints:** none
**Indexes:**
- `idx_wf_tasks_assignee`: `CREATE INDEX idx_wf_tasks_assignee ON public.wf_tasks USING btree (assignee_user_id)`
- `idx_wf_tasks_entity`: `CREATE INDEX idx_wf_tasks_entity ON public.wf_tasks USING btree (entity_type, entity_id)`
- `idx_wf_tasks_status`: `CREATE INDEX idx_wf_tasks_status ON public.wf_tasks USING btree (tenant_id, status, due_at)`
- `wf_tasks_pkey`: `CREATE UNIQUE INDEX wf_tasks_pkey ON public.wf_tasks USING btree (task_id)`

---

# Licensing & Platform Admin (added 2026-08-15)

Defined in [`backend_ocr/app/licensing_schema.py`](../backend_ocr/app/licensing_schema.py),
applied by the app's startup hook and by `scripts/licensing_migrate.py --target both`.
See [LICENSING_AND_ADMIN.md](LICENSING_AND_ADMIN.md) for the model and the runbook.

> Note: the tables above were pulled from the live RDS schema and are **not**
> exhaustive — several tables the app creates at startup (`cost_centers`,
> `agent_settings`, `auth_refresh_tokens`, `supplier_contacts`,
> `tenant_priority_labels`, `vendor_doc_edits`) are absent. Re-pull before relying
> on this document as a complete inventory.

## Table: `service_catalog`

Mirror of the `SERVICES` registry in `backend_ocr/app/services.py`, upserted on every
boot so `tenant_service.service_key` has a foreign-key target. Rows are never
deleted; a service dropped from the registry gets `retired_at` stamped.

| Column | Type | Nullable | Default |
|---|---|---|---|
| `service_key` | `text` | `NO` | `NULL` |
| `display_name` | `text` | `NO` | `NULL` |
| `description` | `text` | `NO` | `''` |
| `category` | `text` | `NO` | `NULL` |
| `is_core` | `bool` | `NO` | `false` |
| `default_enabled` | `bool` | `NO` | `false` |
| `metered_unit` | `text` | `YES` | `NULL` |
| `sort_order` | `int4` | `NO` | `0` |
| `retired_at` | `timestamptz` | `YES` | `NULL` |
| `created_at` | `timestamptz` | `NO` | `now()` |
| `updated_at` | `timestamptz` | `NO` | `now()` |

**Primary keys:** `service_key`

---

## Table: `tenant_service`

**The source of truth for what a tenant may use.** A missing row means "inherit
`service_catalog.default_enabled`", so a newly added core service lights up for
every existing tenant without a backfill write.

| Column | Type | Nullable | Default |
|---|---|---|---|
| `tenant_id` | `uuid` | `NO` | `NULL` |
| `service_key` | `text` | `NO` | `NULL` |
| `enabled` | `bool` | `NO` | `false` |
| `limits` | `jsonb` | `NO` | `'{}'::jsonb` |
| `config` | `jsonb` | `NO` | `'{}'::jsonb` |
| `enabled_at` | `timestamptz` | `YES` | `NULL` |
| `disabled_at` | `timestamptz` | `YES` | `NULL` |
| `updated_by` | `uuid` | `YES` | `NULL` |
| `notes` | `text` | `YES` | `NULL` |
| `created_at` | `timestamptz` | `NO` | `now()` |
| `updated_at` | `timestamptz` | `NO` | `now()` |

**Primary keys:** `tenant_id`, `service_key`
**Foreign keys:**
- `tenant_id` -> `dim_tenants.tenant_id` (ON DELETE CASCADE)
- `service_key` -> `service_catalog.service_key`

`updated_by` deliberately has **no** FK to `platform_users`, so deleting an ops
account does not cascade away the licensing audit trail.

**Indexes:** `idx_tenant_service_enabled` (partial, `WHERE enabled`),
`idx_tenant_service_key` (partial, `WHERE enabled`) — answers "which tenants have FX?"

---

## Table: `service_package` / `service_package_item`

Onboarding prefills. Applying a package **copies** rows into `tenant_service`;
nothing resolves through a package at request time, so editing one never changes a
live tenant.

`service_package`: `package_id` uuid PK, `key` text UNIQUE, `name`, `description`,
`is_active` bool, `sort_order` int, `created_at`.
`service_package_item`: PK (`package_id`, `service_key`), `enabled` bool, `limits` jsonb.

---

## Table: `platform_users`

Ops accounts. Entirely separate from `users` — a different table, a different
signing key, and no tenant.

| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` | `uuid` | `NO` | `gen_random_uuid()` |
| `email` | `text` | `NO` | `NULL` |
| `password_hash` | `text` | `NO` | `NULL` |
| `name` | `text` | `NO` | `NULL` |
| `role` | `text` | `NO` | `'support'` |
| `is_active` | `bool` | `NO` | `true` |
| `last_login_at` | `timestamptz` | `YES` | `NULL` |
| `failed_logins` | `int4` | `NO` | `0` |
| `locked_until` | `timestamptz` | `YES` | `NULL` |
| `created_by` | `uuid` | `YES` | `NULL` |
| `created_at` / `updated_at` | `timestamptz` | `NO` | `now()` |

**Constraints:** `role IN ('owner','admin','support','readonly')`;
`uq_platform_users_email` UNIQUE on `lower(email)`

---

## Table: `platform_refresh_tokens`

Mirrors `auth_refresh_tokens` so rotation and reuse-detection behave identically.
`token_hash` (sha256) UNIQUE, `platform_user_id` FK CASCADE, `issued_at`,
`expires_at`, `revoked`, `revoked_at`, `replaced_by`.

---

## Table: `platform_impersonation`

One row per support session. `session_id` uuid PK (used as the token `jti`),
`platform_user_id` FK, `tenant_id` FK, `assumed_role`, `reason` (required, ≥10
chars), `read_only` bool, `issued_at`, `expires_at`, `revoked_at`.

---

## Table: `tenant_usage`

Monthly rollup. PK (`tenant_id`, `service_key`, `metric`, `period_month`), plus
`used` numeric and `last_event_at`. `period_month` is always the first of the month.

---

## Table: `admin_audit_log`

Append-only record of every ops action. `id` bigserial PK,
`actor_platform_user_id`, `actor_email`, `action` (e.g. `license.enable`,
`tenant.create`, `impersonation.start`), `target_type`, `target_id`, `tenant_id`,
`before`/`after` jsonb, `impersonation_id`, `ip`, `user_agent`, `created_at`.
Written in the same transaction as the change, so a rolled-back change can never
leave a log entry claiming it happened.

**Indexes:** by time, tenant, actor and action — all `DESC` on `created_at`.

---

## Table: `schema_migrations`

`label` text PK, `applied_at`. Guards the one-shot licensing backfill so it can
only ever apply once per database.

---

## Additions to `dim_tenants`

| Column | Type | Nullable | Default | Why |
|---|---|---|---|---|
| `pipeline_enabled` | `bool` | `NO` | `true` | DB-driven successor to the `PIPELINE_TENANT_ID` env list |
| `onboarded_at` | `timestamptz` | `YES` | `NULL` | Set by the onboarding transaction |
| `package_key` | `text` | `YES` | `NULL` | Provenance only — nothing resolves through it |
| `legal_name` | `text` | `YES` | `NULL` | Default brand name; replaces the hardcoded customer name |
| `primary_contact_email` | `text` | `YES` | `NULL` | |

`sap_matching_enabled` is retained for now: it is the source for the licensing
backfill. Drop it only after `sap_matching` has resolved through `tenant_service`
for a full release.

---
