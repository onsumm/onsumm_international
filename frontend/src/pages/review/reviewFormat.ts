/**
 * Pure presentation helpers for the Review Hub. No React, no I/O — see
 * reviewFormat.test.ts.
 *
 * The severity collapse is the notable decision: the backend stores four levels
 * (the existing `severity_level` enum) and the UI shows three. Five-level scales
 * are not calibrated by anyone — everything lands in the middle two — so LOW and
 * MEDIUM both read as "Review". The distinction survives in the data and in the
 * ranking; it just stops being a visual claim the user has to decode.
 */
import type { Finding, Lane, Severity } from '../../api/review'
import { displayLocale } from '../../ui/format'

export type SeverityTone = 'blocking' | 'high' | 'review'

export interface SeverityDisplay {
  tone: SeverityTone
  label: string
}

const SEVERITY: Record<Severity, SeverityDisplay> = {
  CRITICAL: { tone: 'blocking', label: 'Blocking' },
  HIGH:     { tone: 'high',     label: 'High' },
  MEDIUM:   { tone: 'review',   label: 'Review' },
  LOW:      { tone: 'review',   label: 'Review' },
}

export function severityDisplay(s: Severity): SeverityDisplay {
  return SEVERITY[s] ?? SEVERITY.MEDIUM
}

export const LANE_LABELS: Record<Lane, string> = {
  blocking:   'Blocking',
  money:      'Money',
  compliance: 'Compliance',
  quality:    'Data',
  suspected:  'Suspected',
}

export const LANE_ORDER: Lane[] = ['blocking', 'money', 'compliance', 'quality', 'suspected']

/** One-line explanation of each lane, for the filter row's title attribute. */
export const LANE_HINTS: Record<Lane, string> = {
  blocking:   'Stopping a payment or an approval right now',
  money:      'Where money could be lost or overpaid',
  compliance: 'What would fail an audit or a return',
  quality:    'Problems with the data itself',
  suspected:  'Raised by the agent and not yet verified — judge these yourself',
}

/**
 * What the default filter is doing, in words, so nothing is silently withheld.
 * This sentence is the whole "show everything / overwhelm nobody" argument.
 */
export function scopeLine(shown: number, actionable: number, visible: number): string {
  const rest = Math.max(visible - actionable, 0)
  const head = `Showing ${shown} of ${actionable} you can act on`
  return rest > 0 ? `${head} · ${rest} more you can see` : head
}

/** Relative age, short enough for a card. */
export function ageLabel(iso: string, now = Date.now()): string {
  const days = Math.floor((now - new Date(iso).getTime()) / 86_400_000)
  if (days <= 0) return 'today'
  if (days === 1) return '1d'
  if (days < 30) return `${days}d`
  const months = Math.floor(days / 30)
  return months === 1 ? '1mo' : `${months}mo`
}

/** Overdue reads as urgency; a future due date is just context. */
export function dueLabel(iso: string | null, now = Date.now()): string | null {
  if (!iso) return null
  const days = Math.round((new Date(iso).getTime() - now) / 86_400_000)
  if (days < 0) return `${Math.abs(days)}d overdue`
  if (days === 0) return 'due today'
  if (days === 1) return 'due tomorrow'
  if (days <= 14) return `due in ${days}d`
  return null
}

/**
 * The primary action for a card.
 *
 * A suspected finding used to return null here — the model proposes, the human
 * decides, so the queue offered only "Open & check". In practice that read as
 * the fix being *missing*: a contract's PAN/payment-term conflicts arrive
 * UNVERIFIED by construction (`_verification_for` in master_data_conflict.py
 * only reaches VERIFIED once someone has approved the reading), so on those
 * findings — the ones a user is most often triaging — there was no approve
 * button anywhere in the hub.
 *
 * It is offered now, but it is not a one-click write: the caller routes an
 * action carrying `confirm` through the same dialog the drawer uses, with the
 * same before → after line, and an action carrying `fields` opens the drawer
 * instead. The human still decides; they no longer have to go looking for
 * where to say so.
 */
