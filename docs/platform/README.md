# Onsumm Platform Documentation

The [repository README](../../README.md) tells the product story end to end, business and technical, with every diagram from these documents in place.

| # | Document | Scope |
|---|---|---|
| 01 | [High-Level Design](01_HLD.md) | Context, containers, modules, rails, events, data, integrations, security, deployment, NFRs, decisions, risks |
| 02 | [Low-Level Design](02_LLD.md) | Code layout, request pipeline, class diagrams, internal sequences, algorithms, concurrency, configuration, testing |
| 03 | [User Flows](03_USER_FLOWS.md) | Routes, roles, 25 end-to-end flows, error catalogue |
| 04 | [Data Model](04_DATA_MODEL.md) | Schema sources, ER diagrams, state machines, triggers, tenancy |
| 05 | [API Reference](05_API_REFERENCE.md) | Routers, routes, access rules, error codes, examples |
| 06 | [Deployment & Operations](06_DEPLOYMENT_OPERATIONS.md) | Environments, CI, releases, jobs, monitoring, runbooks, DR |
| 07 | [Security](07_SECURITY.md) | Trust boundaries, controls, threat model, remediation plan |
| 08 | [Integrations](08_INTEGRATIONS.md) | Partner integrations and failure handling |
| 09 | [Glossary & Status](09_GLOSSARY_AND_STATUS.md) | Terms, feature status, decision log, open questions |

The previous endpoint-by-endpoint reference is in [`docs/TECHNICAL_REFERENCE.md`](../TECHNICAL_REFERENCE.md).

## Updating the root README

The root README is built from the story in [`_build/README.story.md`](_build/README.story.md); each `{{Dnn-mm}}` placeholder pulls the mm-th diagram from document nn. After editing the story or a document, run from the repo root:

```bash
python scripts/build_docs_readme.py
```
