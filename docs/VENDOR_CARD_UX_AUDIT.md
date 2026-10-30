# Vendor card: UI/UX audit

**Date:** 2026-10-22 · **Branch:** `uat2` (including the uncommitted Documents/Bills cleanup) · **Method:** a deliberately sceptical code review of every tab of the vendor card. Each finding was checked against the source. Items marked *(guess)* are inferred from the code, not seen in a browser.

Paths are relative to `frontend/src`. Line numbers are as of this audit.

## Summary

| Severity | Count | Themes |
|---|---|---|
| **High** | 15 | Failed loads shown as "nothing", money in the wrong currency, emails sent twice or to the wrong thread, unguarded bank changes, silent save failures |
| **Medium** | 39 | Numbers that contradict each other, data lost without warning, dialogs and keyboard access, mobile layout |
| **Low** | 22 | Copy, plurals, ordering, small visual inconsistencies |

The **top 10 to fix first** are at the end. Four findings come from this session's own changes and are marked 🔁: O-M1, B-M5, B-L2, D-L2.

## Status (updated 2026-10-25)

**Fixed:** everything below, except the items listed here. Fixes landed on `uat2` in `c655d6b`, with the follow-ups after it. They're covered by the typecheck and unit tests but haven't been checked in a browser.

| ID | Status | Note |
|---|---|---|
| S-L4 | **Not fixed (blocked)** | Bill upload has no vendor field; the vendor is read from the bill. Pre-filling it needs backend and review-flow changes. "+ Bill" has a tooltip saying so. |
| O-M3, O-M4 | **No longer applies** | The Copilot banner was removed from the Overview. |
| S-M3 | Fixed | At 960px and below, the sidebar renders between the header and the tabs, with its sections folded by default. |
| B-L5 | Fixed | A document delete waits 5 seconds with an Undo before it's sent; closing the card sends anything still waiting. |
| K-L2 | Fixed | The GST section is rebuilt on the card's design tokens. The bank section still uses the older `vd__` classes, but without hard-coded colours. |
| P-M4 | Fixed | The upload and move dialogs in Documents now lock page scroll, trap Tab and return focus. |
| C-L2 | Fixed | Email menus support typeahead as well as Escape, click-outside and arrow keys. |
| C-L3 | Fixed, not checked on a device | Menus slide back inside a 16px screen gutter after opening. |
| Extra | Fixed | A bill attachment that finishes after you've switched conversations is no longer added to the new one. |

### Patterns that cause most of the bugs

1. **`.catch(() => setX([]))` everywhere.** A failed request looks exactly like "no data". Users see "No bank account", "₹0 outstanding", "No contracts" or "Connect your mailbox" when the real problem is a network error. This one pattern accounts for about 8 findings.
2. **No confirmation before destructive or irreversible actions.** Changing the primary bank account, deleting overrides, the TDS catch-up decision, closing with unsaved edits, Forward wiping a draft, and AI overwriting typed text all happen on one click.
3. **Two definitions of the same idea.** "Overdue", "monthly average" and "last billed" are each computed differently in different places, so the screen contradicts itself.
4. **Div-buttons and hover-only actions.** Many clickable elements can't be reached by keyboard, and some actions are invisible on touch screens.

---

## 1. Card shell, header and tabs

### High
- **S-H1 · Closing the card loses unsaved edits, and it isn't a real modal.** `pages/VendorsPage.tsx:2375-2376`. A click on the backdrop or ✕ while editing Vendor details discards the edits without asking. Escape does nothing. There is no focus trap even though the card declares `aria-modal`. **Fix:** confirm when there are unsaved changes, add a guarded Escape, and move focus into the card and trap it there.

### Medium
- **S-M1 · Every tab click adds a browser history entry.** `VendorsPage.tsx:3146, 2304`. After visiting five tabs it takes five Backs to close the card, although the code comment says "Back closes the panel". **Fix:** `navigate(…, { replace: true })` for tab changes.
- **S-M2 · The active-tab underline drifts.** `VendorsPage.tsx:2293-2301`. The tabs get wider once the Documents and Conversations counts load, but the underline is only measured when `view` changes or the window resizes. **Fix:** a `ResizeObserver` on the tab list.
- **S-M3 · At 960px and below, the sidebar ends up under everything, and KPI jumps don't scroll.** `vendorCard.css:2806-2820`, `VendorsPage.tsx:2306`.
  - Vendor details, People and Bank sit below all the tab content.
  - `goView` scrolls `mainRef`, which stops being the scroll container at this width.
  - **Fix:** scroll the real scroll container, or use `scrollIntoView` on the tabs. Consider putting the sidebar above the tabs on mobile.
