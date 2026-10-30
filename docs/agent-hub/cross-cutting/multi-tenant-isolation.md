# Multi-Tenant Isolation

**Scope:** Cross-cutting — applies to every table, not any one stage
**Role:** One customer's data can never collide with another's

## What it does

Every table carries `tenant_id`, and most unique keys are tenant-scoped, so one customer's data can never collide with another's. Org users, access levels, and brand/cost-centre dimensions enforce role-based, project-scoped visibility on top of tenant isolation itself.

## Capabilities

- `tenant_id` on every row, with tenant-scoped uniqueness constraints (e.g. a vendor's invoice number is unique *within* a tenant, not globally)
- RBAC via `org_user_access_levels` — role-based access control layered on top of tenant scoping
- Brand / cost-centre scoping — finer-grained visibility within a single tenant for multi-entity organizations

## Data model

### `dim_tenants`
`plan_tier`, `config`. The tenant record itself.

### `org_users`
Roles. The users belonging to a tenant and what role they hold.

### `org_user_access_levels`
Scoped permissions. Fine-grained, role-based access control within a tenant.

## Which stages this applies to

Every stage in [`ARCHITECTURE.md`](../ARCHITECTURE.md). Every table listed across every agent doc in this folder carries `tenant_id`.

## Why it matters

This project runs a strict rule — enforced in practice, not just in the schema — that certain tenants are production and certain tenants are dev/test, and test data must never cross into a production tenant. Tenant scoping at the schema level (`tenant_id` on every row, tenant-scoped uniqueness) is what makes that boundary technically real rather than a convention someone has to remember. A real incident on this project (synthetic demo data landing on a production tenant) happened specifically because a script bypassed the application layer and wrote directly to the database with the wrong `tenant_id` — the schema-level isolation didn't fail; the mistake was in which tenant a human pointed the script at. See `docs/CHANGE_RELEASE_NOVA_BILLING_DEV_TENANT.md` for the tenant-cleanup precedent this rule comes from.
