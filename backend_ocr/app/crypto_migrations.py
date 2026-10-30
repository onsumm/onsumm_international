"""Schema migrations for the crypto rail (docs/crypto-rail/04_LLD.md §7).

Kept out of migrations.py so the crypto rail's DDL reads as one unit and can be
reviewed on its own. Same conventions as migrations.py: every step is
idempotent (CREATE ... IF NOT EXISTS / ADD COLUMN IF NOT EXISTS), opens its own
connection, and prints a failure instead of raising, so one bad step never stops
the steps after it. scripts/run_api_migrations.py runs CRYPTO_MIGRATION_STEPS
after the core MIGRATION_STEPS.

The audit-chain columns on wf_audit_log and the anchor tables are owned by the
audit migrations, not this module.

Deviations from the LLD DDL, both because of the live schema:
  - `users.id` is an integer, so `supplier_wallets.approved_by` and
    `crypto_payout_signatures.user_id` hold the username (text), not a uuid.
  - `treasury_wallets.custody` also allows 'EOA': a tenant's TAX wallet may be a
    plain address rather than a Safe.
"""
from __future__ import annotations

import traceback

from .config import get_conn, put_conn


def _run(label: str, sql: str) -> None:
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
        print(f"[migration] {label} ready")
    except Exception:
        if conn is not None:
            conn.rollback()
        print(f"[migration] ERROR in {label}:")
        traceback.print_exc()
    finally:
        if conn is not None:
            put_conn(conn)


def _migrate_crypto_existing_tables():
    # Defaults keep every existing tenant on today's behaviour: India-resident,
    # INR, crypto off. Only the bank rail exists for them.
    _run("crypto columns on dim_tenants / dim_suppliers / fact_disbursements", """
        ALTER TABLE dim_tenants
          ADD COLUMN IF NOT EXISTS country_code char(2) DEFAULT 'IN',
          ADD COLUMN IF NOT EXISTS base_currency char(3) DEFAULT 'INR',
          ADD COLUMN IF NOT EXISTS jurisdiction_profile text DEFAULT 'india',
          ADD COLUMN IF NOT EXISTS is_resident_india boolean DEFAULT true,
          ADD COLUMN IF NOT EXISTS kyb_status text DEFAULT 'NOT_STARTED',
          ADD COLUMN IF NOT EXISTS registration_number text,
          ADD COLUMN IF NOT EXISTS tax_residency char(2);

        ALTER TABLE dim_suppliers
          ADD COLUMN IF NOT EXISTS country_code char(2),
          ADD COLUMN IF NOT EXISTS tax_residency char(2),
          ADD COLUMN IF NOT EXISTS preferred_rail text DEFAULT 'BANK',
          ADD COLUMN IF NOT EXISTS tax_docs jsonb DEFAULT '{}'::jsonb;

        -- fact_disbursements is created by routers/disbursement.py at import;
        -- guard so this step also works on a database that has never served it.
        DO $$
        BEGIN
          IF to_regclass('public.fact_disbursements') IS NOT NULL THEN
            ALTER TABLE fact_disbursements
              ADD COLUMN IF NOT EXISTS rail text DEFAULT 'IMPS',
              ADD COLUMN IF NOT EXISTS currency_code char(3) DEFAULT 'INR',
              ADD COLUMN IF NOT EXISTS withholding_amount numeric DEFAULT 0,
              ADD COLUMN IF NOT EXISTS net_amount numeric;
            CREATE INDEX IF NOT EXISTS idx_fdis_rail_status ON fact_disbursements (rail, status);
          END IF;
        END $$;
    """)


def _migrate_crypto_tenant_tables():
    _run("tenant_ubos / tenant_rail_policy / treasury_wallets", """
        CREATE TABLE IF NOT EXISTS tenant_ubos (
          id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id        uuid NOT NULL,
          name             text NOT NULL,
          country_code     char(2),
          ownership_pct    numeric(5,2),
          screening_status text DEFAULT 'PENDING',
          screened_at      timestamptz,
          created_at       timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_tenant_ubos_tenant ON tenant_ubos (tenant_id);

        CREATE TABLE IF NOT EXISTS tenant_rail_policy (
          tenant_id              uuid PRIMARY KEY,
          crypto_enabled         boolean NOT NULL DEFAULT false,
          allowed_chains         text[]  NOT NULL DEFAULT ARRAY['base-sepolia'],
          allowed_tokens         text[]  NOT NULL DEFAULT ARRAY['USDC'],
          per_tx_limit_usd       numeric,
          daily_limit_usd        numeric,
          dual_control_above_usd numeric DEFAULT 10000,
          cooling_minutes        int     NOT NULL DEFAULT 1440,
          require_test_transfer  boolean NOT NULL DEFAULT true,
          updated_at             timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS treasury_wallets (
          id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id      uuid NOT NULL,
          role           text NOT NULL CHECK (role IN ('TREASURY', 'TAX')),
          chain          text NOT NULL,
          address        text NOT NULL,
          custody        text NOT NULL CHECK (custody IN ('SAFE', 'PARTNER', 'EOA')),
          safe_threshold int,
          safe_owners    text[],
          status         text NOT NULL DEFAULT 'ACTIVE',
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, chain, address)
        );
        CREATE INDEX IF NOT EXISTS ix_treasury_wallets_address ON treasury_wallets (lower(address));
    """)