- **S-M4 · Toasts cut each other short, and errors look like info.** `VendorsPage.tsx:1850-1853, 2941-2945`.
  - A second toast clears the first one's timer early.
  - Errors use the same icon as info and `role="status"`.
  - **Fix:** keep and clear the timeout id. Errors get `role="alert"` and a distinct icon.
- **S-M5 · Tabs are missing the tab pattern.** `VendorsPage.tsx:2428`. There is no `aria-controls` or `tabpanel` and no arrow-key navigation.

### Low
- **S-L1 · Long names or emails may crush the header** *(guess)*. `vendorCard.css:518, 568`. `.vc__hero-acts` won't shrink and only wraps at 640px and below. `.vc__hero-sub` has no `overflow-wrap`.
- **S-L2 · Raw backend status in the header.** `VendorsPage.tsx:2392, 2409`. "PENDING" and "FAILED" are shown as-is. "Since" is when the vendor was added to Onsumm, not when the relationship started.
- **S-L3 · Deep link to a vendor that isn't loaded shows nothing.** `VendorsPage.tsx:2994-2996, 3136`. There is no loading or "not found" state.
- **S-L4 · Duplicate actions.**
  - "Pay vendor" appears in up to three places.
  - New senders appear in both Copilot and People.
  - "+ Bill" and "+ Contract" leave the card for the generic upload page without the vendor filled in (`:2449`).

---

## 2. Overview, KPIs and Copilot

### High
- **O-H1 · A failed load reads as real zeroes.** `VendorsPage.tsx:2263, 2044, 2106, 2164`.
  - Failed bills fetch: "₹0 outstanding · Nothing due".
  - Failed bank fetch: "No bank account on record", plus a Copilot "No verified bank account" nudge.
  - Failed inbox fetch: "Connect your mailbox", even when it is connected.
  - **Fix:** an error state per source, "Couldn't load · Retry", and suppress the KPIs and nudges that depend on that source.

### Medium
- **O-M1 🔁 · "Overdue" means two things.** `vendorStats.ts:231, 264`, `VendorViews.tsx:185, 302-316`.
  - The Bills table now says "In approval · 40d late" (amber).
  - The KPI strip still counts that bill as "1 overdue" (red).
  - Copilot shows "Payment risk" with **Pay vendor** for a bill that can't be paid yet.
  - "Awaiting approval" leaves out late unapproved bills.
  - **Fix:** split the late bills into approved-overdue and late-in-approval, and only offer Pay for approved-overdue.
- **O-M2 · New vendors show misleading averages.** `vendorStats.ts:277`, `VendorViews.tsx:197, 455`.
  - A vendor whose first bill is this month shows "₹0 avg / month".
  - The fallback line uses the all-time bill count under a "last 12 months" heading.
  - **Fix:** show "First bills this month" instead of ₹0, and count only the last 12 months.
- **O-M3 · The "PAN is missing" nudge sends users to a place where PAN can't be added.** `VendorViews.tsx:345-351`. The TDS section has no PAN field. PAN can only be edited in the sidebar, by admins and finance controllers.
- **O-M4 · Copilot isn't announced and dismissals don't stick.** `VendorViews.tsx:253-285`. There is no live region. Dismissed nudges come back each time the card opens.

### Low
- **O-L1 · The Outstanding bar legend explains 2 of its 4 colours.** `VendorViews.tsx:182-186`. "Due this week" here is "Due in 7 days" in the schedule and leaves out unapproved bills.
- **O-L2 · The paid-bills footer always shows**, even as "0 paid bills ₹0 · Show". `VendorViews.tsx:380-386`. "Paid in N days" uses `payment_updated_at`, which records when the payment status was last edited, not the date of payment.

---

## 3. Sidebar: Vendor details, People, Bank account

### High
- **D-H1 · A payment term of 0 days saves as 30.** `VendorsPage.tsx:1976, 1986`. `parseInt(...) || 30` treats 0 as missing. **Fix:** `Number.isFinite(n) ? n : 30`, and a validation error when the field is blank.
- **D-H2 · An unverified bank account looks almost the same as a verified one.** `VendorsPage.tsx:2917`, `vendorCard.css:1793`. Both use the same translucent pill on the dark card, and the tick is the only difference. This is risky right before a payment. **Fix:** an amber `vc__vchip--warn` with an alert icon.

