# Review Hub — design brief

*Paste everything below the line into Claude Design. It is written to be self-contained.*

---

## 1. What you are designing

**Onsumm** is an accounts-payable platform used by Indian finance teams. It ingests vendor bills
(OCR from PDFs and email), runs them through a configurable approval chain, verifies vendors against
the GST portal and their bank, reconciles line items against usage data, handles TDS and foreign
exchange, and finally disburses payment.

You are designing the **Review Hub** — a new page at `/review` that is the single command centre for
everything currently wrong across that entire platform, scoped to the logged-in user's permissions.

**The user.** A finance controller or AP accountant at an Indian company. They open this page first
thing in the morning with a coffee. They are not browsing — they are *clearing a queue*. They are
accountable for money going out the door correctly, they are audited, and they are interrupted
constantly. They are fast, keyboard-literate, and deeply sceptical of software that wastes their
time or cries wolf.

**The moment.** 8:40am. 23 things need them. ₹4.2 crore is at risk. Three of those things are
blocking a payment that must go out today. They have about eleven minutes before their first call.

**What lands in the hub.** Findings of every kind, from two sources:
- *Deterministic checks* — duplicate bills, a vendor whose GSTIN verification failed, a bill stalled
  four days at an approval step with no assignee, a failed bank transfer, a line-item reconciliation
  variance, a missing FX rate, a GSTIN on a bill that doesn't match the vendor master, a vendor name
  silently overwritten by the GST portal, a duplicate vendor sharing a bank account with another.
- *An AI agent* that reviews entities open-endedly and can raise problem types nobody wrote a rule
  for. These are explicitly lower-trust and must be **visibly distinguishable** — see §6.

---

## 2. The central design problem — solve this explicitly

Two requirements are in direct tension, and resolving them is the whole assignment:

> **"The user must get all of their issues."** and **"There must be no information overload."**

The resolution I want you to design around:

> **The census is always complete. The presentation is always bounded.**

Counts for every lane, cluster and severity are on screen at all times and never lie. But the page
never *renders* more than about twelve findings without an explicit user act. A user with 400 open
findings sees the number 400 immediately and twelve cards. Nothing is hidden; almost nothing is shown.

The user must never be in a state where they don't know what is being withheld or how to reach it.
The default filter is stated **in words**, on screen, with a one-click escape — something like:

> `Showing 12 of 23 you can act on · 388 more you can see    [Show everything]`

Design that line as a first-class element, not fine print. It is the entire argument made visible.

A second structural rule that shapes everything: **the interface knows *shapes*, never *issue types*.**
A finding type invented by the AI tomorrow must render correctly in today's design. So: no
bespoke layout per problem type. One card anatomy, one drawer anatomy, and a small set of
evidence *shapes* (a before/after diff, a table, a timeline, a field list, plain prose) that any
finding can declare. Design the shapes, not the problems.

---

## 3. Design system — use these exact values

This is a real production system; matching it means the design can be built directly.

**Colour.** Light theme, one page background `#f5f7fb`, cards on `#ffffff`.

```
Brand        900 #00234a · 800 #002a58 · 700 #003a70 · 600 #004080 (primary action)
             500 #0b56a4 · 400 #3b82c4 · 100 #e6eefa · 50 #f2f6fd
Accent       700 #005152 · 600 #00696b (the teal in the logo) · 400 #5cd8da · 50 #effbfb
Ink (text)   900 #0b1c30 headings · 800 #1f2937 · 700 #374151 body · 600 #4b5563 secondary
             500 #5b6473 muted · 400 #67707e (floor for text)
             300 #9ca3af — NON-TEXT ONLY: borders, dividers, decorative rules. Never text.
Lines        300 #d0d7e2 · 200 #e2e8f0 · 100 #eef2f7
Surfaces     0 #ffffff · 1 #f8fafc (hover rows) · 2 #f1f5f9 (table headers) · canvas #f5f7fb
Success      700 #046c4e · 600 #047857 · 500 #10b981 (fills) · 100 #d1fae5 · 50 #ecfdf5
Warn         700 #92400e · 600 #b45309 · 500 #f59e0b (fills) · 100 #fef3c7 · 50 #fffbeb
Danger       700 #991b1b · 600 #b91c1c · 500 #dc2626 · 100 #fee2e2 · 50 #fef2f2
Info         700 #1d4ed8 · 600 #2563eb · 500 #3b82f6 · 100 #dbeafe · 50 #eff6ff
```

