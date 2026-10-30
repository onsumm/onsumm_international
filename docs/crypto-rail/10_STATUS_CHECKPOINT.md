# Crypto Rail — Status Checkpoint

As of 30 Oct 2026 · Hackathon demo: Sat 31 Oct, morning

Legend: ✅ done and checked · 🟡 built, not yet checked in a real browser or on-chain · ⏳ waiting on you · ❌ not built

## 1. Blocking the first real payment (do these next)

| # | What | Who | Status |
| --- | --- | --- | --- |
| 1 | Test ETH to the relayer `0x4638C2E3DFdBAa2E817E2778d5d0d6fFa19437D8` (it pays the network fee). Balance now: **0 ETH**. Coinbase's faucet is not available in India; try faucets.chain.link/base-sepolia, Alchemy, or a teammate sending 0.001 ETH | You | ⏳ |
| 2 | First test payment: bill NOVA-1043, amount **10 USDC**, signed by Ben then Chen | You | ⏳ (needs 1) |

## 2. On-chain setup

| Item | Status | Detail |
| --- | --- | --- |
| Lumen's Safe created on Base Sepolia | ✅ | `0xdE9584b64B2Ba9E8d907Ab2c793Ee4775aC33abD` |
| Safe owners and threshold | 🟡 | **Ben and Chen, 2-of-2**. Plan was Asha + Ben + Chen, 2-of-3. Works for the test; Asha cannot sign |
| Safe funded with USDC | ✅ | 20 USDC (Circle faucet). Demo bill needs 3,600 |
| Safe connected in Onsumm | ✅ | Owners and threshold read from the chain |
| Chain watcher recorded the funding | ✅ | 20 USDC as `FUNDING`, matched |
| Nova's wallet registered, screened, approved | ✅ | `0x4D5F…3881`, status Active, vendor badge Verified |
| Relayer wallet generated | ✅ | key only in `.env` (file mode 600) |
| Relayer funded with ETH | ⏳ | 0 ETH |
| Audit anchor sent on-chain | ❌ | `ANCHOR_SIGNER_KEY` not set; anchors are built locally (status `BUILT`) with no transaction hash |
| `OnsummAnchor.sol` compiled and deployed | ❌ | Foundry not installed; calldata mode needs no contract |

## 3. Application: built and checked

| Area | Status |
| --- | --- |
| Database: 17 new tables, changed tables, migrations run | ✅ on `onsumm-new-db` |
| USDC payout lifecycle (prepare → OTP → Safe proposal → signatures → relayer → confirm → bill paid) | ✅ in tests with a fake chain; ❌ not yet run on the real chain |
| Vendor wallet proof (SIWE), screening, cooling, approval, wallet-change freeze | ✅ run live with real MetaMask |
| Policy engine (all refusal codes), India-resident block | ✅ tests + API |
| Withholding rule packs (SG-DEMO, IN-195, IN-VDA) | ✅ tests; SG-DEMO shown live at 0% |
| On-chain watcher and matcher | ✅ real chain data (funding seen) |
| Audit hash chain (Postgres trigger, 16,931 rows backfilled) and verification | ✅ |
| Merkle proofs and share links | ✅ checked; your `onsumm-proof-5039.json` verified |
| Country rules: GST, PAN, Indian TDS, IFSC, IMPS, MSME on/off per company | ✅ backend and API checked for Lumen vs the Indian demo company |
| Amounts and dates in each company's own currency | ✅ code + tests; Lumen shows `US$` |
| Bills list hang (idle-in-transaction + `ALTER TABLE` lock) | ✅ fixed in the original code too |
| Demo seed script and demo logins | ✅ `scripts/seed_crypto_demo.py`, password `Demo@2026!` |

## 4. Built but not checked in a real browser

| Screen or flow | Status |
| --- | --- |
| Crypto Setup page, Country Rules settings | 🟡 |
| Pay vendor → USDC preview → OTP → Safe signing → receipt | 🟡 preview and policy error seen; signing never run |
| Bill checks: `WALLET_MISMATCH`, `OVERBILLED_VS_CONTRACT` | 🟡 API-tested, not clicked through |
| On-chain ledger tab, Audit page, public `/verify` page | 🟡 `/verify` partly seen via your proof file |
| Dashboard, Reports, other screens in USD | 🟡 |

## 5. Not built

| Item | Note |
| --- | --- |
| WhatsApp invoice intake | Stretch goal; use email intake |
| Xero / QuickBooks / Zoho write-back | After the hackathon |
| Safe cancel/replace of a stuck nonce | Documented in edge cases, not built |
| Tally voucher delivery | XML is generated and stored; no desktop-agent pickup |
| Receipt email to the vendor for USDC payouts | Not sent |
| Inbound receivables matching | Stretch goal |

## 6. Known issues and things to remember

- **Pre-existing test failures:** 21 backend and 7 frontend tests fail exactly as in the original `PILOT-TESTING`. Nothing new fails.
- **Your Mac's IP keeps changing** (mobile carrier). Dropped connections leave stale database sessions that can hold worker locks. If workers or the bills list stall, use stable Wi-Fi and restart the backend.
- **The database is the 1 GB t2.micro** with port 5432 open. Fine for the demo, not for production.
- **Nothing is committed to git.** All changes are uncommitted files.
- **Docs:** `docs/crypto-rail/00`–`09` plus `README.md`; some diagrams were syntax-checked but not rendered.
- **Legal gate** (Indian counsel opinion on FIU-IND / FEMA) is still open and blocks any real-money use.

## 7. Demo run-of-show vs readiness

| Demo step | Ready? |
| --- | --- |
| 1 Onboard Lumen | ✅ seeded |
| 2 Onboard Nova's wallet | ✅ done live |
| 3 Invoice with wrong wallet | 🟡 seeded, flag not yet clicked |
| 4 Overbilling caught | 🟡 seeded, flag not yet clicked |
| 5 Approve and pay in USDC, Safe signing | ⏳ waiting on relayer ETH |
| 6 On-chain reconciliation | ✅ watcher proven on the funding transfer |
| 7 Tax and books | 🟡 withholding shown; Tally XML generated |
| 8 Audit proof and tamper check | ✅ proof works; on-chain anchor ❌ |
| 9 India guardrail | 🟡 API-proven; not clicked through |