export function primaryAction(f: Finding) {
  return f.actions.find(a => a.kind === 'safe' && !a.destructive) ?? null
}

/** Entity chips, capped so a card stays one line of meta. */
export function visibleEntities(f: Finding, max = 2) {
  const named = f.entities.filter(e => e.label)
  return { shown: named.slice(0, max), overflow: Math.max(named.length - max, 0) }
}

/* ── The KPI strip ────────────────────────────────────────────────────────
 *
 * Four tiles above the queue, each one number the user is accountable for
 * plus the one qualifier that changes what they do about it. The census
 * sentence used to carry this; it could state exposure but not *what to do
 * next*, because a sentence long enough to hold four numbers is a sentence
 * nobody reads.
 *
 * Every line below degrades to null or to a plain statement when its number
 * is zero, so a clear queue reads as clear rather than as a row of noughts.
 */

/**
 * Money on findings that are stopping a payment right now — a strictly worse
 * number than total exposure, which is why it is called out under it rather
 * than folded into it.
 */
export function blockedLine(
  blockingMoneyInr: number,
  formatMoney: (n: number) => string,
): string | null {
  if (!blockingMoneyInr || blockingMoneyInr <= 0) return null
  return `${formatMoney(blockingMoneyInr)} blocked from payment`
}

/** What makes the to-do count urgent, rather than merely large. */
export function dueLine(dueSoon: number, overdue: number, actionable: number): string {
  const parts: string[] = []
  if (dueSoon > 0) parts.push(`${dueSoon} due within 3 days`)
  if (overdue > 0) parts.push(`${overdue} past due`)
  if (parts.length) return parts.join(', ')
  return actionable > 0 ? 'None due in the next 3 days' : 'Your queue is clear'
}

/**
 * Closures this month, qualified by how they closed. "Accepted as proposed"
 * is resolved-or-auto-resolved over everything closed: a dismissal is the
 * user telling us we were wrong, and a completion count that hides those is
 * a vanity number.
 */
export function completedLine(today: number, accepted: number, total: number): string {
  if (total <= 0) return 'Nothing closed yet this month'
  const parts: string[] = []
  if (today > 0) parts.push(`${today} today`)
  parts.push(`${Math.round((accepted / total) * 100)}% accepted as proposed`)
  return parts.join(', ')
}

/**
 * Minutes of manual work one finding stands in for — chasing the document,
 * reading it, and making the correction by hand. A single stated constant,
 * not a per-type model: the tile says "estimated" and prints this number
 * underneath, so the assumption is on screen next to the claim it produces.
 */
export const MINUTES_PER_FINDING = 17

export function hoursSaved(resolved: number, minutesEach = MINUTES_PER_FINDING): number {
  if (resolved <= 0) return 0
  return (resolved * minutesEach) / 60
}

/** One decimal only where the whole number would round away the whole value. */
export function formatHours(h: number): string {
  if (h <= 0) return '0'
  return h < 10 ? h.toFixed(1).replace(/\.0$/, '') : String(Math.round(h))
}

/* ── The flat queue's columns ─────────────────────────────────────────────
 *
 * The queue used to collapse findings by kind — one row reading "Vendor has no
 * bank account on file × 224". That answered "what kinds of problem do we
 * have"; it could not answer "which vendor, on which bill, for how much",
 * which is the question someone working the queue is actually holding. The
 * table is one row per finding now, and these helpers are the columns.
 */

/** The closed 8-value family spine, as a chip. */
const FAMILY_DISPLAY: Record<string, { label: string; cat: number }> = {
  reconciliation:  { label: 'Recon',      cat: 1 },
  payment:         { label: 'Payment',    cat: 5 },
  tax:             { label: 'Compliance', cat: 2 },
  data_quality:    { label: 'Data',       cat: 4 },
  approval:        { label: 'Approval',   cat: 3 },
  vendor_identity: { label: 'Vendor',     cat: 6 },
  duplication:     { label: 'Duplicate',  cat: 8 },
  fx:              { label: 'FX',         cat: 7 },
}