Rule of thumb enforced throughout: **-600/-700 for text, -400/-500 for fills and large icons, -50 for
tinted backgrounds.** Never a -400 as text on white.

**Type.** `Inter` for UI, `Public Sans` for display/headings, `ui-monospace` for GSTINs, account
numbers and IDs. Sizes — and there are only eight, nothing smaller than 11px ships:

```
11px · 12px · 13px · 14px (body) · 16px · 20px · 24px · 30px
line-height 1.25 tight / 1.5 normal / 1.7 relaxed
weights 400 / 500 / 600 / 700
```

All currency uses **tabular numerals** so columns align.

**Space.** 4px base: `4 · 8 · 12 · 16 · 20 · 24 · 32 · 40`.
**Radius.** Only four: `4px · 8px · 12px · 999px`.
**Elevation.** `0 1px 2px rgb(15 23 42 / 6%)` · `0 2px 8px / 8%` · `0 8px 24px / 10%` · `0 20px 48px / 16%`.
**Focus ring.** `0 0 0 2px #fff, 0 0 0 4px #004080` — visible on every interactive element.
**Motion.** 120/180/240ms, `cubic-bezier(0.2, 0, 0.2, 1)`. Nothing bouncy, nothing that scales from a
click origin.

**Shell.** A left sidebar (56px collapsed, 216px expanded) and a 54px top bar already exist. Show
them as context in the full-page artboards but do not redesign them — except the notification bell,
which becomes this feature's "peek" surface (artboard 9).

**Existing components to reuse rather than reinvent:** buttons (primary/secondary/ghost/danger/success
× sm/md/lg), badges (neutral/info/success/warn/danger/brand, **every badge carries an icon** so colour
is never the only signal), inputs/selects/checkboxes, modal, confirm dialog (supports a headline
impact row, an itemised list, and a type-to-confirm field), toasts, skeletons, empty states, a data
table with sortable headers and multi-select, filter chips, and an Excel-style column filter dropdown.

**Icons:** Lucide. 16px inline, 20px in headers, 24px in empty states.

---

## 4. Artboards to produce

Lay these out on one canvas, grouped left-to-right in the order below. Desktop is 1440×900 unless
noted.

1. **The hub, loaded and busy** — the hero. 23 findings, 12 rendered, a mix of severities and
   confidence tiers, one cluster card, filters active. This is the artboard everything else supports.
2. **The finding card, anatomy study** — 1200×800. Six cards side by side at large scale showing:
   critical/verified · high/verified · medium/verified · high/AI-suspected · a cluster card · a card
   the user can see but not act on. Annotate the anatomy.
3. **The drawer open** — a finding expanded over the dimmed queue. Show the full vertical order:
   what's wrong → evidence → "36 others like this" escape hatch → the inline fix → deep links out.
4. **Three evidence shapes** — 1200×900. The same drawer region rendered as (a) a before/after diff
   for a GSTIN mismatch, (b) a table for a line-item reconciliation variance, (c) a timeline for a
   stalled approval chain.
5. **Cluster drill-in** — the 37 members of one cluster as a selectable table, with two members
   auto-deselected because they differ, a banner explaining why, and a floating bulk action bar.
6. **The confirm step** — the dialog for applying a fix to 35 bills. Must show the impact numbers,
   the excluded count, an itemised list, and a type-to-confirm field.
7. **Empty and edge states** — 1200×900, four panels: the genuine zero state (design this as an
   *achievement*, not an absence — it needs evidence that a scan ran, evidence of value delivered,
   and an action) · loading skeleton · a failed load · a user whose role sees almost nothing.
8. **Browse view** — the full table of all 400, virtualized, with column filters and bulk select.
   This is the "show me everything" escape from the bounded queue.
9. **The bell** — the top-bar notification dropdown, 380px wide, showing the top five by rank with a
   link into the hub.
10. **Mobile, 390×844** — the queue and one expanded finding. Triage on a phone is a real use case:
    an approver checking whether anything is blocking before they board a flight.

For each artboard, show **real states, not idealised ones.** At least one long vendor name that must
truncate, at least one finding with no money attached, at least one snoozed item, at least one
finding that is 40 days old.

---

## 5. Content — use this data, do not invent lorem ipsum