def _migrate_crypto_wallet_tables():
    _run("supplier_wallets / wallet_screenings / vendor_portal_nonces / vendor_contract_rates", """
        CREATE TABLE IF NOT EXISTS supplier_wallets (
          id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id        uuid NOT NULL,
          supplier_id      uuid NOT NULL,
          chain            text NOT NULL,
          asset            text NOT NULL DEFAULT 'USDC',
          address          text NOT NULL,
          is_primary       boolean NOT NULL DEFAULT true,
          siwe_message     text,
          ownership_sig    text,
          verified_at      timestamptz,
          cooling_until    timestamptz,
          screening_status text DEFAULT 'PENDING',
          status           text NOT NULL DEFAULT 'PENDING_PROOF'
            CHECK (status IN ('PENDING_PROOF', 'COOLING', 'ACTIVE', 'FROZEN', 'REVOKED')),
          approved_by      text,
          replaced_by      uuid,
          frozen_reason    text,
          created_at       timestamptz NOT NULL DEFAULT now(),
          updated_at       timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_supplier_wallets_supplier ON supplier_wallets (tenant_id, supplier_id);
        CREATE INDEX IF NOT EXISTS ix_supplier_wallets_address ON supplier_wallets (lower(address));

        CREATE TABLE IF NOT EXISTS wallet_screenings (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          chain       text NOT NULL,
          address     text NOT NULL,
          provider    text NOT NULL,
          result      text NOT NULL CHECK (result IN ('CLEAR', 'HIT', 'ERROR')),
          risk_score  numeric,
          categories  jsonb DEFAULT '[]'::jsonb,
          screened_at timestamptz NOT NULL DEFAULT now(),
          raw         jsonb
        );
        CREATE INDEX IF NOT EXISTS ix_wallet_screenings_address ON wallet_screenings (lower(address), screened_at DESC);

        -- One row per SIWE challenge: a signed message is accepted once, only
        -- for the tenant, vendor and address it was issued to, before expiry.
        CREATE TABLE IF NOT EXISTS vendor_portal_nonces (
          nonce       text PRIMARY KEY,
          tenant_id   uuid NOT NULL,
          supplier_id uuid NOT NULL,
          address     text NOT NULL,
          message     text NOT NULL,
          expires_at  timestamptz NOT NULL,
          used_at     timestamptz,
          created_at  timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS vendor_contract_rates (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id     uuid NOT NULL,
          supplier_id   uuid NOT NULL,
          item_key      text NOT NULL,
          unit_price    numeric NOT NULL,
          currency_code char(3) NOT NULL,
          valid_from    date,
          valid_to      date,
          created_at    timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_vendor_contract_rates_supplier ON vendor_contract_rates (tenant_id, supplier_id);
    """)


def _migrate_crypto_payout_tables():
    _run("fx_snapshots / crypto_disbursement_details / crypto_payout_signatures / withholding tables", """
        CREATE TABLE IF NOT EXISTS fx_snapshots (
          id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          token          text NOT NULL,
          token_usd      numeric NOT NULL,
          usd_functional numeric NOT NULL,
          usd_inr        numeric,
          bill_currency  char(3),
          usd_bill       numeric,
          source         text NOT NULL,
          captured_at    timestamptz NOT NULL DEFAULT now(),
          block_time     timestamptz
        );

        CREATE TABLE IF NOT EXISTS crypto_disbursement_details (
          disbursement_id    uuid PRIMARY KEY,
          tenant_id          uuid NOT NULL,
          chain              text NOT NULL,
          token_contract     text NOT NULL,
          treasury_wallet_id uuid NOT NULL REFERENCES treasury_wallets(id),
          supplier_wallet_id uuid NOT NULL REFERENCES supplier_wallets(id),
          tax_wallet_id      uuid REFERENCES treasury_wallets(id),
          gross_units        numeric(38,0) NOT NULL,
          amount_units       numeric(38,0) NOT NULL CHECK (amount_units > 0),
          withholding_units  numeric(38,0) NOT NULL DEFAULT 0,
          fx_snapshot_id     uuid REFERENCES fx_snapshots(id),
          safe_tx_hash       text,
          safe_nonce         bigint,
          idempotency_key    text NOT NULL UNIQUE,
          sub_status         text NOT NULL DEFAULT 'AWAITING_RELEASE',
          tx_hash            text,
          block_number       bigint,
          confirmations      int DEFAULT 0,
          gas_fee_native     numeric,
          submitted_at       timestamptz,
          confirmed_at       timestamptz,
          failure_reason     text,
          created_at         timestamptz NOT NULL DEFAULT now(),
          updated_at         timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_cdd_tx_hash ON crypto_disbursement_details (lower(tx_hash));
        CREATE INDEX IF NOT EXISTS ix_cdd_tenant_sub_status ON crypto_disbursement_details (tenant_id, sub_status);

        CREATE TABLE IF NOT EXISTS crypto_payout_signatures (
          disbursement_id uuid NOT NULL REFERENCES crypto_disbursement_details(disbursement_id),
          signer_address  text NOT NULL,
          user_id         text,
          signature       text NOT NULL,
          signed_at       timestamptz NOT NULL DEFAULT now(),
          UNIQUE (disbursement_id, signer_address)
        );

        CREATE TABLE IF NOT EXISTS withholding_remittances (
          id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id        uuid NOT NULL,
          period           text NOT NULL,
          authority        text NOT NULL,
          total_units      numeric(38,0) NOT NULL DEFAULT 0,
          fiat_amount      numeric,
          reference        text,
          tx_hash          text,
          certificate_path text,
          status           text NOT NULL DEFAULT 'DRAFT',
          created_at       timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS crypto_withholdings (
          id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id        uuid NOT NULL,
          disbursement_id  uuid NOT NULL,
          rule_pack        text NOT NULL,
          rule_code        text NOT NULL,
          rate             numeric NOT NULL,
          base_amount      numeric NOT NULL,
          base_currency    char(3) NOT NULL,
          amount_units     numeric(38,0) NOT NULL,
          requires_signoff boolean NOT NULL DEFAULT false,
          note             text,
          status           text NOT NULL DEFAULT 'CALCULATED',
          remittance_id    uuid REFERENCES withholding_remittances(id),
          created_at       timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_crypto_withholdings_disbursement ON crypto_withholdings (disbursement_id);
    """)


