import type { Bill } from '../../api/invoices'
import { billAmount, billMoney, dayOf, daysBetween, isOverdue, isPaid, isUnconverted, money, outstandingOf, shortDate, stageOf } from './vendorStats'

/**
 * What "Write with AI" offers to say, worked out from the bills the email is
 * about: the ones attached to it first, then any the conversation mentions.
 * Each suggestion names the bill and its state, so one click gives a draft
 * about the right bill with the right facts.
 */

export interface IntentGroup { title: string; items: string[] }

const GENERAL = [
  'Share the payment status of their open bills',
  'Ask for a statement of account',
  'Ask for a corrected invoice',
  'Acknowledge and say we’ll get back shortly',
]

/**
 * What the vendor actually received for a paid bill: the amount recorded as
 * paid, else the net payable after TDS, else the bill amount. Bill-currency
 * figures are only used for INR bills, since money() formats rupees.
 */
export function paidAmount(b: Bill): number {
  const foreign = b.inr_amount_current != null && Number(b.inr_amount_current) !== Number(b.total_amount)
  if (!foreign) {
    if (Number(b.amount_paid) > 0) return Number(b.amount_paid)
    if (Number(b.net_payable) > 0) return Number(b.net_payable)
  }
  return billAmount(b)
}

/** What there is to say about one bill, most likely first. */
export function billIntents(b: Bill, now: Date, attached: boolean): string[] {
  const no = b.id || 'this bill'
  if (isPaid(b)) {
    // No paid-on date here: payment_updated_at is when the status was last
    // edited, not when the money moved, so the draft shouldn't claim a date.
    return [
      `Confirm ${no} (${billMoney(b, paidAmount(b))}) has been paid`,
      attached ? `Send ${no} and ask them to confirm they received the payment` : `Ask them to confirm they received payment for ${no}`,
    ]
  }
  const amt = billMoney(b, outstandingOf(b) || billAmount(b))
  const due = dayOf(b.due_date)
  const first = isOverdue(b, now)
    ? `Tell them when ${no} (${amt}, ${daysBetween(due!, now)} days overdue) will be paid`
    : stageOf(b) >= 2
      ? `Tell them ${no} (${amt}) is approved and due ${due ? shortDate(b.due_date) : 'soon'}`
      : `Let them know ${no} (${amt}) is in approval`
  return [
    first,
    attached ? `Ask them to confirm the amount on ${no} matches their records` : `Ask for a corrected copy of ${no}`,
  ]
}

export function aiIntents({ attached, mentioned, now, hasThread }: {
  /** Bills attached to the email being written. */
  attached: Bill[]
  /** Bills the conversation mentions by number. */
  mentioned: Bill[]
  now: Date
  /** Replying in a conversation (rather than a new email). */
  hasThread: boolean
}): IntentGroup[] {
  const groups: IntentGroup[] = []
  if (attached.length > 1) {
    const total = attached.reduce((t, b) => t + billAmount(b), 0)
    const late = attached.filter(b => isOverdue(b, now))
    groups.push({
      title: `About the ${attached.length} attached bills`,
      items: [
        `Send these ${attached.length} bills${attached.some(isUnconverted) ? '' : ` (${money(total)})`} and ask them to confirm they match their records`,
        `Share the payment status of these ${attached.length} bills`,
        ...(late.length ? [`Tell them when the ${late.length} overdue ${late.length === 1 ? 'one' : 'ones'} will be paid`] : []),
      ],
    })
  } else if (attached.length === 1) {
    groups.push({ title: `About ${attached[0].id || 'the attached bill'}`, items: billIntents(attached[0], now, true) })
  }
  const others = mentioned.filter(b => !attached.some(a => a.invoice_id === b.invoice_id)).slice(0, 3)
  if (others.length) {
    groups.push({ title: 'About bills in this conversation', items: others.map(b => billIntents(b, now, false)[0]) })
  }
  const general = [...(hasThread ? ['Reply helpfully to their last message'] : []), ...GENERAL]
  groups.push({ title: groups.length ? 'Something else' : 'What should it say?', items: groups.length ? general.slice(0, 3) : general })
  return groups
}

const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')

/**
 * A bill number as a whole token: "1234" must not match inside "12345", a
 * phone number or "INV-1234A". Letters and digits on either side break it.
 */
export function billIdPattern(id: string, flags = 'i'): RegExp {
  return new RegExp(`(?<![a-z0-9])${esc(id)}(?![a-z0-9])`, flags)
}

/** Bills whose number appears in any of these texts. */
export function billsMentioned(bills: Bill[], texts: string[]): Bill[] {
  const hay = texts.join('\n')
  return bills.filter(b => b.id && b.id.length >= 4 && billIdPattern(b.id).test(hay))
}