The realism of the copy *is* the design here. Indian AP, rupees, real formats.

Currency renders compactly: `₹4.20 Cr`, `₹12.4 L`, `₹84,000`. Never `₹0` — if there is no money at
risk, show nothing.

GSTINs are 15 characters, mono, e.g. `29AABCU9603R1ZJ` (the first two digits are the state code —
27 is Maharashtra, 29 is Karnataka, and that distinction is the substance of several findings).

Use these findings verbatim:

| Severity | Confidence | Title (entity-free, ≤80 chars) | Entities | Money | Age |
|---|---|---|---|---|---|
| Blocking | Verified | Bill is four steps into a chain that has no approver at step 3 | Acme Industries Ltd · INV-4471 | ₹12.4 L | 6d |
| Blocking | Verified | Payment blocked — vendor bank account has never been verified | Sundaram Logistics · ₹8.4 L due today | ₹8.4 L | 1d |
| High | Verified | GSTIN on the bill doesn't match the vendor master | *cluster of 37* · Vendor X | ₹4.20 Cr | 12d |
| High | Suspected | Bank account on this bill has never been paid before for this vendor | Nexon Pvt Ltd · INV-9930 | ₹84.0 L | 1d |
| High | Verified | Duplicate bill — same vendor, amount and date as INV-3391 | Kaveri Traders · INV-3402 | ₹2.6 L | 2d |
| High | Verified | Line items total ₹18,400 more than the invoice subtotal | Signzy Technologies · INV-8821 | ₹18,400 | 4d |
| Medium | Verified | Vendor name was silently replaced with the GST portal's legal name | Bharat Freight Carriers | — | 9d |
| Medium | Verified | Two vendors share the same bank account and IFSC | Deccan Supplies / Deccan Supply Co | — | 21d |
| Medium | Verified | No FX rate on file for the invoice date — bill parked | Cloudflare Inc · USD 4,200 | ₹3.5 L | 3d |
| Medium | Suspected | This vendor's per-unit rate rose 41% against its own six-month average | Orbit Facility Services | ₹1.1 L | 1d |
| Medium | Verified | CGST/SGST claimed but the place of supply is another state | Meridian Print · INV-2214 | ₹64,800 | 5d |
| Low | Verified | Line item could not be matched to a catalogue item code | Signzy Technologies · 3 lines | — | 40d |

Deck headline for the hero artboard:

> **23 things need you. ₹4.2 Cr at risk. 3 are blocking a payment today.**

Zero-state copy:

> **Nothing needs you right now.**
> Last full scan 6 minutes ago: 412 bills, 88 vendors, 11 checks.
> `31 resolved this week` · `₹1.2 Cr protected` · `0 open across 4 lanes`

**Voice.** Plain, specific, never chirpy. State what is wrong in the words a controller would use to
a colleague. "Bill is four steps into a chain that has no approver at step 3" — not "Workflow
configuration anomaly detected." Titles carry no entity names (those are separate chips, so a cluster
header stays readable); they are under 80 characters and survive truncation.

---

## 6. Two component specifications to get exactly right

### The finding card

This is the most important component in the feature — twelve of them are the page.

Encode four dimensions at a glance, and **never by colour alone**:

- **Severity** — a 3px left rail *plus* a badge with both an icon and a word. Three levels only:
  *Blocking* (danger), *High* (warn), *Review* (info). Not five — nobody calibrates five, and
  everything lands in the middle two.
- **Confidence** — this is the make-or-break detail. A deterministic finding and an AI guess styled
  identically destroys trust in both. Differentiate **structurally**: verified gets a solid rail,
  AI-suspected gets a **dashed** rail, plus a quiet `✓ Verified` badge versus a `◈ Suspected` badge
  with a small three-pip meter. Visible at a glance, at a distance, and in greyscale.
- **Money at risk** — right-aligned, tabular, compact. Absent means absent.
- **Age / urgency** — relative ("6d"), and past a due date it becomes a badge reading "2d overdue".

Plus: up to three entity chips (each a link to that vendor or bill, with a "+N" overflow), and one
primary action.

**The AI-suspected variant changes the verb.** A verified finding's primary action is the fix itself
("Update GSTIN to 29AABCU9603R1ZJ"). A suspected finding's is **"Open & check"** — an AI finding never
offers a one-click write as its primary affordance. Suspected findings also carry 👍 / 👎 feedback
buttons that verified ones don't; this is both an honesty signal to the user and the system's
training data.