### Medium
- **D-M1 · Legal name can't be set, and renaming silently writes one.** `VendorsPage.tsx:2829, 1955, 1974`. The Legal name row is hidden when it equals the brand name. Renaming "Acme" to "Acme Corp" sets the legal name to "Acme" without anyone typing it. **Fix:** always show the field while editing, and track whether the user changed it.
- **D-M2 · Contact and copy actions are invisible on touch screens.** `vendorCard.css` `.vc__person-acts` (~1717) and `.vc__copy` (~1670). They stay at `opacity: 0` with no `@media (hover: none)` override. Other controls on the card already have one.
- **D-M3 · The bank card is a div-button with a real button inside it.** `VendorsPage.tsx:2914`. Nesting interactive elements this way breaks keyboard and screen-reader behaviour.

### Low
- **D-L1 · Vague save errors, and PAN can't be cleared.** `VendorsPage.tsx:1996, 1977`. The error shows axios's "Request failed with status code 422" instead of the server's `detail`. Clearing PAN sends `undefined`, so the old value stays.
- **D-L2 🔁 · Clicking a section title while editing collapses it later.** `VendorViews.tsx` `SideSection`. While `forceOpen` holds the section open, the toggle still saves "folded" to localStorage, so the section snaps shut after Save. **Fix:** do nothing while the section is forced open.

---

## 4. Documents (Bills, Contracts, Recent, custom types)

### High
- **B-H1 · Foreign-currency bills are shown and summed as ₹.** `vendorStats.ts:26`. `billAmount` falls back to `total_amount` when no INR value has been computed, and `money()` always prints ₹. A USD 5,000 bill shows as ₹5,000 in the table, the summary, the spend chart and the forecast. **Fix:** leave unconverted bills out of the totals and show them as "USD 5,000 · not converted".
- **B-H2 · Loading or a failed fetch shows "No bills" or "No contracts".** `VendorsPage.tsx:2480, 2009`, `VendorDocuments.tsx:88`, `VendorViews.tsx:484`. `stmtLoading` is never passed down, and errors become `[]`. **Fix:** pass loading and error flags, and render a skeleton or a Retry message.
- **B-H3 · "Upload contract", "Add new" in a type, and "New type" all ignore where they were clicked.** `VendorDocuments.tsx:83, 113`. `upload.preset` is stored but never passed to `UploadDialog`, so an MSA uploaded from Contracts can be filed under "Rate cards" by the AI. **Fix:** pass the preset as the starting filing, and let classification only suggest.

### Medium
- **B-M1 · Search doesn't match amounts as displayed.** `VendorDocuments.tsx:120`. Searching "1,25,000" or "₹1,25,000" finds nothing. **Fix:** strip ₹ and commas from both the query and the amount.
- **B-M2 · Bill rows can't be reached by keyboard.** `VendorDocuments.tsx:165`. A `<tr onClick>` has no tabIndex or key handler. "Latest bills" in Recent can't be opened at all. **Fix:** make the bill number a real link or button.
- **B-M3 · A filename guess is shown as a confident AI answer.** `VendorDocuments.tsx:521-528, 568, 612`. When the file can't be read, the guess has no `sure: false`, and there is no "I'll choose" option during a slow read.
- **B-M4 · No file type or size checks.** `VendorDocuments.tsx:561, 329`. There is no `accept` and no size cap. A 200 MB file is sent to classify and then uploaded a second time. Dropping several files silently keeps only the first.
- **B-M5 🔁 · The filter names disagree with the status column.** `VendorDocuments.tsx:102`.
  - "Overdue" includes unapproved bills that the status column shows as "In approval · Nd late".
  - "In approval" also counts "Needs review" bills.
  - **Fix:** define the filters from `billState`.
- **B-M6 · A type left empty stays selected.** `VendorDocuments.tsx:290, 275`. When the last file in a type is moved or deleted, its nav item disappears but the body still shows that type.
- **B-M7 · Type names that differ only by case create duplicate folders** *(guess on backend)*. `VendorDocuments.tsx:541, 606, 92`.
- **B-M8 · Narrow screens hide custom types.** `vendorCard.css:2829-2836`. The folder row scrolls sideways with its scrollbar hidden, so types off-screen have no visible cue.

### Low
- **B-L1 · Dates have no year.** `VendorDocuments.tsx:168, 170`. Last year's bills read "Due 3 Mar".
- **B-L2 🔁 · The Outstanding column comes and goes with the filter.** `VendorDocuments.tsx:128`. `partPaid` is worked out from the filtered rows.
- **B-L3 · Rows inside a month group follow API order**, not bill date. `VendorDocuments.tsx:226`.
- **B-L4 · CSV export details.** `VendorDocuments.tsx:159`. The download link is revoked immediately after the click, which can cancel the download in Safari *(guess)*. Raw codes such as `PENDING_L1` are exported.
- **B-L5 · Smaller dialog details.**
  - The Move dialog's ✕ stays enabled while it's busy (`:664`).
  - Space on `TypeOption` scrolls the page (`:371`).
  - Delete uses `window.confirm` with no undo (`VendorsPage.tsx:2027`).