/** The Type filter's order: what blocks money first, housekeeping last. */
export const FAMILY_ORDER = [
  'approval', 'payment', 'vendor_identity', 'duplication', 'fx', 'reconciliation', 'tax', 'data_quality',
] as const

export function familyDisplay(family: string) {
  return FAMILY_DISPLAY[family] ?? { label: humaniseFamily(family), cat: 0 }
}

/** A family the spine does not know yet still has to render as something. */
function humaniseFamily(family: string): string {
  if (!family) return 'Other'
  return family.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())
}

/**
 * Vendor findings answered on one tab of the vendor card: a missing or
 * unverified GSTIN on GSTINs, a missing or unverified bank account on Bank.
 * Opening one goes straight there — the fix is adding or verifying the record,
 * which the finding card can only describe.
 */
export interface VendorFix {
  tab: 'gstins' | 'bank' | 'details'
  /** The card's `data-guide` controls to point at, best first. */
  targets: string[]
  message: string
  /** The message for a fallback target, when the first one isn't on screen. */
  messages?: Record<string, string>
}

const ADD_BANK = 'No bank account on record. Add it here to clear this.'
const ADD_GSTIN = 'No GSTIN on record. Add the vendor’s GSTIN here.'

const VENDOR_FIX_BY_TYPE: Record<string, VendorFix> = {
  // Details shows PAN read-only until Edit; the guide follows the field into
  // the edit form once it opens.
  vendor_pan_missing: {
    tab: 'details', targets: ['details.pan-input', 'details.pan'],
    message: 'Enter the vendor’s PAN here, then Save.',
    messages: { 'details.pan': 'No PAN on record. Click Edit to add it here.' },
  },
  vendor_bank_missing: {
    tab: 'bank', targets: ['bank.add'], message: ADD_BANK,
  },
  vendor_bank_unverified: {
    tab: 'bank', targets: ['bank.verify', 'bank.add'],
    message: 'This account isn’t verified yet. Verify it here before paying.',
    messages: { 'bank.add': ADD_BANK },
  },
  vendor_bank_unverifiable: {
    tab: 'bank', targets: ['bank.verify', 'bank.add'],
    message: 'This account couldn’t be verified. Retry, or add the correct account.',
    messages: { 'bank.add': ADD_BANK },
  },
  vendor_bank_new: {
    tab: 'bank', targets: ['bank.verify', 'bank.add'],
    message: 'New bank details arrived. Verify them before the next payment.',
    messages: { 'bank.add': ADD_BANK },
  },
  vendor_gstin_missing: {
    tab: 'gstins', targets: ['gstins.add'], message: ADD_GSTIN,
  },
  vendor_gstin_unverified: {
    tab: 'gstins', targets: ['gstins.verify', 'gstins.add'],
    message: 'Check this GSTIN on the GST portal — Reverify here.',
    messages: { 'gstins.add': ADD_GSTIN },
  },
  vendor_gstin_verification_failed: {
    tab: 'gstins', targets: ['gstins.verify', 'gstins.add'],
    message: 'The GST portal check failed. Reverify, or add the correct GSTIN.',
    messages: { 'gstins.add': ADD_GSTIN },
  },
}

export function vendorFix(f: Pick<Finding, 'finding_type'>): VendorFix | null {
  return VENDOR_FIX_BY_TYPE[f.finding_type] ?? null
}

/**
 * Findings that are shown as one card with a line per finding: a vendor's
 * failed payments, and every FX item. Mirrors ROLLUP_KEY_SQL on the server.
 */
export type RollupKind = 'payment' | 'fx'

export function rollupKind(f: Pick<Finding, 'finding_type' | 'family'>): RollupKind | null {
  if (f.finding_type === 'disbursement_failed') return 'payment'
  if (f.family === 'fx') return 'fx'
  return null
}