### The drawer

520px, right side, over the queue — the list stays visible and keeps its scroll position. Vertical
order is fixed and load-bearing:

1. **What's wrong** — two or three sentences of plain explanation.
2. **Evidence** — one of the shapes from §4.4.
3. **The cluster escape hatch** — "36 other bills from Acme Ltd have the same problem → handle all 37".
4. **Fix it here** — an inline form, at most three fields, with a plain-language preview of the
   after-state: *"The bill matches the master and re-enters the chain at step 2."*
5. **Or go to the source** — deep links that land on the bill or vendor with the exact field
   highlighted.

The fix is always *below* the evidence. You cannot reach the fix without having scrolled past why.

---

## 7. States and interactions to show

- **Order freezes on mount.** New findings arriving from a background poll must never reshuffle cards
  under the user's cursor — design a "3 new findings since you opened this — [Show]" pill instead.
- **Undo is a persistent strip, not a toast.** At speed a user resolves eight findings in ten seconds
  and needs to reverse the third; a toast for a card that has scrolled away is useless. Design a 30s
  strip with a countdown, pinned below the queue.
- **Optimistic resolution** — the card animates out, counts decrement, and focus lands on the card
  that took its index. Show the failure case too: the card slides back into place with an inline
  error on the card itself ("Couldn't update — the vendor master changed 2 minutes ago"), not only
  a toast.
- **Can see but cannot fix** — the card renders identically except the actions collapse to a lock
  chip reading `Needs Finance Controller` plus a `[Notify]` button. Never hide it: an approver who
  can see a GST problem they can't fix still must know before they approve.
- **Keyboard triage** — design a shortcut sheet (`?`) covering j/k to move, o to open, e for the
  primary action, s to snooze, d to dismiss, x to select, u to undo. Show the focused-card state
  distinctly from the hovered state.
- **Dismissal takes a reason** from five options, the last being *"Never show me this type again."*
  Design that small dialog — free-text dismissal yields nothing, and this is how the system learns.

---

## 8. Accessibility — non-negotiable

- Every text colour above is contrast-checked; `#9ca3af` is for borders and dividers only.
- Severity and confidence are never communicated by colour alone — icon and word always present.
- One polite live region announcing "Resolved. 22 remaining." Background poll updates are **not**
  announced, or the live region becomes noise a screen-reader user switches off.
- Visible focus ring on everything interactive; a logical focus order deck → tabs → filters → queue.
- Honour reduced-motion: the card exit becomes instant, the drawer fades rather than slides.
- Design at 200% zoom for at least the queue — show me it doesn't break.

---

## 9. Anti-patterns — explicitly avoid

- A dense analytics dashboard. This is a **queue**, not a BI page. No sparklines, no donut charts, no
  KPI tile grid competing with the findings.
- A flat table as the primary view. A table is the *escape hatch* (artboard 8); the default is a
  ranked stream of cards, because each finding is a decision, not a row.
- Red everywhere. If most of the page is danger-coloured, nothing is urgent. Severity colour should
  be scarce enough that *Blocking* actually reads as blocking.
- Badge soup. A card carries at most four chips. Anything more belongs in the drawer.
- Infinite scroll. Finance users use Cmd+F and need to know how far through they are. An explicit
  "Load next 12" button is the honest pattern.
- Celebratory empty-state illustrations or a bare "No findings 🎉" — that reads as a failed fetch.
- Any suggestion that "the AI found nothing" means "everything is fine." The system must never render
  absence of findings as a green tick.
- Modals for the finding detail. It must be a drawer, so the queue stays visible and position survives.

---

## 10. What success looks like

Three tests for the final design:

1. **The eleven-minute test.** Can a controller open this at 8:40am, understand their exposure in
   four seconds, and clear the three blocking items before their call — without opening anything else?
2. **The trust test.** Looking at any single card, is it immediately obvious whether this is a fact
   the system verified or a guess it wants a human to judge?
3. **The 400 test.** Does a user with 400 open findings feel *in control* rather than buried — and
   can they see, without clicking, exactly how many they aren't being shown and why?

Deliver the artboards with short annotations on the hero and the card anatomy explaining the
encoding decisions. I care more about the finding card, the drawer and the bounded-queue mechanics
than about visual novelty — this is a tool people will use every morning for years.