def _migrate_crypto_chain_tables():
    _run("chain_transfers / chain_watch_cursors / ledger_postings", """
        CREATE TABLE IF NOT EXISTS chain_transfers (
          id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          chain                   text NOT NULL,
          tx_hash                 text NOT NULL,
          log_index               int NOT NULL,
          block_number            bigint NOT NULL,
          block_hash              text,
          block_time              timestamptz,
          token_contract          text NOT NULL,
          from_address            text NOT NULL,
          to_address              text NOT NULL,
          amount_units            numeric(38,0) NOT NULL,
          tenant_id               uuid,
          direction               text NOT NULL CHECK (direction IN ('IN', 'OUT')),
          match_type              text DEFAULT 'UNKNOWN'
            CHECK (match_type IN ('PAYOUT', 'TAX', 'FUNDING', 'RECEIVABLE', 'UNKNOWN')),
          matched_disbursement_id uuid,
          matched_invoice_id      uuid,
          status                  text NOT NULL DEFAULT 'UNMATCHED'
            CHECK (status IN ('MATCHED', 'UNMATCHED', 'MISMATCH')),
          confirmations           int DEFAULT 0,
          removed                 boolean NOT NULL DEFAULT false,
          created_at              timestamptz NOT NULL DEFAULT now(),
          UNIQUE (chain, tx_hash, log_index)
        );
        CREATE INDEX IF NOT EXISTS ix_chain_transfers_tenant ON chain_transfers (tenant_id, block_number DESC);
        CREATE INDEX IF NOT EXISTS ix_chain_transfers_disbursement ON chain_transfers (matched_disbursement_id);

        CREATE TABLE IF NOT EXISTS chain_watch_cursors (
          chain           text PRIMARY KEY,
          last_block      bigint NOT NULL,
          last_block_hash text,
          updated_at      timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS ledger_postings (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id     uuid NOT NULL,
          source_type   text NOT NULL,
          source_id     uuid NOT NULL,
          target_system text NOT NULL,
          payload       jsonb NOT NULL,
          external_ref  text,
          status        text NOT NULL DEFAULT 'PENDING',
          error         text,
          attempts      int NOT NULL DEFAULT 0,
          created_at    timestamptz NOT NULL DEFAULT now(),
          updated_at    timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_ledger_postings_tenant ON ledger_postings (tenant_id, created_at DESC);
    """)


def _migrate_crypto_demo_settlement():
    # A payout settled in demo mode (CRYPTO_SETTLEMENT=demo) is validated against
    # the real Safe contract but never broadcast; it and the transfer rows it
    # records are flagged so no screen or reconciliation mistakes them for chain data.
    _run("demo settlement flags", """
        ALTER TABLE crypto_disbursement_details ADD COLUMN IF NOT EXISTS simulated boolean NOT NULL DEFAULT false;
        ALTER TABLE chain_transfers ADD COLUMN IF NOT EXISTS simulated boolean NOT NULL DEFAULT false;
    """)


CRYPTO_MIGRATION_STEPS = (
    _migrate_crypto_existing_tables,
    _migrate_crypto_tenant_tables,
    _migrate_crypto_wallet_tables,
    _migrate_crypto_payout_tables,
    _migrate_crypto_chain_tables,
    _migrate_crypto_demo_settlement,
)