/** The queue row's title for a rolled-up card; the finding's own title otherwise. */
export function rowTitle(f: Finding): string {
  const n = f.rollup?.count ?? 1
  const kind = rollupKind(f)
  if (kind === 'payment') return n > 1 ? `${n} payments failed — bills still unpaid` : 'Payment failed — bill still unpaid'
  if (kind === 'fx' && n > 1) return `${n} FX items to review`
  return f.title
}

/** At-risk for the row: the whole card's total when it is a rollup. */
export function rowMoney(f: Finding): number | null {
  return f.rollup?.money_at_risk_inr ?? f.money_at_risk_inr
}

/**
 * Severity as a priority label.
 *
 * Same four stored levels, read the way a queue is worked rather than the way
 * a detector emits them: P0 is not "critical in the abstract", it is the thing
 * you do first. The tone stops at P1 — colouring all four would make the
 * column a rainbow in which nothing is urgent.
 */
const PRIORITY: Record<Severity, { label: string; tone: 'p0' | 'p1' | 'flat' }> = {
  CRITICAL: { label: 'P0', tone: 'p0' },
  HIGH:     { label: 'P1', tone: 'p1' },
  MEDIUM:   { label: 'P2', tone: 'flat' },
  LOW:      { label: 'P3', tone: 'flat' },
}

export function priorityDisplay(s: Severity) {
  return PRIORITY[s] ?? PRIORITY.MEDIUM
}

/**
 * Who this is about, and on what.
 *
 * The vendor is the name; the reference is whichever bills the finding spans.
 * Several bills collapse to a count — "6 bills" is readable where six invoice
 * numbers side by side are not.
 */
export function vendorCell(f: Finding): { name: string | null; ref: string | null } {
  const vendor = f.entities.find(e => e.type === 'vendor' && e.label)
  const docs = f.entities.filter(e => (e.type === 'bill' || e.type === 'invoice') && e.label)

  let ref: string | null = null
  if (docs.length === 1) ref = docs[0].label
  else if (docs.length > 1) ref = `${docs.length} bills`
  else if (f.occurrence_count > 1) ref = `seen ${f.occurrence_count}×`

  if (vendor) return { name: vendor.label, ref }
  // No vendor entity: the subject itself is the best name available, and a
  // blank cell in the column people scan by is worse than a generic one.
  const fallback = f.entities.find(e => e.role === 'subject' && e.label)
  return { name: fallback?.label ?? null, ref }
}

export type StatusTone = 'open' | 'blocked' | 'pending' | 'muted'

/**
 * What is true of this finding right now, in one word.
 *
 * Order matters. "You cannot fix this yourself" outranks "the agent is not
 * sure", because it is the fact that changes whether the reader should be
 * looking at the row at all.
 */
export function statusDisplay(f: Finding): { label: string; tone: StatusTone } {
  if (f.status === 'SNOOZED') return { label: 'Snoozed', tone: 'muted' }
  if (f.status === 'RESOLVED' || f.status === 'AUTO_RESOLVED') {
    return { label: 'Resolved', tone: 'muted' }
  }
  if (f.status === 'DISMISSED') return { label: 'Dismissed', tone: 'muted' }
  if (!f.can_act) return { label: 'Blocked', tone: 'blocked' }
  if (f.is_suspected) return { label: 'Proposal pending', tone: 'pending' }
  if (f.status === 'ACKNOWLEDGED') return { label: 'Acknowledged', tone: 'open' }
  return { label: 'Open', tone: 'open' }
}

/** "76–100 of 636", or the honest thing to say when there is no page to be on. */
export function pageLine(offset: number, shown: number, total: number | null): string {
  if (total === null) return `${shown} shown`
  if (total === 0) return 'No findings'
  const first = offset + 1
  const last = Math.min(offset + shown, total)
  return `${first}–${last} of ${total.toLocaleString(displayLocale())}`
}

// The page-button run lives with the shared pager now.
export { pageItems } from '../../ui/Pager'

export function pageCount(total: number | null, perPage: number): number {
  if (!total || perPage <= 0) return 1
  return Math.max(Math.ceil(total / perPage), 1)
}
