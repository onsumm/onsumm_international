-- Tenant-configurable Nova API key storage.
-- No migrations framework exists in this repo yet — apply manually:
--   psql "$DB_URL" -f backend_ocr/app/sql/nova_credentials.sql
--
-- Deliberately its own table rather than a new `data_source` enum value on
-- int_connections: unlike the Tally/SAP agent-pairing flow (a generated
-- pairing token the customer's agent exchanges for an api_key), this is a
-- literal secret the tenant already holds and pastes in directly — a
-- different shape of data with a different retrieval need (must be
-- decryptable to call Nova's API on the tenant's behalf, not just hashed
-- for inbound verification like agent_api_key_hash).

CREATE TABLE IF NOT EXISTS int_nova_credentials (
    tenant_id          uuid PRIMARY KEY REFERENCES dim_tenants (tenant_id) ON DELETE CASCADE,
    api_key_encrypted  text NOT NULL,   -- Fernet-encrypted, see backend_ocr/app/nova_credentials_api.py
    base_url           text,            -- optional override of NOVA_BILLING_BASE_URL for this tenant
    is_active          boolean NOT NULL DEFAULT true,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