---

## 5. Document preview and claims

### Medium
- **P-M1 · Preview can spin forever.** `VendorsPage.tsx:932-944`. On a network error, `.catch(() => {})` leaves "Fetching…" up for good. `r.ok` is never checked. **Fix:** an error state with Retry and Download.
- **P-M2 · Escape or a backdrop click discards claim edits.** `VendorsPage.tsx:916-920, 951`.
- **P-M3 · "Approve (5)" applies fewer than 5.** `VendorsPage.tsx:872, 893`. The count includes conflicted claims that are then skipped, and the result is reported as an error. **Fix:** count only the claims that will apply.
- **P-M4 · Dialog accessibility.** `VendorDocuments.tsx:406-467`, `VendorsPage.tsx:1101-1104, 1250`.
  - There is no focus trap, focus isn't returned on close, and the page behind still scrolls.
  - The preview has no `role="dialog"`.
  - The zoom and page buttons have no labels.
  - `AttachBillPicker` has no Escape.
- **P-M5 · The contract preview is side by side even on phones** *(guess)*. `VendorsPage.tsx:1022, 1080`.

---

## 6. Spend

### Medium
- **SP-M1 · The forecast says "No bills since June" when there's a bill this month.** `vendorStats.ts:177`. `lastBilled` ignores the current month.
- **SP-M2 · Two different "Monthly average" figures.** `SpendChart.tsx:83` vs `vendorStats.ts:277`. The dashed line changes with 6M or 24M, while the headline doesn't.
- **SP-M3 · The forecast claims a track record it doesn't have.** `vendorStats.ts:186-190`, `VendorViews.tsx:543`. With 3–5 months of data, "Past forecasts landed within ±X%" is really the band width, not a backtest.
- **SP-M4 · The chart works by mouse only.** `SpendChart.tsx:103, 125-127`. There is no keyboard or touch handling, and the `role="img"` label describes no data. "Click a bar" still shows in Table view.

### Low
- **SP-L1 · Axis labels round badly.** `vendorStats.ts:294`. Steps of ₹2.5K read "₹3K / ₹5K / ₹8K". The tooltip range can go negative (`SpendChart.tsx:182`).
- **SP-L2 · "Billing rhythm" uses API order**, not the latest bills by date. Plurals read "1 bills". `VendorViews.tsx:482, 535, 595`.

---

## 7. Conversations

### High
- **C-H1 · Reply text leaks into the next thread.** `components/vendor/VendorInbox.tsx:232, 251`. `replyDraft()` keeps `body: draft.body`. If you type in thread A and open thread B, B's composer has A's text, and autosave stores it under B. **Fix:** start with an empty body when the thread changes.
- **C-H2 · Attachments follow you to other threads.** `VendorsPage.tsx:2150, 2154`. If you attach invoices in A and send from B, B's recipients get them. **Fix:** keep attachments per thread, or clear them, with a notice, when the thread changes.
- **C-H3 · Cmd/Ctrl+Enter skips the send guards.** `VendorInbox.tsx:837, 337`. The shortcut calls `send()`, which never checks `sending` or `typing`. Pressing it twice sends twice. Pressing it mid-AI sends a half-written draft. **Fix:** return early in `send()` when `sending || typing`.
- **C-H4 · "Write with AI" overwrites typed text with no warning or undo.** `VendorInbox.tsx:306, 315`. **Fix:** add the AI text below, or confirm the replace, and keep one undo step.

### Medium
- **C-M1 · A failed inbox load looks like an empty mailbox.** `VendorsPage.tsx:2164`, `VendorInbox.tsx:420`. **Fix:** set `inbox.error` so the existing warning bar shows.
- **C-M2 · An attachment shows as "filed" even when saving it failed.** `VendorInbox.tsx:515-521, 685`. The parent catches the error, so the tile marks itself saved.
- **C-M3 · AI suggestions can state wrong facts.** `aiIntents.ts:29-31, 84`, `VendorInbox.tsx:600`.
  - The "paid on" date is `payment_updated_at`, which is when the payment status was last edited.
  - The amount is the gross bill amount, not what was paid after TDS.
  - Bill numbers are matched as substrings, so "1234" matches "12345" or a phone number.
