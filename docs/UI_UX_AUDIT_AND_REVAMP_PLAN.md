# Onsumm.ai — UI/UX Audit & Enterprise Revamp Plan

**Auditor role:** Senior UI/UX Manager (adversarial / devil's-advocate review)
**Date:** 2026-08-01
**Branch:** `ui-revamp` (cut from `uat2`)
**Scope:** Entire `frontend/` React app — 15 pages, 8 components, 4 landing pages, ~25,600 LOC
**Target bar:** Production-grade SaaS used daily by 100+ enterprises, each processing ₹100 crore+ of annual expense

---

## 0. How to read this document

- **Part A** — Executive verdict and scorecard
- **Part B** — Systemic (cross-cutting) defects. These are the expensive ones.
- **Part C** — Page-by-page findings, every page
- **Part D** — Enterprise readiness gaps (what procurement / IT-security / CFO will block on)
- **Part E** — The step-by-step revamp plan (7 phases, sequenced, with acceptance criteria)
- **Part F** — Appendices: token spec, contrast table, QA checklist, definition-of-done

Every finding carries a severity:

| Severity | Meaning |
|---|---|
| **P0 — Blocker** | Will lose a deal, cause a wrong financial decision, or fail an accessibility/security review |
| **P1 — Critical** | Daily friction for power users; visibly "not enterprise grade" |
| **P2 — Major** | Erodes trust and polish; competitors do this better |
| **P3 — Polish** | Craft-level; the difference between "good" and "premium" |

Findings reference real code as `file:line`.

---

# PART A — Executive verdict

## A.1 The one-paragraph verdict

This application is **functionally impressive and visually unfinished**. The domain logic — approval chains, TDS, GST checksums, FX exposure, Nova reconciliation, PAN-based vendor dedup — is genuinely sophisticated and, in places, better thought through than most Indian AP products. But the presentation layer was built page-by-page by accretion, with **no design system, no routing, no responsive strategy, and effectively no accessibility work**. There are **194 distinct hex colours**, **21 distinct font sizes**, **19 border-radius values**, **23 z-index values**, and **19 separate injected `<style>` blocks** across the codebase. The single most-used text colour in the app (`#9ca3af`, 285 occurrences) fails WCAG AA at **2.54:1**. The Bills table — the primary work surface — renders **every bill in the DOM with no pagination or virtualisation**, which will not survive a customer with 40,000 bills a year. In its current state this UI would pass a demo and fail a procurement review.

**It is very fixable.** Almost all of the damage is in a thin presentation layer, and the underlying component structure is sane. The plan in Part E gets to an enterprise bar in roughly 8–10 focused weeks without touching business logic.

## A.2 Scorecard

| Dimension | Score | Verdict |
|---|---:|---|
| Visual design & brand consistency | **3 / 10** | Three competing colour systems; two brand identities fighting |
| Design system / tokens | **1 / 10** | Tokens exist in `index.css` and are used by exactly one page |
| Typography | **3 / 10** | A font is referenced 32× and never loaded in the app shell |
| Colour & contrast (WCAG) | **2 / 10** | Most-used text colour fails AA; status colours fail on white |
| Accessibility (WCAG 2.2 AA) | **1 / 10** | 5 aria attributes, 0 focus-visible, 63 `outline:none`, custom toggles unreachable by keyboard |
| Responsiveness | **2 / 10** | 8 media queries in 25.6k LOC; Bills/Vendors/Settings/Reports have **zero** |
| Information architecture | **4 / 10** | 10 sidebar items with no grouping; overlapping tab + filter mechanisms |
| Navigation & routing | **1 / 10** | No URL routing at all. No deep links, no browser Back, refresh loses your place |
| Data density & table UX | **3 / 10** | 15 columns, no column sort, no column chooser, no pagination, no saved views |
| Forms & validation | **4 / 10** | Good GSTIN checksum work; no unsaved-changes guard, weak required-field enforcement |
| Feedback, errors, empty states | **5 / 10** | Genuinely good error copy in Integrations; `window.confirm()` used for destructive bulk actions |
| Performance perception | **3 / 10** | Full-list fetch + full-list render; 650ms modal animations block work |
| Trust & polish signals | **3 / 10** | Emoji as UI icons, dead links, `© 2025`, "View Details" button that does nothing |
| **Overall** | **2.7 / 10** | **Demo-ready, not enterprise-ready** |

## A.3 The five things that will lose you a ₹100cr client

1. **No URL routing.** A CFO cannot send a colleague a link to a specific bill. Refreshing the page loses their work context. Browser Back does nothing. `App.tsx:38` — the entire app is `useState`.
2. **The Bills table will not scale.** `BillsPage.tsx:3927` renders every bill. `api/invoices.ts:907` fetches every bill. A client with ₹100cr of spend has 20k–60k bills/year. This is a hard wall, not a slowdown.
3. **Accessibility is effectively zero.** Large Indian enterprises and every MNC subsidiary now run a WCAG check during vendor onboarding. 63 instances of `outline: none` with no `:focus-visible` replacement means the app is unusable by keyboard.
4. **Auth tokens are placed in URL query strings.** `api/invoices.ts:20-24` appends `?access_token=<JWT>` to image and document URLs. This lands in server logs, browser history, and `Referer` headers. This is a straight IT-security rejection.
5. **The product looks like three different products.** Login/Landing use navy `#002a58` + teal `#00696b`. The app shell uses `#004080`. The pages use Tailwind-default blue `#2563eb`. A buyer sees inconsistency and infers immaturity.

---

# PART B — Systemic defects

These are cross-cutting. Fixing them fixes dozens of page-level symptoms at once.

## B.1 There is no design system — P0

**Evidence.** `src/index.css:11-42` defines a proper token set (`--blue`, `--gray-*`, `--radius`, `--shadow`). Across the whole `src/` tree, `var(--` appears **14 times**, and almost all of them are in one file: `DonePage.tsx:30-43`. Every other page hardcodes raw hex.

**Consequences measured in the codebase:**

| Metric | Count | Enterprise target |
|---|---:|---:|
| Unique hex colours | **194** | ≤ 40 (tokenised) |
| Unique font sizes | **21** (8, 9, 10, 10.5, 11, 11.5, 12, 12.5, 13, 13.5, 14, 15, 16, 17, 18, 20, 21, 22, 24, 26, 32) | 8 |
| Unique border-radius values | **19** (0…16, 20, 99, 999, 9999) | 4 |
| Unique z-index values | **23** (0…10000) | 6 named layers |
| Injected `<style>` blocks | **19** | 0 (or 1 global sheet) |
| Distinct button paddings | **20+** | 3 sizes |

**Why this is P0, not cosmetic.** Every new feature costs 2–3× more to build because there is nothing to reuse. Every visual bug must be fixed 15 times. A rebrand is a multi-week project. Two engineers building two features will produce two different-looking buttons — which is exactly what happened.

**Three competing colour systems currently in the codebase:**

| System | Primary | Accent | Defined in | Used by |
|---|---|---|---|---|
| A — "Sovereign Ledger" | `#002a58` | `#00696b` teal | `LandingApp.tsx:20-31` (local const `C`) | Landing, Login |
| B — App shell navy | `#004080` (58 uses) | `#00696b` | Nowhere — hardcoded | Sidebar, TopBar, Upload |
| C — Tailwind default | `#2563eb` (116 uses) | `#7c3aed`, `#4f46e5`, `#6366f1` | Nowhere — hardcoded | Bills, Dashboard, Vendors, Settings, Reports |

`Sidebar.tsx:230` even uses `background: #004080 !important` — a specificity fight inside a component with no competing stylesheet, which tells you the author didn't trust the cascade.

## B.2 Typography is broken in the authenticated app — P1

**`'Public Sans'` is referenced 32 times across 9 files and is never loaded in the app shell.**

- `index.html:8` loads **only Inter**.
- `'Public Sans'` is imported *only* via `@import` inside injected `<style>` tags in `LoginPage.tsx:288` and `LandingApp.tsx:208`.
- Those `<style>` tags are unmounted when the user logs in.
- So `Sidebar.tsx:163`, `TopBar.tsx:283`, `UploadPage.tsx:388,533,633` request a font that isn't there and silently fall back to the generic system sans-serif.

**User-visible result:** the logo and section headings render in one typeface on the login screen and a *different* typeface one second later inside the app. Most users won't name it — they'll just feel that the product is cheap.

**Secondary problems:**
- `@import` inside an injected `<style>` is render-blocking and defeats `<link rel=preload>`.
- Both `@import`s request Inter again at different weights (400/500/600) than `index.html` (300–700) — duplicate font downloads.
- 21 font sizes with no scale. Sizes of `8px`, `9px`, `10px`, `10.5px` appear in production UI (`bm__step-actor-role` at `BillsPage.tsx:4372` is **8px**). 8px text is unreadable and, in a financial approval chain, that's where the *approver's name* is displayed.

## B.3 Colour & contrast fail WCAG AA — P0

Measured contrast ratios (WCAG 2.2, normal text needs 4.5:1, large text 3:1):

| Foreground | Background | Ratio | AA normal | Occurrences | Where |
|---|---|---:|---|---:|---|
| `#9ca3af` | `#ffffff` | **2.54** | ❌ FAIL | **285** | Every field label (`bm__lbl`), every "—" hint |
| `#9ca3af` | `#f9fafb` | **2.43** | ❌ FAIL | many | Labels on grey cards |
| `#94a3b8` | `#ffffff` | **2.56** | ❌ FAIL | 23 | `db__card-sub`, all dashboard card subtitles |
| `#94a3b8` | `#f1f5f9` | **2.34** | ❌ FAIL | 23 | Dashboard page background |
| `#d1d5db` | `#ffffff` | **1.47** | ❌ FAIL | 91 | The "—" empty-value marker in every table |
| `#cbd5e1` | `#ffffff` | **1.48** | ❌ FAIL | — | Chart axis labels, `ReportsPage.tsx:68` |
| `#c3c6d2` | `#ffffff` | **1.70** | ❌ FAIL | — | Borders + `dz` dropzone text |
| `#8a94a6` | `#ffffff` | **3.06** | ❌ FAIL | — | Bills search icon `BillsPage.tsx:3553` |
| `#737781` | `#ffffff` | **4.48** | ❌ FAIL (marginal) | 40 | All landing/upload secondary text |
| `#10b981` | `#ffffff` | **2.54** | ❌ FAIL | 34 | Success text, "Paid" indicators |
| `#f59e0b` | `#ffffff` | **2.15** | ❌ FAIL | 20 | Warning text, FX warning dots |
| `#059669` | `#ffffff` | **3.77** | ❌ FAIL | 85 | "Net Payable" amounts, Pay Now button label |
| `#ffffff` | `#10b981` | **2.54** | ❌ FAIL | — | White text on green buttons |
| `#ffffff` | `#f59e0b` | **2.15** | ❌ FAIL | — | White text on amber buttons |
| `rgba(255,255,255,0.4)` | `#002a58` | **3.45** | ❌ FAIL | — | `lp__feat-sub`, `lp__stat-lbl` on Login |
| `rgba(255,255,255,0.35)` | `#002a58` | **2.98** | ❌ FAIL | — | Landing footer copyright |

**Passing, keep these:** `#2563eb` on white (5.17), `#6b7280` on white (4.83), `#dc2626` on white (4.83), `#004080` on white (10.27), badge pairs `#15803d/#f0fdf4` (4.79) and `#b45309/#fffbeb` (4.84).

**Additional colour defects:**

- **P1 — Colour is the only signal in several places.** `BillsPage.tsx:3969` marks an overdue due date purely by turning it red. `BillsPage.tsx:3939-3944` shows FX warning vs opportunity as a 7px amber dot vs a 7px green dot — indistinguishable to the ~8% of male finance staff with red-green colour vision deficiency, on a control that materially affects payment timing.
- **P1 — Status colour semantics are inconsistent across pages.** `BillsPage.tsx:24-31` maps `APPROVED` to `#def7ea/#1d7a5f`. `DashboardPage.tsx:27` maps `APPROVED` to `#f0fdf4/#059669`. Same status, two greens, two pages. A user cannot learn one visual language.
- **P2 — Five different page canvas backgrounds.** `body` `#f3f4f6` (`index.css:5`), Bills `#f5f7fb`, Dashboard `#f1f5f9`, Upload `#f8f9ff`, Reports/DataExtraction/AgentHub `#f9fafb`. Switching pages produces a visible flash of a different grey.
- **P2 — Gradient KPI cards.** `DashboardPage.tsx:437-462` uses four saturated gradients (`#1d4ed8→#3b82f6`, `#065f46→#10b981`, `#92400e→#f59e0b`, `#4c1d95→#8b5cf6`). This reads as consumer-fintech, not CFO-grade. It also makes the four numbers visually compete rather than rank.

## B.4 Accessibility is effectively absent — P0

Hard counts across all `.tsx`/`.ts`/`.css`:

| Signal | Count | Comment |
|---|---:|---|
| `aria-*` attributes | **5** | For a 25,600-line app |
| `role="…"` | **3** | |
| `:focus-visible` rules | **0** | |
| `outline: none` / `outline: 'none'` | **63** | Focus ring removed, never replaced |
| `prefers-reduced-motion` | **0** | Despite 20+ animations |
| `<label>` elements | 50 | Many are decorative, not `htmlFor`-bound |

**Specific blockers:**

1. **Keyboard traps and unreachable controls.**
   - `AgentHubPage.tsx:149-154` — the master "Vendor Bill Agent On/Off" switch is a `<span onClick>` inside a `<label>` with no form control. Not focusable, no `role="switch"`, no `aria-checked`. A keyboard user **cannot turn the agent on or off.**
   - `AgentHubPage.tsx:240-243` — same pattern for every watched-contact toggle.
   - `LoginPage.tsx:187` — the show/hide password button is `tabIndex={-1}`. Keyboard users cannot verify what they typed.

2. **Modals are not accessible dialogs.** `BillsPage.tsx:4234` (`bm__overlay`), `DashboardPage.tsx:962` (`db__drill`), `BillsPage.tsx:636`/`726` (nested reconciliation modals) — none have `role="dialog"`, `aria-modal`, focus trapping, focus restoration on close, or an accessible name. `BillsPage.tsx:928` adds Escape handling on one modal only. The nested Nova override modal at `BillsPage.tsx:726` sits on top of another modal with no Escape handler at all.

3. **Tables are not accessible.** No `<caption>`, no `scope="col"` on `<th>`, no `aria-sort`, no row-count announcement. Screen-reader users get 15 unlabelled columns.

4. **Motion.** `BillsPage.tsx:4219-4232` — the bill modal animates from `scale(0.028)` with `border-radius: 50%` over **650ms**. `DashboardPage.tsx:978-983` — the drill-down does the same over 320ms from `scale(0.06)`. These are vestibular-trigger animations with zero `prefers-reduced-motion` escape hatch, and 650ms is a *long* time to wait when you are reviewing your 80th bill of the day.

5. **Icon-only controls without accessible names.** `BillsPage.tsx:1176` (close `<X>`), `BillsPage.tsx:3876` (`<Trash2>` discard draft), `VendorsPage.tsx:2373` (delete bank account). `title=` is used 99 times as a substitute — `title` is not exposed reliably to screen readers and never appears on touch devices.

6. **Emoji used as functional UI.** 17 instances. `BillsPage.tsx:3553` `⌕` as the search icon, `:3578` `⊞ Filters`, `:3748` `⚡`, `:4063` `⏸ Hold`, `:4071` `🗑 Delete`, `:2094`/`:2105` in the bill modal, `LoginPage.tsx:265` `✅`, `DashboardPage.tsx:417` `👋`, `VendorsPage.tsx:2052,2373` `🗑`. Emoji render differently per OS, get announced literally by screen readers ("wastebasket"), and — for a delete action on financial records — read as unserious.

## B.5 No routing, no deep links, no state persistence — P0

- No router library. `frontend/package.json` has no `react-router`. Navigation is `App.tsx:38` `useState<NavPage>`.
- `window.history` is used exactly twice, both in `VendorsPage.tsx:1448,1452`, only to strip an OAuth callback param.
- **Consequences:**
  - You cannot share a link to a bill, a vendor, a report, or a settings tab.
  - Browser Back exits the app or does nothing.
  - Refresh (F5) resets to the role default page (`App.tsx:29-33`) and drops all filters, the open bill, the review queue, and the upload queue.
  - Sessions live in `sessionStorage` (`AuthContext.tsx:54`) — closing the tab logs the user out with no warning and no "resume where you left off".
  - Browser tab title is permanently `Onsumm — Invoice Processing` (`index.html:6`), so a user with 6 tabs open cannot tell them apart.
  - No analytics or support triage is possible ("what page were you on?" has no answer).

**In an approval workflow product this is close to disqualifying.** The core enterprise motion is: approver receives an email → clicks a link → lands on the bill. That flow cannot exist without routing.

## B.6 The Bills table will not scale — P0

`api/invoices.ts:907-929` — `fetchBills()` takes no `limit`, `offset`, `page`, or `cursor`. It requests `/api/bills` and caches the entire response.
`BillsPage.tsx:3366-3417` — all filtering is client-side, in a `.filter()` over the full array, re-run on every keystroke.
`BillsPage.tsx:3927` — `displayBills.map(...)` renders **every** row. No pagination control exists anywhere in the codebase (grep for `paginat|pageSize|react-window|virtual` returns nothing relevant).

Each row is not cheap: it mounts `PriorityBadge` (with its own popover state), `TdsBadge`, `PaymentCell` (with its own popover), plus 15 `<td>`s.

**Projected behaviour** for a client with ₹100cr annual spend (~25,000 bills/year):

| Bills | DOM nodes (≈40/row) | Initial JSON | Realistic outcome |
|---:|---:|---:|---|
| 500 | 20,000 | ~1 MB | Sluggish but usable |
| 5,000 | 200,000 | ~10 MB | Multi-second freeze on every filter keystroke |
| 25,000 | 1,000,000 | ~50 MB | Tab crash / OOM on mid-spec laptops |

The same pattern exists in `TopBar.tsx:33` (fetches **all** bills just to build a notification dropdown) and `useBills.ts` (Dashboard and Reports both pull the full list to compute aggregates client-side — aggregation that belongs in SQL).

## B.7 Responsiveness is not implemented — P1

**8 `@media` queries in the entire 25,600-line codebase**, in 6 files: `LandingApp`, `LoginPage`, `UploadPage`, `DashboardPage`, `DataExtractionPage`, `VendorsPage`.

**Zero media queries in:** `BillsPage` (4,777 lines), `SettingsPage` (3,640), `ReportsPage` (1,179), `ReviewPage` (2,061), `IntegrationsPage`, `AgentHubPage`, `NovaPage`, `PaymentGatewayPage`.

Hard-coded layout assumptions:
- `ReviewPage.tsx:1533` — `min-width: 520px` on the form panel, next to a PDF panel. Below ~1,180px this overflows horizontally.
- `ReviewPage.tsx:1525` — `height: calc(100vh - 54px)` hardcodes the TopBar height; any change to `TopBar` (`.tb { height: 54px }`) silently breaks it.
- `App.tsx:106` — `marginLeft: 56` hardcodes the collapsed sidebar width, duplicating `Sidebar.tsx:31 COLLAPSED_W`.
- `BillsPage.tsx:4244` — `max-width: 1400px` on the bill modal, `bm__left { flex: 0 0 58% }`. On a 1366×768 laptop (still common in Indian enterprise) the PDF pane is ~560px wide — too narrow to read an invoice without zooming.
- `LoginPage.tsx:556-562` — below 820px the entire left brand panel is `display: none`. Every value proposition, every stat, the whole reason to trust the product, vanishes on mobile.

**There is no mobile or tablet story at all.** For a CFO who approves on a phone at an airport — the single highest-value moment in an AP product — the answer today is "you can't."

## B.8 Interaction & feedback quality — P1

- **`window.confirm()` for destructive bulk operations.** `BillsPage.tsx:3490` — bulk soft-delete of N bills. `BillsPage.tsx:3232` — discard an agent draft. `SettingsPage.tsx:1228` — save user permission changes. 9 occurrences total. A native OS dialog cannot show *which* bills, cannot show the total value at risk, cannot be styled, and cannot be tested. Deleting ₹4 crore of payables behind a browser-chrome "OK/Cancel" is not acceptable.
- **No unsaved-changes guard anywhere.** `grep beforeunload` → 0 results. `ReviewPage` holds a full session of hand-corrected OCR data (`ReviewPage.tsx:176 sessionsRef`). Refresh or accidental Back = total loss with no warning. `App.tsx:74 goBack()` discards the review queue unconditionally.
- **No global toast/notification system.** Each page invents its own: `BillsPage.tsx:4093` `bulkToast`, `VendorsPage.tsx:1077` "Alert toast", `SettingsPage.tsx:355` inline "Saved!" text that self-clears after 2s. Different positions, different durations, different shapes. Toasts are not announced to assistive tech (`aria-live` absent).
- **Loading states are inconsistent.** Skeletons exist in `ReviewPage.tsx:692`, `VendorsPage.tsx:2114`, and `BillsPage.tsx:140-167`. Everywhere else it's a centred grey "Loading…" string (`DashboardPage.tsx:401`, `BillsPage.tsx:3922`, `SettingsPage` ×8). Layout jumps when content arrives.
- **Optimistic updates without rollback UI.** `AgentHubPage.tsx:82-85` flips the toggle, then reverts silently on failure with no message. The user sees the switch flick back and doesn't know why.
- **`onMouseEnter`/`onMouseLeave` used for hover styling in ~15 places** (`BillsPage.tsx:3775`, `DashboardPage.tsx:573`, `AgentHubPage.tsx:202`). This never fires on touch, causes re-renders, and is unnecessary — CSS `:hover` exists.

## B.9 Information architecture — P1

**Sidebar (`Sidebar.tsx:18-29`) is a flat list of 10 items with no grouping:**
`Upload Invoices · Bills · Dashboard · Vendors · Reports · Data Extraction · Nova · Agent Hub · Integrations · Settings`

Problems:
- **Ordering is wrong.** "Upload Invoices" is first; "Dashboard" — the natural landing page — is third.
- **No grouping.** Daily work (Bills, Upload), analysis (Dashboard, Reports), master data (Vendors), automation (Agent Hub, Nova, Data Extraction), and admin (Integrations, Settings) are visually equal.
- **Opaque labels.** "Nova" and "Data Extraction" mean nothing to a new user; `Nova` is a customer-specific integration surfaced as a top-level nav item for every tenant.
- **Hover-to-expand with no pin.** `Sidebar.tsx:51-58` — labels only appear on hover, with a 120ms leave timer. A new user sees 10 unlabelled icons. There is no way to lock the sidebar open. `title={!open ? label : undefined}` is the only fallback, which fails on touch.
- **Duplicate, competing entry points.** Bill approvals are reachable from the sidebar Bills item, the TopBar notification bell, the Dashboard "Due & Overdue" card, and the Dashboard KPI drill-downs — four different visual treatments of the same underlying list.
- **`Settings` contains 10+ sub-sections** (`SettingsPage.tsx:2724-2733`: Approval Chain, Roles, Team, Brands, Cost Centers, Charges, Categories, COA Rules, TDS, FX) in a 3,640-line file with no search and no deep-linkable URL per section.

## B.10 Code-health issues with direct UX consequences — P2

- **19 injected `<style>` blocks.** Every page ships its own global CSS at runtime. Class names are globally scoped (`.card`, `.tab`, `.badge`, `.dot`, `.err`, `.f`, `.sec` in `UploadPage`/`ReviewPage`) — these **will** collide. `UploadPage.tsx:392 .card` and `ReviewPage.tsx` `.sec` are generic enough to leak into any page rendered simultaneously.
- **Two components define `@keyframes spin`** (`UploadPage.tsx:706`, `BillsPage.tsx:4152`, `DashboardPage.tsx:860`) — last one mounted wins.
- **Debug code in production.** `api/invoices.ts:96-97` — `// ── TEMP: axios timing interceptors ──` with `performance.now()` logging still active.
- **Four monolith files:** `BillsPage.tsx` 4,777 lines, `VendorsPage.tsx` 3,939, `SettingsPage.tsx` 3,640, `ReviewPage.tsx` 2,061. `BillsPage.tsx` alone contains 12 component definitions. Nothing is reusable, nothing is testable, and any two engineers touching Bills will conflict.
- **Zero tests.** No test runner in `package.json`. No Storybook. No visual regression. For a financial-approval UI this means every release is a manual QA gamble.

---

# PART C — Page-by-page findings

## C.1 Landing site (`landing/LandingApp.tsx` + 4 sub-pages)

| # | Severity | Finding | Evidence |
|---|---|---|---|
| L1 | **P0** | **Dead links in the footer.** "Blog", "Privacy Policy", and "Terms of Service" all navigate to the **home page**. Enterprise procurement and DPO review *require* a real privacy policy and ToS URL. This is a hard blocker in vendor onboarding. | `LandingApp.tsx:174-175` |
| L2 | **P1** | **Two nav labels, one destination.** "Product" and "Integrations" both route to `features`. Because the active state is `page === link.page`, **both light up simultaneously** — the user sees two active tabs. | `LandingApp.tsx:13-14, 73-75` |
| L3 | **P1** | **Fake social buttons.** The `𝕏` and `in` chips are `<div>`s with `cursor: pointer` and no `onClick`, no `href`. They look clickable and do nothing. | `LandingApp.tsx:164-168` |
| L4 | **P1** | **Stale copyright: `© 2025`.** Hardcoded. It is 2026. The first thing a diligence reviewer notices. | `LandingApp.tsx:190` |
| L5 | **P1** | **A fourth token system.** `const C = {...}` "Sovereign Ledger tokens" is defined locally in this one file and shared with nothing. | `LandingApp.tsx:20-31` |
| L6 | **P2** | Footer text `rgba(255,255,255,0.35)` on `#002a58` = **2.98:1**. Fails AA. Column headings at `0.4` = 3.45:1, also fail. | `LandingApp.tsx:178, 190` |
| L7 | **P2** | Unsubstantiated claim "**99.9% accuracy** on Indian formats" with no methodology, sample size, or footnote. Sophisticated enterprise buyers discount the whole page when they see an unqualified 99.9%. | `LoginPage.tsx:123` |
| L8 | **P2** | `window.scrollTo({behavior:'smooth'})` on every nav click with no `prefers-reduced-motion` check. | `LandingApp.tsx:202` |
| L9 | **P3** | No SEO metadata, no OG tags, no favicon (`index.html:5` still points at `/vite.svg`). | `index.html:5` |

## C.2 Login (`pages/LoginPage.tsx`)

| # | Severity | Finding | Evidence |
|---|---|---|---|
| LG1 | **P0** | **Sign In is enabled with an empty password.** `disabled={loading \|\| !username}` — password is not checked. Submitting fires a pointless network round-trip and returns the generic "Invalid username or password", so the user is told their *credentials* are wrong when they simply left a field blank. | `LoginPage.tsx:203` |
| LG2 | **P1** | **Show/hide password is keyboard-inaccessible** (`tabIndex={-1}`). On a long generated password this is the single most-used control on the screen. | `LoginPage.tsx:187` |
| LG3 | **P1** | **Four decorative nav links.** `NAV_LINKS = ['Home','Product','Pricing','About']` all call the same `onBack()`. Four labels, one behaviour — actively misleading. | `LoginPage.tsx:79, 90` |
| LG4 | **P1** | **OTP field is a plain text input.** No `inputMode="numeric"`, no `autoComplete="one-time-code"` (which would enable iOS/Android SMS autofill), no digit-only masking, no paste handling, no visible countdown, no resend cooldown. "Resend OTP" is styled as a back-link and routes the user backwards through the flow. | `LoginPage.tsx:231, 237` |
| LG5 | **P1** | **No session/enterprise auth affordances.** No "Remember me", no SSO / SAML / Google Workspace button, no Caps-Lock warning, no lockout feedback, no MFA. Every 100-person finance org will ask for SSO. | whole file |
| LG6 | **P2** | Password reset accepts **6 characters** with no strength meter and no confirm field. | `LoginPage.tsx:249, 256` |
| LG7 | **P2** | Mobile (<820px) hides the entire left panel — brand, value props, and trust signals all disappear. | `LoginPage.tsx:556-562` |
| LG8 | **P2** | Feature subtitles at `rgba(255,255,255,0.4)` = **3.45:1**, stat labels at 10px + same colour. Fails AA. | `LoginPage.tsx:419, 436` |
| LG9 | **P3** | `✅` emoji at 40px as the success icon, inside an app that otherwise uses Lucide. | `LoginPage.tsx:265` |
| LG10 | **P3** | `@import` of Google Fonts inside an injected `<style>` — render-blocking, duplicates the `index.html` Inter request. | `LoginPage.tsx:288` |

## C.3 App shell — Sidebar (`components/Sidebar.tsx`)

| # | Severity | Finding | Evidence |
|---|---|---|---|
| S1 | **P1** | **Hover-only labels with no pin/expand toggle.** Ten unlabelled 17px icons by default. Discoverability is near zero for a new approver who logs in twice a month. | `Sidebar.tsx:41-58, 242-251` |
| S2 | **P1** | **Fails on touch entirely.** `onMouseEnter` is the only expansion trigger; `title` tooltips never fire on touch. On a tablet the sidebar is permanently a strip of mystery glyphs. | `Sidebar.tsx:63-72, 88` |
| S3 | **P1** | **"Need Help?" is a dead button.** `<button className="sb__help">` with no `onClick`. Prominent, styled, does nothing. | `Sidebar.tsx:100-106` |
| S4 | **P2** | **Logout has no confirmation and no unsaved-work check.** One click from an icon-only 26px button destroys an in-progress review queue. | `Sidebar.tsx:115` |
| S5 | **P2** | Flat 10-item list, no grouping, no section headers, no counts/badges (Bills has pending approvals but the sidebar never says so). | `Sidebar.tsx:18-29` |
| S6 | **P2** | Collapsed width duplicated as a magic number in `App.tsx:106` (`marginLeft: 56`) and `Sidebar.tsx:31` (`COLLAPSED_W`). They will drift. | both |
| S7 | **P3** | `background: #004080 !important` on the active item — specificity hack with no cause. | `Sidebar.tsx:230` |

## C.4 App shell — TopBar (`components/TopBar.tsx`)

| # | Severity | Finding | Evidence |
|---|---|---|---|
| T1 | **P0** | **JWT in a URL query string.** The brand logo `<img src>` is built with `withAccessToken(...)` which appends `?access_token=<JWT>`. This leaks the access token into browser history, CDN/proxy access logs, and `Referer` headers. Also used for PDF documents (`BillsPage.tsx:343`) and draft files. **Straight IT-security rejection.** | `TopBar.tsx:64`, `api/invoices.ts:20-24` |
| T2 | **P1** | **Fetches every bill in the company to render a dropdown.** `fetchBills(...)` then `.filter()` client-side for the notification list. | `TopBar.tsx:33-40` |
| T3 | **P1** | **Notification count and list can disagree.** `count` comes from `fetchNotificationCount` (server) while the list comes from a client-side filter of all bills. The badge can read "5" while the dropdown shows 3 rows. | `TopBar.tsx:32-40, 133, 140` |
| T4 | **P1** | **"Help" button does nothing.** No `onClick`. Second dead help affordance (see S3). | `TopBar.tsx:124-127` |
| T5 | **P2** | Brand switcher is silent about consequences. Switching brands (`TopBar.tsx:67`) resets filters, clears cost-centre selection (`BillsPage.tsx:3169`), and reloads all data — with no confirmation and no indication that unsaved work elsewhere may be affected. | `TopBar.tsx:67-70` |
| T6 | **P2** | `dangerouslySetInnerHTML`-equivalent: the logo `onError` handler writes a raw SVG string into `img.parentElement.innerHTML`. Fragile and an XSS-shaped pattern even if currently static. | `TopBar.tsx:108-112` |
| T7 | **P2** | Dropdowns close on `mousedown` outside but **not on Escape**, and do not trap or restore focus. | `TopBar.tsx:43-51` |
| T8 | **P3** | Notification badge caps at "9+" with no way to see the true number. | `TopBar.tsx:133` |

## C.5 Upload Invoices (`pages/UploadPage.tsx`)

| # | Severity | Finding | Evidence |
|---|---|---|---|
| U1 | **P0** | **Four of the five tabs are non-functional.** "From URL / Drive Link", "Google Drive", "Dropbox", "OneDrive" are rendered, clickable, and change `tab` state — but the dropzone below never changes. The user clicks "Google Drive", sees a PDF dropzone, and concludes the product is broken. | `UploadPage.tsx:26-32, 134, 141` |
| U2 | **P1** | **The copy contradicts the code.** Feature card says "Supports **PDF & Excel** files" but `accept: {'application/pdf': ['.pdf']}` rejects Excel, and the dropzone hint correctly says "Supports PDF". Two truths on one screen. | `UploadPage.tsx:21` vs `:61, :148` |
| U3 | **P1** | **"View Details" is a dead button.** Styled as a link, no handler. | `UploadPage.tsx:278` |
| U4 | **P1** | **The 5-step "Processing Pipeline" is theatre.** `stepState()` derives all five states from queue counts alone — "Validating" and "Completed" are never driven by real backend signals. It shows "Extracting Data" and "Processing" as simultaneously active because both map to the same condition. It implies observability the system does not have. | `UploadPage.tsx:98-113` |
| U5 | **P1** | **Rejected files vanish silently.** `useDropzone` is configured with `maxFiles: 20` and `maxSize: 50MB`, but `fileRejections` is never read. Drop 25 files and 5 disappear with no message. Drop a 60MB scan and nothing happens at all. There is even an unused `.err` style at `:486` — the error UI was planned and never wired. | `UploadPage.tsx:60-64` |
| U6 | **P1** | **Duplicate detection is by filename only.** `new Set(q.map(e => e.file.name))` — the same invoice saved as `inv.pdf` and `inv(1).pdf` both enter the queue. | `UploadPage.tsx:55-57` |
| U7 | **P2** | **All OCR fires in parallel with no concurrency cap.** `Promise.all(pending.map(...))` on up to 20 files. Twenty simultaneous OCR requests will rate-limit or time out; there is no retry for the ones that fail. | `UploadPage.tsx:75-84` |
| U8 | **P2** | No per-file progress — only an indeterminate bar (`:248`). For a 40-page scanned invoice the user has no idea whether to wait or refresh. |
| U9 | **P2** | "Recent Uploads" panel shows only the **current session's** queue, despite the name implying history. | `UploadPage.tsx:346-375` |
| U10 | **P3** | Page title is 21px here vs 26px on Bills vs 24px on Dashboard vs 22px on Vendors/Reports. | `UploadPage.tsx:388` |

## C.6 Review / Extraction editor (`pages/ReviewPage.tsx`) — the highest-value screen

This is where an accountant spends most of their day, and where a data error becomes a wrong payment.

| # | Severity | Finding | Evidence |
|---|---|---|---|
| R1 | **P0** | **No unsaved-changes protection.** All hand-corrected OCR lives in `sessionsRef` in memory. `onBack` (`App.tsx:74`) clears the queue unconditionally. No `beforeunload`. An accidental refresh after 20 minutes of correcting a 60-line invoice loses everything, silently. | `ReviewPage.tsx:176`, `App.tsx:74-77` |
| R2 | **P0** | **"Send for Approval" has no client-side validation gate.** `disabled={loading \|\| bulkLoading}` only. A bill with no vendor, no amount, no date, no category and an invalid GSTIN can be submitted into the approval chain. The GSTIN checksum work (`gstin.ts`) computes a real error and then **does not block submission**. | `ReviewPage.tsx:1463` |
| R3 | **P1** | **Bulk-send has no preview or confirmation.** `handleBulkIngest` at `:1467` sends the *entire remaining queue* into the approval chain from one button, with no summary of what is about to be submitted and no per-bill validation status. | `ReviewPage.tsx:1467` |
| R4 | **P1** | **Not responsive; hard minimum ~1,180px.** `min-width: 520px` on the form panel beside a PDF panel, inside `height: calc(100vh - 54px)`. Below that it overflows horizontally with no fallback. | `ReviewPage.tsx:1525, 1533` |
| R5 | **P1** | **AI-populated fields are not visually distinguished from human-entered ones.** `runEnrich` (`:183`) silently writes category, notes, ledger, and bank details into the form. The reviewer cannot tell what the model guessed vs what they typed — which is precisely the distinction an auditor will ask about. No confidence indicator, no "AI suggested" chip, no undo-to-original. | `ReviewPage.tsx:183-223` |
| R6 | **P1** | **Silent AI failures.** `runEnrich`, `fetchSuggestion`, `fetchSapMatch`, and the ledger fetch all end in `.catch(() => {})` or `catch { /* silent */ }`. When enrichment fails the user just sees empty fields and assumes the AI found nothing — rather than that it errored. | `:221, :344, :379, :288` |
| R7 | **P2** | Line-item table has no keyboard grid navigation (Tab/Enter/arrow), no bulk edit, no copy-down. On a 60-line invoice this is hundreds of mouse trips. | `ReviewPage.tsx` line-items section |
| R8 | **P2** | PDF viewer offers page-by-page only (`:1488-1490`) — no continuous scroll, no text search, no rotate, no fit-to-width/fit-to-page, no side-by-side field highlighting. Field-to-document linking is the single biggest accuracy accelerator in AP review and it is absent. |
| R9 | **P2** | The `500ms` debounced TDS preview (`:310`) has no "calculating" affordance beyond a boolean, so the TDS figure silently changes under the user's cursor. |
| R10 | **P3** | Cancel button (`:1462`) is styled identically in weight to a ghost action and sits immediately left of the primary submit — a mis-click discards the queue. |

## C.7 Bills (`pages/BillsPage.tsx`) — 4,777 lines, the core work surface

| # | Severity | Finding | Evidence |
|---|---|---|---|
| B1 | **P0** | **No pagination, no virtualisation, no server-side filtering.** See B.6. Hard scaling wall. | `:3927`, `api/invoices.ts:907` |
| B2 | **P0** | **No column sorting.** A 15-column financial table where you cannot sort by amount, due date, vendor, or status. The only sort is a binary "Priority / default" toggle (`:3736`). Ask any AP clerk to find the largest unpaid bill and they cannot. | `:3899-3918, 3736` |
| B3 | **P0** | **Destructive bulk actions behind `window.confirm()`.** Bulk soft-delete shows a native dialog with a count and nothing else — no list, no total value, no undo. | `:3486-3508` |
| B4 | **P1** | **Header count is wrong on the On Hold / Deleted tabs.** The header renders `filteredBills.length` (active bills) while the table renders `archivedBills`. On the Deleted tab the header can say "312 bills" above a table of 4. | `:3541` vs `:3420` |
| B5 | **P1** | **Export → "Excel (.xls)" and "CSV (.csv)" do exactly the same thing.** `exportBills` ignores its `_format` argument and always opens the same preview. Two menu items, one behaviour. | `:3361-3364, 3774, 3780` |
| B6 | **P1** | **Fake Excel export.** `doExportDownload('xlsx')` writes a **CSV string** with a BOM, labels it `application/vnd.ms-excel`, and names it `.xls`. Excel throws a "the file format does not match the extension" warning on open. The project already depends on `xlsx` (`package.json`) — it just isn't used here. | `:3352-3357` |
| B7 | **P1** | **Two overlapping status-filter mechanisms.** Tabs (`All / My Approvals / My Uploaded / All Approved / On Hold / Deleted / Draft`, `:3799-3808`) and a Status chip filter (`Pending/Approved/Rejected/Draft/Cancelled`, `:3604-3607`). Selecting the "All Approved" tab *and* the "Draft" chip yields zero rows with no explanation. | `:3368-3388, 3799` |
| B8 | **P1** | **650ms modal open animation from `scale(0.028)`.** For a user opening 60 bills a day that is 39 seconds of waiting per day, plus a vestibular-trigger zoom with no reduced-motion opt-out. | `:4219-4232, 4249` |
| B9 | **P1** | **No bulk selection persistence or cross-page awareness.** `checkedIds` is a raw `Set`; `toggleAll` only covers currently rendered rows (`:3452-3462`). With no pagination this is currently "all", but it silently breaks the moment pagination is added. |
| B10 | **P1** | **Nested modals.** Reconciliation mismatch popup (`:636`, z-index 1000) opens the override confirm (`:726`, z-index 1100) on top of the bill modal (z-index 1000). Escape is only handled on the outermost. Focus is never trapped or restored. |
| B11 | **P2** | 15 columns with no column chooser, no density control, no saved views, no sticky first column. The *export* dialog has a column picker (`:3302`) — the table does not. Inconsistent. |
| B12 | **P2** | Emoji as functional icons: `⌕` search (`:3553`), `⊞ Filters` (`:3578`), `⚡` (`:3748`), `↓ Export` (`:3763`), `⏸ Hold` / `🗑 Delete` (`:4063, 4071`), `⬆ Push to Tally` (`:4036`), `↩ Restore` (`:4084`). |
| B13 | **P2** | The Filters panel has ~9 filter groups in a 340px popover with no scroll container — on a 768px-tall laptop the TDS Status group at the bottom is clipped. | `:3584-3718` |
| B14 | **P2** | Approver names in the approval stepper render at **8px** (`bm__step-actor-role`) and 9px (`bm__step-actor-name`) — legally significant information at unreadable size. | `:4371-4372` |
| B15 | **P2** | Active filters are indicated only by a count badge and a "✕ Clear filters" link. There are no removable filter chips, so the user cannot see *which* filters are on without reopening the panel. | `:3559-3578, 3723` |
| B16 | **P2** | `ExportPdfButton` (`:356`) uses raw `fetch` without the auth wrapper and swallows all errors into a 3-second "Export failed" state with no detail and no retry. |
| B17 | **P3** | Column header "ID" shows a truncated 8-char hash (`:3972`) with the full value only in a `title` tooltip. Users cannot copy it. |

## C.8 Dashboard (`pages/DashboardPage.tsx`)

| # | Severity | Finding | Evidence |
|---|---|---|---|
| D1 | **P0** | **A materially misleading headline number.** The card is titled "**Daily Spend** — Approved bills · last 30 days" and shows a 30-day chart. The large number beside it is `fmt(m.thisMonth)`, which is `monthlyAmounts[29]` — **today's spend only**. The delta beneath reads "% **vs yesterday**". A CFO glancing at a card labelled "last 30 days" with one big number will read it as the 30-day total. This is a financial misread waiting to happen. | `:529-539, 340-342` |
| D2 | **P1** | **All aggregation is client-side over the full bill list.** `useBills()` pulls everything; `useMemo` at `:293-399` computes 20+ aggregates in the browser on every load. At 25k bills this is a multi-second main-thread block. These are `GROUP BY` queries. | `:276, 293` |
| D3 | **P1** | **No date-range control.** Every window is hardcoded: last 30 days, next 7 days, next 30 days. A finance user cannot ask "last quarter" or "FY 25-26". For a CFO dashboard this is the first thing they will try. | `:296-308, 334-337` |
| D4 | **P1** | **No brand / cost-centre / department segmentation** on the dashboard, despite the app being multi-brand and cost-centre aware everywhere else. |
| D5 | **P2** | Four saturated gradient KPI cards make all four numbers shout equally — no visual hierarchy, no primary metric. Reads consumer, not enterprise. | `:437-462` |
| D6 | **P2** | Drill-down panel animates from `scale(0.06)` with `border-radius: 50%`; no `prefers-reduced-motion`; not a `role="dialog"`; no focus trap. | `:958-983` |
| D7 | **P2** | The donut has no accessible alternative — status counts exist only as coloured arcs plus a legend with 9px swatches. Card subtitles at `#94a3b8` = **2.34:1** on the `#f1f5f9` canvas. | `:130-154, 916` |
| D8 | **P2** | Greeting + `👋` emoji (`:417`). Charming once; on the 400th login it is noise occupying the most valuable pixels on the page. |
| D9 | **P2** | `mom` variable name (month-over-month) holds a day-over-day value — a latent bug for the next engineer. | `:342` |
| D10 | **P3** | "Refresh" uses a fake 800ms `setTimeout` to keep the spinner visible rather than tracking the real request. | `:290` |
| D11 | **P3** | Inline `cursor:'pointer'` on hero KPIs contradicts `.db__hero-kpi { cursor: default }` in the stylesheet. Dead CSS. | `:464` vs `:892` |

## C.9 Vendors (`pages/VendorsPage.tsx`) — 3,939 lines

| # | Severity | Finding | Evidence |
|---|---|---|---|
| V1 | **P1** | No pagination or virtualisation; the same full-fetch pattern as Bills. |
| V2 | **P1** | No column sorting on the vendor table. |
| V3 | **P1** | Two media queries in 3,939 lines — effectively non-responsive. |
| V4 | **P1** | `🗑` emoji as the delete control for **GSTINs** (`:2052`) and **bank accounts** (`:2373`). Deleting a vendor bank account is one of the highest-risk actions in an AP system; it is behind an emoji. |
| V5 | **P2** | Page title 22px vs Bills 26px; subtitle `#9ca3af` = 2.54:1. | `:3026-3027` |
| V6 | **P2** | Excel import result shown as `✅ N vendors added · M skipped` (`:408`) with errors truncated to the first three joined by commas (`:411`) — no downloadable error report, no row numbers, no partial-retry. For a 2,000-row vendor master import this is unusable. |
| V7 | **P2** | OAuth redirect via `window.location.href` (`:1462`) with no interstitial explaining what is about to happen or which scopes are requested. |
| V8 | **P3** | Search is a single free-text box over name/GSTN/email with no advanced filters, despite the vendor master being the place people search hardest. |

## C.10 Reports (`pages/ReportsPage.tsx`)

| # | Severity | Finding | Evidence |
|---|---|---|---|
| RP1 | **P1** | **Top-6 hard cap on vendor breakdowns** with no "show all" and no indication that data is truncated. On a ₹100cr book with 400 vendors this hides ~98% of the data. | `:382, :487` |
| RP2 | **P1** | Client-side aggregation over the full bill list (`useBills`), same as Dashboard. |
| RP3 | **P1** | No date range picker, no comparison periods, no drill-through from a chart to the underlying bills, no scheduled/emailed reports, no PDF export. This is a chart page, not a reporting module. |
| RP4 | **P2** | Chart axis labels at `#cbd5e1` = **1.48:1** and 9px (`:68`). Effectively invisible. |
| RP5 | **P2** | Charts are hand-rolled SVG with no tooltips on `SparkBars` beyond a native `title` (`:59`), no legend interaction, no accessible table alternative. |
| RP6 | **P2** | Two separate colour palettes defined in one file — `VENDOR_COLORS` (8) and `CAT_COLORS` (10) — neither checked for colour-blind safety or contrast. | `:13-14` |

## C.11 Settings (`pages/SettingsPage.tsx`) — 3,640 lines

| # | Severity | Finding | Evidence |
|---|---|---|---|
| ST1 | **P1** | **No deep links to sections.** 10+ config areas (`:2724-2733`) behind local state. Support cannot say "go to /settings/tds". |
| ST2 | **P1** | **`window.confirm()` to save permission changes** — "Save changes to X?" in a native dialog, with no diff of what is changing. Permission changes are audit-relevant. | `:1228` |
| ST3 | **P1** | **No unsaved-changes guard.** Approval-chain edits live in local state; navigating away silently discards them. | `:352-362` |
| ST4 | **P1** | **Inconsistent, ephemeral save feedback.** `setSaved(true); setTimeout(..., 2000)` — a button label flickers to "Saved!" for 2s. Some sections do this, others show inline errors, others nothing. There is no single "unsaved changes / save / discard" bar. | `:352-362, 585-588` |
| ST5 | **P2** | Settings nav active state uses purple `#7c3aed` (`:2915`) — a colour used nowhere else as a primary. A fourth accent. |
| ST6 | **P2** | Each of the 10 sections invents its own empty state string and its own loading string (`chg__empty`, `coa__empty`, `cat__empty`, `tds__empty`) — 4 CSS classes for the same concept. |
| ST7 | **P2** | Approval-chain builder relies on drag handles (`GripVertical`) with no keyboard reordering alternative. |
| ST8 | **P2** | No settings search. Finding "TDS threshold" in a 10-section, 3,640-line page requires knowing where it lives. |

## C.12 Agent Hub (`pages/AgentHubPage.tsx`)

| # | Severity | Finding | Evidence |
|---|---|---|---|
| AH1 | **P0** | **The master agent on/off switch is keyboard-inaccessible.** `<label>` wrapping a `<span onClick>` with no input, no `role="switch"`, no `aria-checked`, no `tabIndex`. | `:149-154` |
| AH2 | **P0** | Same for every per-contact watch toggle — an agent that reads a vendor's mailbox can only be disabled with a mouse. | `:240-243` |
| AH3 | **P1** | **Silent optimistic-update rollback.** On failure the toggle flips back with no error message. The user believes they disabled the agent; it is still running. For a mailbox-reading agent this is a privacy incident. | `:83-85, 92-93` |
| AH4 | **P1** | **30-second polling with no visible "live" indicator and no pause.** `setInterval(refresh, 30000)` runs forever while the tab is open, including in background tabs. | `:73-76` |
| AH5 | **P2** | Entirely inline-styled (no CSS classes), so nothing here is reusable and none of it participates in any future theme. | whole file |
| AH6 | **P2** | Activity log has no filtering, no pagination (fixed `limit: 50`), no date grouping, and no export — for an audit surface. | `:54` |
| AH7 | **P2** | `Bot` icon on `#004080` here, agent card icon on `#f3e8ff`/`#7c3aed`, drafts button `#7c3aed`, Fact Graph button `#4f46e5` — four accents in one card. | `:115, 134, 180, 269` |

## C.13 Done (`pages/DonePage.tsx`)

| # | Severity | Finding | Evidence |
|---|---|---|---|
| DN1 | **P2** | **This is the only page that uses the design tokens.** It proves the tokens work and that every other page simply ignored them. | `:30-43` |
| DN2 | **P2** | Success screen shows three internal identifiers — `upload_id`, `stg_bill_id`, `line_items_count` — as monospace UUIDs. `stg_bill_id` ("staging bill id") is internal implementation vocabulary leaking to a finance user. No copy button, no "what happens next", no link to the specific bill just created. | `:17-21` |
| DN3 | **P3** | Terminal dead-end: the two exits are "Process Another" and "Go to Bills". There is no "View this bill" — the most likely next action. | `:22-25` |

## C.14 Integrations (`pages/IntegrationsPage.tsx`)

| # | Severity | Finding | Evidence |
|---|---|---|---|
| I1 | — | **Positive: `readError()` (`:26-36`) is the best error handling in the codebase** — parses FastAPI `detail`, maps 401/403/404/5xx to human copy, and explicitly refuses to dump raw HTML. **This should become the app-wide standard.** |
| I2 | **P1** | Uses raw `fetch` without the auth wrapper used elsewhere (`:39-63`) — inconsistent 401-refresh behaviour vs the rest of the app. |
| I3 | **P2** | No media queries; the connection cards will not reflow. |
| I4 | **P2** | Pairing-token UX: a token is generated and shown with a Copy button, but there is no expiry countdown surfaced prominently, no regenerate confirmation, and no "what to do next" checklist. |

## C.15 Nova / Data Extraction / Payment Gateway

| # | Severity | Finding | Evidence |
|---|---|---|---|
| N1 | **P1** | **"Nova" is a customer-specific integration promoted to a permanent top-level nav item for every tenant**, behind a generic `payables.view` permission. Every other customer sees a nav item that means nothing to them. Should be feature-flagged like `sap_matching` already is (`BillsPage.tsx:814`). | `Sidebar.tsx:25` |
| N2 | **P1** | `PaymentGatewayPage` is rendered as a full-screen overlay from inside `BillsPage` (`:4111`) rather than as a route — so the highest-consequence action in the product (disbursing money) has no URL, no back button, and no audit-friendly navigation history. | `BillsPage.tsx:4111-4118` |
| N3 | **P2** | `DataExtractionPage` title 22px, canvas `#f9fafb`, its own two media queries — a fourth layout convention. | `DataExtractionPage.tsx:173-179` |

---

# PART D — Enterprise readiness gaps

These are not "UI polish". These are the questions a ₹100cr client's procurement, IT-security, and internal-audit teams will ask in weeks 2–4 of an evaluation.

| # | Gap | Severity | Why they will block |
|---|---|---|---|
| E1 | **No SSO / SAML / OIDC.** Username + password only. | **P0** | Mandatory for 100+ seat deployments. No IT team will manage a second password estate. |
| E2 | **Tokens in URLs** (`api/invoices.ts:20`). | **P0** | Automatic finding in any pen-test or VAPT report. |
| E3 | **No WCAG 2.2 AA conformance / no VPAT.** | **P0** | Increasingly a standard RFP line item; mandatory for any listed company or MNC subsidiary. |
| E4 | **No Privacy Policy / Terms / DPA pages** — footer links go to the home page (`LandingApp.tsx:175`). | **P0** | DPO sign-off is impossible without them. |
| E5 | **No audit-trail UI for configuration changes.** Bills have a beautiful activity timeline (`BillsPage.tsx:1189`); approval chains, roles, TDS rules, and bank accounts have none. | **P0** | Internal audit will ask "who changed the approval threshold and when?" There is no answer in the UI. |
| E6 | **No session-expiry UX.** 60-minute tokens with silent refresh; `sessionStorage` means closing the tab logs you out. No idle warning, no "your session expired, here's your work" recovery. | **P1** | Approvers lose in-progress work with no explanation. |
| E7 | **No pagination / server-side query** → the product cannot demo at customer scale. | **P1** | The POC will be run on their real data. |
| E8 | **No mobile approval experience.** | **P1** | Approver mobility is the #1 stated reason finance teams buy AP software. |
| E9 | **No i18n / l10n scaffolding.** All strings hardcoded in English inside JSX. Currency is hardcoded `INR` in ~12 formatters even on the FX screens (`BillsPage.tsx:1084`). | **P1** | Blocks any non-INR entity and any multinational rollout. |
| E10 | **No print stylesheet.** Finance prints. `@media print` appears zero times. | **P2** | |
| E11 | **No empty-tenant onboarding.** A brand-new tenant sees "No bills found." with no guided setup for chains, categories, TDS, or brands. | **P2** | Time-to-first-value is a churn predictor. |
| E12 | **No in-app help, docs, changelog, or support entry point.** Both "Help" affordances are dead buttons (`Sidebar.tsx:100`, `TopBar.tsx:124`). | **P2** | Support cost per seat. |
| E13 | **No tests, no Storybook, no visual regression.** | **P2** | Every release is a manual QA gamble on a system that moves money. |
| E14 | **No dark mode.** | **P3** | Increasingly expected; also forces you to tokenise, which is the real win. |

---

# PART E — The revamp plan

## E.0 Principles

1. **Tokens before pixels.** Do not restyle a single page until the token layer exists. Otherwise you create a fourth colour system.
2. **Strangler pattern, not big-bang.** Ship the primitive library, then migrate page by page behind the existing structure. The app must be releasable at the end of every week.
3. **Bills and Review first.** They are 80% of user time and 100% of the financial risk.
4. **Accessibility is built in, not bolted on.** Every primitive ships keyboard-complete and AA-contrast on day one. Retrofitting a11y costs 5×.
5. **No new page ships without: a responsive layout, a loading state, an empty state, an error state, and keyboard operability.**

## E.1 Phase 0 — Foundation & guardrails (Week 1) — ✅ **COMPLETE**

**Goal:** make it impossible to keep drifting.

| # | Task | Acceptance criteria | Status |
|---|---|---|---|
| 0.1 | Add tooling: ESLint + `eslint-plugin-jsx-a11y`, Prettier, Stylelint. Add `vitest` + `@testing-library/react`. Add Storybook 8. | `npm run lint` and `npm run test` exist and pass in CI | ✅ `npm run verify` exits 0 |
| 0.2 | Add CI gate on `ui-revamp`: lint + typecheck + test + build must pass | PR cannot merge red | ✅ `.github/workflows/frontend-ci.yml` |
| 0.3 | Delete debug code: remove the TEMP axios timing interceptors | No `performance.now()` logging in prod bundle | ✅ 10 `console.*` calls removed; `no-console` now an ESLint **error** |
| 0.4 | **Fix the token-in-URL security issue.** Replace `withAccessToken()` with an `Authorization`-header fetch → blob-URL pattern | `grep access_token=` in `src/` returns 0 | ✅ all 8 sites; guarded by a test **and** a CI step |
| 0.5 | Add `react-router-dom` and a route skeleton | Every page has a URL; browser Back works | ✅ 12 routes + SPA rewrites |
| 0.6 | Load fonts correctly in `index.html`; remove both `@import`s | Public Sans renders inside the app; one request per family | ✅ |
| 0.7 | Add `<title>` management per route and a real favicon | Tab titles are distinguishable | ✅ |

**Deliverable:** identical-looking app, on rails. ✅

### What landed

**New files**
- `src/api/authedFiles.ts` — `useAuthedObjectUrl` / `fetchAuthedObjectUrl` / `downloadAuthed`
- `src/routes/paths.ts` — single source of truth for URLs, titles, nav-active state
- `src/hooks/useDocumentTitle.ts`
- `src/api/authedFiles.test.ts`, `src/routes/paths.test.ts` — 16 tests
- `eslint.config.js`, `.prettierrc.json`, `.prettierignore`, `.stylelintrc.json`, `src/setupTests.ts`
- `.storybook/main.ts`, `.storybook/preview.ts` (a11y addon enabled)
- `public/favicon.svg`, `public/_redirects`
- `.github/workflows/frontend-ci.yml`

**Security (0.4).** All eight `withAccessToken()` call sites removed and the function
deleted. Logos, PDFs, attachments and vendor documents are now fetched with the
bearer header and rendered from opaque `blob:` URLs. The fact-graph SSE stream —
which could not use `EventSource`, since that API cannot send headers — was
rewritten over `fetch` + `ReadableStream`. Two removals of `innerHTML` DOM hacks
came along for free (audit findings T6, and the SettingsPage equivalent).

**Lint policy.** 0 errors / 255 warnings. The warning count is **pinned** in
`package.json` (`--max-warnings=255`), so the accessibility and dead-code debt can
never grow. Each subsequent phase pays some down and lowers the ceiling. Current
debt: 96 static-element-interaction + click-without-keyboard, 36 unlabelled form
controls, 29 `any`, 28 exhaustive-deps, 24 unused vars, 19 `autoFocus`, 13
`alert`/`confirm`.

**Prettier scope.** 44 legacy files predate the formatter; reformatting them here
would bury the diff. `.prettierignore` scopes formatting to code this revamp has
touched, and each phase un-ignores the files it rewrites.

### Carried into later phases (deliberately not done here)

| Item | Where it lands |
|---|---|
| `/bills/:id` deep links — needs modal↔route wiring and close-updates-URL | Phase 2.10 |
| Draft persistence so `/upload/review` survives a refresh | Phase 3.1 |
| Paying down the 255 lint warnings | Phases 2–6 |
| Reformatting the 44 legacy files | As each phase rewrites them |

### ⚠️ One manual step required

Deep links will 404 in production until the SPA rewrite is configured.
Vercel (`vercel.json`) and Netlify-style hosts (`public/_redirects`) are done in
code. **AWS Amplify reads rewrites from the console only** — the exact rule is in
the header comment of `frontend/amplify.yml`.

## E.1b Implementation status — 2026-08-02

| Phase | Status | Notes |
|---|---|---|
| **0 — Foundation** | ✅ **Complete** | Routing, security fix, fonts, favicon, CI |
| **1 — Design system** | ✅ **Complete** | Tokens, 20 primitives, shell, Storybook, 19 tests |
| **2 — Bills** | 🟡 **Frontend fixes done; server work outstanding** | All P0/P1 *bugs* fixed. Pagination + sort need API changes |
| **3 — Review editor** | 🟡 **Two P0s fixed** | Unsaved guard + validation gate. Autosave needs an API |
| **4 — Dashboard / Reports** | 🟡 **P0 fixed** | Misleading metric + truncation. Aggregation needs API |
| **5 — Agent Hub / Vendors** | ✅ **a11y P0s fixed** | Real switches, error surfacing, icon controls |
| **6 — A11y / print** | 🟡 **Partial** | Focus rings, reduced motion, print sheet, contrast |
| **7 — Enterprise** | 🟡 **Partial** | i18n scaffolding, landing fixes. SSO/VPAT outstanding |

**Verification:** `npm run verify` and `npm run build` both exit 0. 43 tests pass.
Lint: 0 errors, 249 warnings (ceiling ratcheted 255 → 249).

### Blocked on backend work

These cannot be finished in the frontend and are the top of the next sprint:

| # | Item | Needs |
|---|---|---|
| 2.1 | Bills server-side pagination + sort + filter | `GET /api/bills?page&page_size&sort&dir` |
| 3.1 | Review draft autosave | `PUT /api/drafts/:id` + fetch on load |
| 4.2 | Dashboard/Reports aggregation | `GET /api/analytics/*` returning `GROUP BY` results |
| 5.3 | Settings config audit trail | Change-log table + read endpoint |
| 7.1 | SSO / SAML / OIDC | IdP integration |

Until 2.1 lands, Bills still fetches and renders every row — **the hard scaling
wall in §B.6 is not yet removed.** The `DataTable` primitive it will move onto is
built, tested and ready.

### Deliberately not attempted

- **Full rewrite of the four monolith pages** (BillsPage 4,777 lines, VendorsPage
  3,939, SettingsPage 3,640). Targeted fixes were applied instead; the
  decomposition in §E.3 remains outstanding.
- **Migrating all 194 hex colours to tokens.** The token layer exists and new code
  uses it; the ~700 legacy call sites are replaced page-by-page as each is rewritten.
- **VPAT / formal WCAG conformance report** — requires an external audit.

---

## E.2 Phase 1 — Design system (Weeks 2–3)

**Goal:** one source of visual truth.

### 1.1 Token layer (`src/styles/tokens.css`)

Replace the 194 hex values with a semantic scale. Every colour must pass its intended contrast use.

```css
:root {
  /* ── Brand ─────────────────────────────────────── */
  --brand-900:#00234a; --brand-800:#002a58; /* login/landing navy */
  --brand-700:#003a70; --brand-600:#004080; /* primary action    */
  --brand-500:#0b56a4; --brand-400:#3b82c4;
  --brand-100:#e6eefa; --brand-50:#f2f6fd;
  --accent-600:#00696b; --accent-400:#5cd8da;   /* teal, existing brand mark */

  /* ── Neutrals (AA-checked) ─────────────────────── */
  --ink-900:#0b1c30;  /* headings                      */
  --ink-700:#374151;  /* body            10.3:1 on #fff */
  --ink-600:#4b5563;  /* secondary        7.6:1        */
  --ink-500:#5b6473;  /* NEW muted        5.4:1  ← replaces #9ca3af */
  --ink-400:#6b7280;  /* min for labels   4.8:1        */
  --line-300:#d0d7e2; --line-200:#e2e8f0; --line-100:#eef2f7;
  --surface-0:#ffffff; --surface-1:#f8fafc; --canvas:#f5f7fb;  /* ONE canvas */

  /* ── Semantic (AA on white AND on their own tint) ─ */
  --success-700:#046c4e; --success-600:#047857; --success-50:#ecfdf5;
  --warn-700:#92400e;    --warn-600:#b45309;    --warn-50:#fffbeb;
  --danger-700:#991b1b;  --danger-600:#b91c1c;  --danger-50:#fef2f2;
  --info-700:#1d4ed8;    --info-600:#2563eb;    --info-50:#eff6ff;

  /* ── Type scale (8 steps, no 8/9/10.5px) ───────── */
  --fs-2xs:11px; --fs-xs:12px; --fs-sm:13px; --fs-md:14px;
  --fs-lg:16px;  --fs-xl:20px; --fs-2xl:24px; --fs-3xl:30px;
  --lh-tight:1.25; --lh-normal:1.5; --lh-relaxed:1.7;
  --font-sans:'Inter',system-ui,sans-serif;
  --font-display:'Public Sans','Inter',sans-serif;
  --font-mono:ui-monospace,'SF Mono',Menlo,monospace;

  /* ── Space (4px base, 8 steps) ─────────────────── */
  --sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px;
  --sp-5:20px; --sp-6:24px; --sp-8:32px; --sp-10:40px;

  /* ── Radius (4 values only) ────────────────────── */
  --r-sm:4px; --r-md:8px; --r-lg:12px; --r-full:999px;

  /* ── Elevation (4 levels) ──────────────────────── */
  --e-1:0 1px 2px rgba(15,23,42,.06);
  --e-2:0 2px 8px rgba(15,23,42,.08);
  --e-3:0 8px 24px rgba(15,23,42,.10);
  --e-4:0 20px 48px rgba(15,23,42,.16);

  /* ── Z-index (6 named layers, replaces 23) ─────── */
  --z-base:0; --z-sticky:100; --z-dropdown:200;
  --z-overlay:300; --z-modal:400; --z-toast:500;

  /* ── Motion ────────────────────────────────────── */
  --dur-fast:120ms; --dur-base:180ms; --dur-slow:240ms;
  --ease:cubic-bezier(.2,0,.2,1);

  /* ── Focus (replaces 63× outline:none) ─────────── */
  --focus-ring:0 0 0 2px var(--surface-0),0 0 0 4px var(--brand-600);
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration:.01ms !important; animation-iteration-count:1 !important;
    transition-duration:.01ms !important; scroll-behavior:auto !important;
  }
}

:where(a,button,input,select,textarea,[tabindex]):focus-visible {
  outline:none; box-shadow:var(--focus-ring); border-radius:var(--r-sm);
}
```

**Colour migration map** (mechanical find-and-replace, ~700 sites):

| Old | Uses | New | Reason |
|---|---:|---|---|
| `#9ca3af` | 285 | `var(--ink-500)` `#5b6473` | 2.54 → 5.4:1 |
| `#94a3b8` | 23 | `var(--ink-500)` | 2.56 → 5.4:1 |
| `#d1d5db` (as text) | 91 | `var(--ink-400)` | 1.47 → 4.8:1 |
| `#cbd5e1` (as text) | — | `var(--ink-400)` | 1.48 → 4.8:1 |
| `#737781` | 40 | `var(--ink-500)` | 4.48 → 5.4:1 |
| `#10b981` (as text) | 34 | `var(--success-600)` | 2.54 → 4.9:1 |
| `#f59e0b` (as text) | 20 | `var(--warn-600)` | 2.15 → 4.5:1 |
| `#059669` (as text) | 85 | `var(--success-700)` | 3.77 → 5.6:1 |
| `#004080` / `#2563eb` / `#002a58` | 236 | `var(--brand-600)` / `--info-600` | one primary |
| 5 page canvases | — | `var(--canvas)` | one canvas |

### 1.2 Primitive library (`src/ui/`)

Build these 18 components in Storybook, each with keyboard support, ARIA, loading/disabled/error states, and a contrast test:

`Button` (primary/secondary/ghost/danger × sm/md/lg) · `IconButton` (requires `aria-label`) · `Input` · `Select` · `Combobox` · `DatePicker` · `DateRangePicker` · `Checkbox` · `Radio` · **`Switch`** (real `role="switch"`) · `Textarea` · `Badge`/`StatusPill` · `Tooltip` · `Popover` · **`Modal`** (focus trap + restore + Escape + `role="dialog"`) · **`ConfirmDialog`** (typed confirmation for destructive acts) · **`Toast`** (`aria-live="polite"`, one provider) · `Skeleton` · `EmptyState` · `ErrorState` · **`DataTable`** (sort, virtualise, sticky header + first column, column chooser, density, row selection) · `PageHeader` · `Card` · `Tabs` · `Stepper` · `Money` (locale + currency aware) · `DateTime`.

### 1.3 Layout shell

- `AppShell` owning sidebar width, TopBar height, and canvas — remove the magic `marginLeft: 56` and `calc(100vh - 54px)`.
- Responsive breakpoints: `sm 640 / md 768 / lg 1024 / xl 1280 / 2xl 1536`.
- Sidebar: grouped nav, **pin/unpin toggle persisted to `localStorage`**, badge counts, keyboard navigable, real Help link.

**Acceptance:** Storybook published; automated contrast test passes for every token pair; `grep -c "<style>{" src/` ≤ 1.

## E.3 Phase 2 — Bills page rebuild (Weeks 4–5)

Highest user-time, highest risk. Rebuild on the `DataTable` primitive.

| # | Task | Acceptance criteria |
|---|---|---|
| 2.1 | **Server-side pagination + sort + filter.** Extend `GET /api/bills` with `page`, `page_size`, `sort`, `dir`, and the filter set. Update `fetchBills`. | 25,000-bill tenant: first paint < 1.5s; filter response < 400ms |
| 2.2 | **Virtualised rows** (`@tanstack/react-virtual`) as a safety net for large page sizes | ≤ 60 rows in DOM regardless of page size |
| 2.3 | **Sortable columns** — all 15, with `aria-sort` | Sort by amount/due date/vendor/status works and is announced |
| 2.4 | **Column chooser + density toggle + saved views** (persisted per user) | User can save "My overdue >₹10L" and return to it |
| 2.5 | **Filter chips** — every active filter is a removable chip in the toolbar | User can see and remove filters without opening the panel |
| 2.6 | **Reconcile tabs vs status filter.** Tabs become scopes (All / Needs my action / I uploaded / Archived); status becomes a pure filter | No combination silently returns 0 rows without an explanation |
| 2.7 | **Fix the header count bug** — count must reflect the displayed set | Deleted tab header matches the row count |
| 2.8 | **Replace `window.confirm` with `ConfirmDialog`** listing affected bills, total ₹ value, and requiring typed confirmation for delete | Bulk delete shows what and how much |
| 2.9 | **Real XLSX export** via the already-installed `xlsx` package; make the Excel/CSV menu items actually differ; add the column picker | Excel opens without a format warning |
| 2.10 | **Bill modal → route** (`/bills/:id`), `role="dialog"`, focus trap, Escape, ≤180ms fade-scale from 0.96 | Deep link to a bill works; Back closes it |
| 2.11 | Raise approver name/role text from 8px/9px to `--fs-2xs` (11px) minimum | No text below 11px anywhere |
| 2.12 | Replace all 8 emoji controls with Lucide icons + labels | `grep` for the emoji set returns 0 in Bills |
| 2.13 | Add a dual-encoded FX signal (icon + colour + text), not colour alone | Passes a greyscale screenshot test |

**Split `BillsPage.tsx` into:** `BillsPage`, `BillsToolbar`, `BillsFilters`, `BillsTable`, `BillDetailModal`, `ApprovalStepper`, `ReconciliationPanel`, `TdsCell`, `PaymentCell`, `PriorityCell`, `BulkActionBar`, `ExportDialog`. Target: no file > 400 lines.

## E.4 Phase 3 — Review editor (Week 6)

The accuracy-critical screen.

| # | Task | Acceptance criteria |
|---|---|---|
| 3.1 | **Draft autosave** to the server every 5s + on blur; restore on return | Refresh mid-review loses nothing |
| 3.2 | **`beforeunload` + in-app navigation guard** | Leaving with unsaved edits always prompts |
| 3.3 | **Validation gate on Send for Approval** — required fields (vendor, amount, date, category), GSTIN checksum, subtotal + tax = total; inline errors; a summary panel; button disabled until clean | An invalid bill cannot enter the approval chain |
| 3.4 | **AI-provenance UI** — every model-populated field carries an "AI" chip + confidence + "revert to original"; a header banner states what was auto-filled | An auditor can see which values a human confirmed |
| 3.5 | **Surface AI failures** — replace the 4 silent `catch {}` blocks with an inline "Auto-fill unavailable — retry" state | User can distinguish "nothing found" from "it broke" |
| 3.6 | **Field ↔ document linking** — clicking a field highlights the source region in the PDF | Review time per bill drops measurably |
| 3.7 | **PDF viewer upgrade** — continuous scroll, fit-width/page, rotate, text search, keyboard paging | |
| 3.8 | **Keyboard grid** on line items — Tab/Shift-Tab/Enter/arrows, copy-down, bulk row ops | 60-line invoice editable without the mouse |
| 3.9 | **Responsive**: stack form-over-document below 1024px with a toggle | Usable on a 13" laptop and a tablet |
| 3.10 | **Bulk-send preview** — a dialog listing every queued bill with its validation status and total value | No blind bulk submission |

## E.5 Phase 4 — Dashboard & Reports (Week 7)

| # | Task | Acceptance criteria |
|---|---|---|
| 4.1 | **Fix the misleading "Daily Spend" card** — either title it "Today's Spend" or show the 30-day total. Add an explicit period label to every metric | No metric's label can be misread as a different period |
| 4.2 | **Server-side aggregation endpoints** (`/api/analytics/*`) — move all 20+ client `useMemo` aggregates to SQL | Dashboard loads in < 1s at 25k bills |
| 4.3 | **Global date-range control** (presets: This month, Last month, This quarter, FY-to-date, Custom) applied across Dashboard + Reports | CFO can ask "last quarter" |
| 4.4 | **Brand / department / cost-centre segmentation** on both pages | |
| 4.5 | **Flatten the gradient KPI cards** to a single-surface treatment with one clear primary metric | Visual hierarchy exists |
| 4.6 | **Remove the top-6 truncation** in Reports; add "show all" + drill-through to the filtered Bills list | No silent data truncation |
| 4.7 | **Accessible charts** — AA-contrast axes, colour-blind-safe palette, keyboard-reachable data points, and a toggleable data table for every chart | Passes a screen-reader pass |
| 4.8 | Scheduled/emailed reports + PDF export | |

## E.6 Phase 5 — Vendors, Settings, Agent Hub, Integrations (Week 8)

| # | Task | Acceptance criteria |
|---|---|---|
| 5.1 | Vendors → `DataTable` (pagination, sort, filters, bulk ops). Replace `🗑` with `IconButton` + `ConfirmDialog` (typed confirm for bank-account deletion) | |
| 5.2 | Vendor import: full error report with row numbers, downloadable, partial-retry | 2,000-row import is debuggable |
| 5.3 | Settings → nested routes (`/settings/tds`), a global **unsaved-changes bar** (Save / Discard), section search, and a **config audit trail** ("changed by X on Y") | Deep links work; no silent config loss |
| 5.4 | Replace the permission-save `window.confirm` with a diff-showing `ConfirmDialog` | Reviewer sees exactly what changes |
| 5.5 | Agent Hub → real `Switch` primitives; error toast on optimistic-rollback; pause polling in background tabs; filterable + paginated + exportable activity log | Agent can be toggled by keyboard; failures are visible |
| 5.6 | Promote `IntegrationsPage.readError()` to `src/api/errors.ts` and adopt it everywhere; route all `fetch` through the authenticated wrapper | One error vocabulary app-wide |
| 5.7 | Feature-flag `Nova` per tenant like `sap_matching` already is | Non-Nova tenants don't see it |
| 5.8 | Move `PaymentGatewayPage` to a real route | Money movement has a URL and history |

## E.7 Phase 6 — Accessibility, responsiveness, polish (Week 9)

| # | Task | Acceptance criteria |
|---|---|---|
| 6.1 | Full keyboard pass on every flow | Login → upload → review → submit → approve → pay, mouse unplugged |
| 6.2 | Screen-reader pass (NVDA + VoiceOver) on the same flows | Every control has a name, role, and state |
| 6.3 | `axe-core` in CI on every page | 0 serious/critical violations |
| 6.4 | Responsive pass at 375 / 768 / 1024 / 1440 / 1920 | No horizontal scroll; Bills becomes a card list on mobile; approve/reject reachable on a phone |
| 6.5 | `@media print` stylesheet for bill detail and reports | Printed bill is clean and complete |
| 6.6 | Skeletons everywhere (replace all "Loading…" strings); consistent empty and error states | Zero layout shift on load |
| 6.7 | Remove all dead controls (Sidebar Help, TopBar Help, Upload "View Details", landing social chips) or wire them | No control does nothing |
| 6.8 | Session UX: idle warning at 55 min, graceful re-auth that preserves work, "session expired" recovery | No silent work loss |
| 6.9 | Fix landing: real Privacy/Terms/Blog pages, `© {new Date().getFullYear()}`, Product≠Integrations, real social links | Passes a DPO review |

## E.8 Phase 7 — Enterprise hardening (Week 10+, parallel track)

| # | Task |
|---|---|
| 7.1 | **SSO / SAML / OIDC** (Google Workspace + Microsoft Entra first) |
| 7.2 | **WCAG 2.2 AA conformance report + VPAT** |
| 7.3 | **i18n scaffolding** — extract strings, currency/locale-aware `Money` and `DateTime` primitives, remove hardcoded `INR` |
| 7.4 | **Mobile approval PWA** — Bills-needing-my-action, bill detail, approve/reject/send-back, push notifications |
| 7.5 | **Config audit trail UI** across all of Settings |
| 7.6 | **Empty-tenant onboarding checklist** with progress |
| 7.7 | **In-app help centre, changelog, and support widget** |
| 7.8 | **Visual regression** (Playwright + Chromatic) on the top 20 screens |
| 7.9 | **Dark mode** (near-free once tokenised) |

## E.9 Sequencing summary

```
W1   Phase 0  Foundation ──────────────► routing, fonts, security fix, CI
W2-3 Phase 1  Design system ───────────► tokens + 25 primitives + shell
W4-5 Phase 2  Bills rebuild ───────────► pagination, sort, a11y modal
W6   Phase 3  Review editor ───────────► autosave, validation, AI provenance
W7   Phase 4  Dashboard + Reports ─────► server aggregates, date range
W8   Phase 5  Vendors/Settings/Agents ─► routes, unsaved-guard, real switches
W9   Phase 6  A11y + responsive ───────► axe clean, mobile, print
W10+ Phase 7  Enterprise hardening ────► SSO, VPAT, i18n, mobile PWA
```

**Critical path:** Phase 1 blocks 2–6. Do not start Phase 2 before the primitives exist.
**Parallelisable:** Phase 7 items 7.1 (SSO) and 7.3 (i18n scaffolding) can start in Week 2 on a separate track.

## E.10 Success metrics

| Metric | Today | Target |
|---|---:|---:|
| Unique hex colours in `src/` | 194 | ≤ 40 (all via tokens) |
| Unique font sizes | 21 | 8 |
| `outline: none` without `:focus-visible` | 63 | 0 |
| `aria-*` attributes | 5 | > 300 |
| axe serious/critical violations | not measured | 0 |
| Media queries | 8 | every page responsive at 5 breakpoints |
| Injected `<style>` blocks | 19 | ≤ 1 |
| Largest component file | 4,777 lines | ≤ 400 |
| Bills first paint @ 25k bills | crash | < 1.5s |
| Bill modal open latency | 650ms | ≤ 180ms |
| Dead/no-op controls | ≥ 8 | 0 |
| Text below 11px | many (8px, 9px, 10px) | 0 |
| Test coverage on `src/ui` | 0% | ≥ 80% |

---

# PART F — Appendices

## F.1 Contrast quick-reference (post-fix targets)

| Use | Token | On | Ratio |
|---|---|---|---:|
| Body text | `--ink-700` `#374151` | `--surface-0` | 10.3 |
| Secondary text | `--ink-600` `#4b5563` | `--surface-0` | 7.6 |
| Muted / labels | `--ink-500` `#5b6473` | `--surface-0` | 5.4 |
| Minimum muted | `--ink-400` `#6b7280` | `--surface-0` | 4.8 |
| Primary link/action | `--info-600` `#2563eb` | `--surface-0` | 5.2 |
| Brand on white | `--brand-600` `#004080` | `--surface-0` | 10.3 |
| White on brand | `--surface-0` | `--brand-600` | 10.3 |
| Success text | `--success-700` `#046c4e` | `--surface-0` | 5.6 |
| Warning text | `--warn-600` `#b45309` | `--surface-0` | 4.9 |
| Danger text | `--danger-600` `#b91c1c` | `--surface-0` | 6.4 |
| Success badge | `--success-700` | `--success-50` | 5.4 |
| Warning badge | `--warn-700` | `--warn-50` | 6.9 |

**Rule:** never use a `-400` or `-500` semantic colour as text on white. Use `-600`/`-700` for text, `-400`/`-500` for fills and icons ≥ 24px, `-50` for tinted backgrounds.

## F.2 Component definition-of-done

A primitive is done only when all 12 are true:

1. Storybook story with every variant and state
2. Keyboard-operable end-to-end; visible `:focus-visible` ring
3. Correct role, accessible name, and state exposed to AT
4. Contrast verified for every colour pair it can render
5. Loading, disabled, error, and empty states
6. Responsive from 320px to 2560px
7. Honours `prefers-reduced-motion`
8. Zero hardcoded hex, px font size, or z-index — tokens only
9. Unit test for behaviour + interaction test for keyboard
10. TypeScript props documented, no `any`
11. RTL-safe (logical properties: `margin-inline`, not `margin-left`)
12. Works with 200% browser zoom without clipping

## F.3 Per-page QA checklist (run before every page is signed off)

- [ ] Reachable and shareable by URL; browser Back works
- [ ] Keyboard-only: every action performable, focus order logical, no traps
- [ ] Screen reader: page title, landmarks, headings h1→h2→h3 in order
- [ ] axe: 0 serious/critical
- [ ] 375 / 768 / 1024 / 1440 / 1920 — no horizontal scroll
- [ ] 200% zoom — no clipping or overlap
- [ ] Loading, empty, error, and no-permission states all designed
- [ ] Every destructive action uses `ConfirmDialog` with consequences stated
- [ ] Unsaved changes are guarded
- [ ] No text below 11px; no colour below its AA threshold
- [ ] No emoji as a functional control
- [ ] No dead controls
- [ ] Greyscale screenshot: all state still distinguishable
- [ ] Tested with 0 rows, 1 row, and 10,000 rows

## F.4 Findings index

| Severity | Count | Where concentrated |
|---|---:|---|
| **P0 — Blocker** | **17** | Design system, contrast, a11y, routing, scale, security, Bills, Review, Dashboard, Agent Hub, Landing, Login |
| **P1 — Critical** | **48** | Every page |
| **P2 — Major** | **41** | Every page |
| **P3 — Polish** | **11** | Landing, Login, Upload, Dashboard, Bills, Done |
| **Total** | **117** | |

## F.5 What is genuinely good — keep and propagate

Not everything needs replacing. These are above the market bar and should be preserved and reused:

1. **`IntegrationsPage.readError()`** (`:26-36`) — the best error handling in the codebase. Promote to `src/api/errors.ts`.
2. **The approval stepper's send-back visualisation** (`BillsPage.tsx:251-283`) — dashed orange bi-directional connectors for a skipped-and-re-approved segment is a genuinely elegant solution to a hard state-visualisation problem.
3. **GSTIN checksum validation with inline warning + valid badge** (`utils/gstin.ts`, `BillsPage.tsx:410-449`) — real domain value, well presented. It just needs to *block* submission.
4. **The Nova override dialog** (`BillsPage.tsx:726-796`) — requires a typed reason, shows the agent's attached facts, and states plainly that it saves as an "override, not reconciled". This is exemplary audit-aware UX. **Make this the template for every destructive/exceptional action in the product.**
5. **The bill activity timeline with expandable field-level diffs** (`BillsPage.tsx:1189-1264`) — extend this pattern to Settings and Vendors (see E5).
6. **PAN-duplicate merge prompt after a GSTIN correction** (`BillsPage.tsx:1009-1014, 1131-1157`) — proactive data-quality intervention at exactly the right moment.
7. **Stale-while-revalidate caching** in `fetchBills` (`api/invoices.ts:920-927`) — correct instinct; keep it and layer pagination on top.
8. **`DonePage`'s token usage** (`:30-43`) — the proof that the token layer works. Use it as the migration reference.

---

*End of audit. 117 findings across 15 pages, 25,600 lines. Recommended next action: create the Phase 0 branch tasks and do not restyle a single page until Phase 1 tokens land.*
