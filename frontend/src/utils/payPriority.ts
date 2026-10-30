/**
 * Payment priority — one calculation, shared between the Bills page and the
 * Payment Hub. This used to live only in BillsPage.tsx; the Payment Hub grew
 * its own separate overdue/due-this-week/not-yet-due grouping instead of
 * reusing it, which meant a bill could read "P0" on Bills and something
 * unrelated on the Payment Hub for the same due date. There is one priority
 * system, not two.
 *
 * Score from due date, then adjusted by value, then clamped:
 *   0 (P0) = overdue or due today       high value (> ₹50,000) → −1 (escalate)
 *   1 (P1) = due in 1–3 days            low value  (< ₹10,000) → +1 (de-escalate)
 *   2 (P2) = due in 4–7 days
 *   3 (P3) = due in 8+ days
 *
 * `manual_priority` (an admin override, stored on the bill) always wins over
 * the calculation. `done` (paid) and `null` (no due date / not yet approved)
 * are the two non-p0..p3 outcomes.
 */
import type { PriorityLabels } from '../api/invoices'

export type PayPriority = 'p0' | 'p1' | 'p2' | 'p3' | 'done' | null

/** The fields the calculation needs — a subset of Bill, so callers with a
 *  lighter shape (the Payment Hub's queue bills) don't need to fake the rest. */
export interface PriorityInput {
  due_date: string | null
  total_amount: number | null
  manual_priority?: string | null
  /** Omit when the caller's data is already known-live (e.g. the Payment
   *  Hub's queue only ever returns outstanding, non-draft bills) — only
   *  checked when provided. */
  status?: string | null
  payment_status?: string | null
}

export const PRIORITY_META: Record<NonNullable<PayPriority>, { label: string; color: string; bg: string; border: string; rank: number }> = {
  p0:   { label: 'P0', color: '#dc2626', bg: '#fef2f2', border: '#fecaca', rank: 0 },
  p1:   { label: 'P1', color: '#2563eb', bg: '#eff6ff', border: '#bfdbfe', rank: 1 },
  p2:   { label: 'P2', color: '#d97706', bg: '#fffbeb', border: '#fde68a', rank: 2 },
  p3:   { label: 'P3', color: '#059669', bg: '#f0fdf4', border: '#a7f3d0', rank: 3 },
  done: { label: '✓',  color: 'var(--ink-500)', bg: '#f9fafb', border: '#e5e7eb', rank: 4 },
}

export const DEFAULT_PRIORITY_LABELS: PriorityLabels = { p0: 'P0', p1: 'P1', p2: 'P2', p3: 'P3' }

export function priorityMeta(labels: PriorityLabels) {
  return {
    ...PRIORITY_META,
    p0: { ...PRIORITY_META.p0, label: labels.p0 },
    p1: { ...PRIORITY_META.p1, label: labels.p1 },
    p2: { ...PRIORITY_META.p2, label: labels.p2 },
    p3: { ...PRIORITY_META.p3, label: labels.p3 },
  }
}

const SCORE_TO_PRIORITY: PayPriority[] = ['p0', 'p1', 'p2', 'p3']

export function getPayPriority(bill: PriorityInput): PayPriority {
  if (bill.status !== undefined) {
    const status = bill.status?.toUpperCase()
    if (!status || status === 'DRAFT') return null
  }
  if (bill.payment_status === 'paid') return 'done'

  // Admin manual override takes precedence over auto-calculation
  if (bill.manual_priority && bill.manual_priority in PRIORITY_META) {
    return bill.manual_priority as PayPriority
  }

  if (!bill.due_date) return null                 // no due date → no priority yet

  const today = new Date(); today.setHours(0, 0, 0, 0)
  const days  = Math.round((new Date(bill.due_date).getTime() - today.getTime()) / 86400000)

  // Date-based score (0 = most urgent)
  let score = days <= 0 ? 0 : days <= 3 ? 1 : days <= 7 ? 2 : 3

  // Value-based adjustment
  const amount = bill.total_amount ?? 0
  if (amount > 50000) score -= 1        // high-value bill → escalate
  else if (amount < 10000) score += 1   // low-value bill → de-escalate

  // Clamp to valid range
  score = Math.max(0, Math.min(3, score))

  return SCORE_TO_PRIORITY[score]
}