- **C-M4 · Forward wipes the draft and its attachments, and the original's files load with no progress shown.** `VendorInbox.tsx:261-279`. Send stays enabled before they finish arriving.
- **C-M5 · A send that finishes after you've switched threads resets the new thread's recipients.** `VendorInbox.tsx:368`.
- **C-M6 · An invalid address is silently dropped.** `VendorInbox.tsx:728-730, 339`. `ravi@vendor` only turns red, and Send goes ahead to the others. The "Name or email" placeholder suggests names work, but only emails are suggested.

### Low
- **C-L1 · A summary error is shown as if it were the summary.** `VendorInbox.tsx:285`.
- **C-L2 · Menus don't close on Escape or on a click outside, and `role="menu"` has no arrow keys.** `VendorInbox.tsx:701, 877`.
- **C-L3 · At 640px and below, action buttons become unlabelled icons.** `vendorCard.css:3367, 4076`. There are no `title`s. `.vc__menu--wide` may overflow a 360px screen *(guess)*.
- **C-L4 · The selected filter chip and open thread aren't announced.** `VendorInbox.tsx:410, 569`. There is no `aria-pressed` or `aria-current`.

---

## 8. Compliance (GST, bank, TDS)

### High
- **K-H1 · Anyone can add or delete bank accounts, or change the primary, with no confirmation.** `VendorsPage.tsx:2635, 2666, 2669, 2213`. GSTIN changes are gated by `canEdit`, but bank changes aren't. A primary change redirects payments. **Fix:** gate by `canEdit` and confirm with the masked old and new accounts.
- **K-H2 · The full account number is shown in plain text.** `VendorsPage.tsx:2680`. The header (`:2655`) and the sidebar (`:2927`) both mask it. **Fix:** mask it here too, with the same Show toggle.
- **K-H3 · "Re-verification queued" shows even when the request failed.** `VendorsPage.tsx:1336-1338`. `fetch` doesn't throw on a 4xx or 5xx, `catch {}` swallows network errors, and nothing refreshes the status. **Fix:** check `res.ok`, show the error, and refresh the account.
- **K-H4 · A failed TDS category change is silent, and the control isn't role-gated.** `VendorsPage.tsx:1421-1424, 2793-2798`. The dropdown keeps the new value while the header shows the old one. **Fix:** roll back and show an error, show saving and saved states, and gate by `canEdit`.

### Medium
- **K-M1 · The irreversible TDS catch-up decision has no confirmation and no error handling.** `VendorsPage.tsx:1522, 1526`. Clicking twice submits twice.
- **K-M2 · Deleting an override has no confirmation, and "+ Add override" silently replaces the existing one.** `VendorsPage.tsx:1667, 1505`. **Fix:** show "Edit override" when one exists.
- **K-M3 · The override form has no validation and can be submitted twice.** `VendorsPage.tsx:1426-1445, 1585, 1623`. It accepts rates below 0 or above 100, and an end date before the start date. Errors are a generic "Save failed".
- **K-M4 · A failed override load reads as "No override — using category default".** `VendorsPage.tsx:1415, 1631`. That is a false statement about how tax is being deducted.

### Low
- **K-L1 · The "Set as primary" toggle can't be used by keyboard.** `VendorsPage.tsx:2744`. It is a `div` inside a `label` with no input.
- **K-L2 · GST section rough edges.** `VendorsPage.tsx:1867, 1874, 2530-2541, 1920`.
  - The FAILED toast reappears on every visit to the tab.
  - GSTIN format isn't validated, and Enter doesn't submit.
  - Statuses show as raw uppercase.
  - Confirmations use `window.confirm`, and the section uses legacy `vd__` styling.
- **K-L3 · The heading year is hardcoded.** `VendorsPage.tsx:1693`. It reads "FY 2026-27" regardless of the data.

---

## Top 10 to fix first

| # | ID | Why first |
|---|---|---|
| 1 | C-H1, C-H2, C-M5 | Wrong content or attachments sent to a vendor. These can't be recalled once sent. |
| 2 | C-H3 | Duplicate emails to vendors. A one-line fix. |
| 3 | K-H1 | Unguarded changes to where payments go. |
| 4 | K-H2 | Full account number exposed, against the card's own masking. |
| 5 | B-H1 | Foreign-currency bills inflate every total. |
| 6 | O-H1, B-H2, C-M1, K-M4 | One pattern: failed loads shown as "nothing". Fix it once with a shared error state. |
| 7 | D-H1 | A 0-day term saves as 30. A one-line fix. |
| 8 | K-H3, K-H4 | Success shown for failed actions in Compliance. |
| 9 | O-M1, B-M5 | "Overdue" defined three different ways. Make `billState` the single source. |
| 10 | S-H1, P-M2, C-H4 | Unsaved work lost without warning. |
