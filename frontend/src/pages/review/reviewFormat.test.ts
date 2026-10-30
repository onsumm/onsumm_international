import { describe, expect, it } from 'vitest'

import type { Finding } from '../../api/review'
import {
  LANE_LABELS, LANE_ORDER, MINUTES_PER_FINDING, ageLabel, blockedLine,
  completedLine, dueLabel, dueLine, familyDisplay, formatHours, hoursSaved,
  pageCount, pageLine, primaryAction, priorityDisplay, scopeLine, severityDisplay,
  statusDisplay, vendorCell, visibleEntities,
  vendorFix,
} from './reviewFormat'

const money = (n: number) => `₹${n}`

function finding(over: Partial<Finding> = {}): Finding {
  return {
    finding_id: 'f1', finding_type: 't', family: 'tax', lane: 'compliance',
    subject: { type: 'bill', id: 'b1' }, severity: 'HIGH', confidence: 1,
    verification: 'VERIFIED', is_suspected: false, money_at_risk: null,
    money_currency: 'INR', money_at_risk_inr: null, due_at: null, rank: 0.5,
    title: 'x', detail: '', evidence: {}, actions: [], deep_link: '/bills/b1',
    document: null,
    can_act: true, required_capability: 'edit', status: 'OPEN',
    status_reason: null, status_by: null, snoozed_until: null,
    detector: 'adapter:test', source_service: 'bills', model: null,
    occurrence_count: 1, first_detected_at: new Date().toISOString(),
    last_seen_at: new Date().toISOString(), entities: [],
    ...over,
  }
}

describe('severity', () => {
  it('collapses four stored levels into three shown ones', () => {
    // Five-level scales are never calibrated; LOW and MEDIUM both read "Review".
    expect(severityDisplay('CRITICAL').label).toBe('Blocking')
    expect(severityDisplay('HIGH').label).toBe('High')
    expect(severityDisplay('MEDIUM').label).toBe('Review')
    expect(severityDisplay('LOW').label).toBe('Review')
  })

  it('never returns undefined for an unexpected value', () => {
    expect(severityDisplay('WHATEVER' as never).label).toBe('Review')
  })
})

describe('KPI strip', () => {
  it('calls out money that is blocking a payment', () => {
    expect(blockedLine(421900, money)).toBe('₹421900 blocked from payment')
  })

  it('stays silent when nothing is blocked — a tile of noughts says nothing', () => {
    expect(blockedLine(0, money)).toBeNull()
  })

  it('leads the to-do tile with what is urgent about it', () => {
    expect(dueLine(7, 2, 12)).toBe('7 due within 3 days, 2 past due')
    expect(dueLine(7, 0, 12)).toBe('7 due within 3 days')
    expect(dueLine(0, 2, 12)).toBe('2 past due')
  })

  it('distinguishes "nothing urgent" from "nothing at all"', () => {
    expect(dueLine(0, 0, 12)).toBe('None due in the next 3 days')
    expect(dueLine(0, 0, 0)).toBe('Your queue is clear')
  })

  it('qualifies completions by how they closed, dismissals included', () => {
    expect(completedLine(14, 119, 131)).toBe('14 today, 91% accepted as proposed')
    expect(completedLine(0, 119, 131)).toBe('91% accepted as proposed')
  })

  it('does not print a percentage of nothing', () => {
    expect(completedLine(0, 0, 0)).toBe('Nothing closed yet this month')
  })

  it('estimates hours from one stated per-item constant', () => {
    expect(MINUTES_PER_FINDING).toBe(17)
    expect(hoursSaved(131)).toBeCloseTo(37.1, 1)
    expect(hoursSaved(0)).toBe(0)
  })

  it('keeps a decimal only where rounding would erase the value', () => {
    expect(formatHours(37.1)).toBe('37')
    expect(formatHours(2.56)).toBe('2.6')
    expect(formatHours(3)).toBe('3')
    expect(formatHours(0)).toBe('0')
  })
})

describe('scope line', () => {
  it('always states what is being withheld', () => {
    expect(scopeLine(12, 23, 411)).toBe('Showing 12 of 23 you can act on · 388 more you can see')
  })

  it('drops the tail when nothing is withheld', () => {
    expect(scopeLine(5, 5, 5)).toBe('Showing 5 of 5 you can act on')
  })

  it('never reports a negative remainder', () => {
    expect(scopeLine(3, 9, 2)).toBe('Showing 3 of 9 you can act on')
  })
})

describe('age and due', () => {
  const now = new Date('2026-09-12T09:00:00Z').getTime()
  const ago = (d: number) => new Date(now - d * 86_400_000).toISOString()
  const ahead = (d: number) => new Date(now + d * 86_400_000).toISOString()

  it('shortens age', () => {
    expect(ageLabel(ago(0), now)).toBe('today')
    expect(ageLabel(ago(1), now)).toBe('1d')
    expect(ageLabel(ago(12), now)).toBe('12d')
    expect(ageLabel(ago(65), now)).toBe('2mo')
  })

  it('surfaces overdue and stays quiet about the distant future', () => {
    expect(dueLabel(ahead(-2), now)).toBe('2d overdue')
    expect(dueLabel(ahead(0), now)).toBe('due today')
    expect(dueLabel(ahead(3), now)).toBe('due in 3d')
    expect(dueLabel(ahead(90), now)).toBeNull()
    expect(dueLabel(null, now)).toBeNull()
  })
})

describe('primary action', () => {
  const safe = {
    id: 'verify', label: 'Run verification', kind: 'safe' as const, endpoint: '/x',
    method: 'POST', fields: [], preview: '', capability: 'verify',
    destructive: false, href: null, body: {}, confirm: null,
  }

  it('offers the fix on a verified finding', () => {
    expect(primaryAction(finding({ actions: [safe] }))?.id).toBe('verify')
  })

  it('offers the fix on a suspected finding too', () => {
    // It used to return null here, which meant a contract's PAN and
    // payment-term conflicts — UNVERIFIED by construction — had no approve
    // button anywhere in the hub. The human still decides: the callers route
    // anything carrying `confirm` through ActionConfirm, and anything carrying
    // `fields` into the drawer. What is offered is the decision, not the write.
    expect(primaryAction(finding({ is_suspected: true, actions: [safe] }))?.id).toBe('verify')
  })

  it('ignores destructive actions as a primary affordance', () => {
    expect(primaryAction(finding({ actions: [{ ...safe, destructive: true }] }))).toBeNull()
  })
})

describe('entities', () => {
  it('caps chips and reports the overflow', () => {
    const f = finding({
      entities: [
        { type: 'bill', id: 'b', label: 'INV-1', role: 'subject' },
        { type: 'vendor', id: 'v', label: 'Acme', role: 'counterparty' },
        { type: 'vendor', id: 'w', label: 'Beta', role: 'related' },
      ],
    })
    const { shown, overflow } = visibleEntities(f, 2)
    expect(shown.map(e => e.label)).toEqual(['INV-1', 'Acme'])
    expect(overflow).toBe(1)
  })

  it('drops unlabelled entities rather than rendering a raw id', () => {
    const f = finding({ entities: [{ type: 'bill', id: 'b', label: null, role: 'subject' }] })
    expect(visibleEntities(f).shown).toHaveLength(0)
  })
})

describe('lanes', () => {
  it('labels every lane it can order', () => {
    for (const lane of LANE_ORDER) expect(LANE_LABELS[lane]).toBeTruthy()
  })
})

describe('the flat queue’s columns', () => {
  it('gives every family in the closed spine its own label and colour slot', () => {
    const families = [
      'reconciliation', 'payment', 'tax', 'data_quality',
      'approval', 'vendor_identity', 'duplication', 'fx',
    ]
    const cats = families.map(f => familyDisplay(f).cat)
    // Eight families, eight distinct categorical slots — two families sharing
    // a colour is a column nobody can scan.
    expect(new Set(cats).size).toBe(8)
    expect(cats).not.toContain(0)
    expect(familyDisplay('reconciliation').label).toBe('Recon')
    expect(familyDisplay('tax').label).toBe('Compliance')
  })

  it('renders a family the spine has not got yet without inventing a colour', () => {
    // The agent mints finding types at runtime; a family it invents must still
    // render as something, and as something that looks unknown.
    expect(familyDisplay('carbon_accounting')).toEqual({ label: 'Carbon accounting', cat: 0 })
    expect(familyDisplay('')).toEqual({ label: 'Other', cat: 0 })
  })

  it('reads severity as the order a queue is worked in', () => {
    expect(priorityDisplay('CRITICAL')).toEqual({ label: 'P0', tone: 'p0' })
    expect(priorityDisplay('HIGH').label).toBe('P1')
    expect(priorityDisplay('MEDIUM').label).toBe('P2')
    expect(priorityDisplay('LOW').label).toBe('P3')
    // Only the top two carry a colour; four tinted values is a rainbow in
    // which nothing is urgent.
    expect(priorityDisplay('MEDIUM').tone).toBe('flat')
    expect(priorityDisplay('LOW').tone).toBe('flat')
  })

  it('names the vendor and the bill it is on', () => {
    const f = finding({
      entities: [
        { type: 'vendor', id: 'v1', label: 'Om Logistics', role: 'subject' },
        { type: 'bill', id: 'b1', label: 'INV-4410', role: 'related' },
      ],
    })
    expect(vendorCell(f)).toEqual({ name: 'Om Logistics', ref: 'INV-4410' })
  })

  it('collapses several bills to a count rather than a wall of numbers', () => {
    const f = finding({
      entities: [
        { type: 'vendor', id: 'v1', label: 'Kaleyra', role: 'subject' },
        ...['a', 'b', 'c', 'd', 'e', 'f'].map((x, i) => ({
          type: 'bill', id: `b${i}`, label: `INV-${x}`, role: 'related',
        })),
      ],
    })
    expect(vendorCell(f)).toEqual({ name: 'Kaleyra', ref: '6 bills' })
  })

  it('falls back to the subject rather than leaving the scanned column blank', () => {
    const f = finding({
      entities: [{ type: 'gstin', id: 'g1', label: '27AABCU9603R1ZX', role: 'subject' }],
    })
    expect(vendorCell(f).name).toBe('27AABCU9603R1ZX')
  })

  it('puts "you cannot fix this" above "the agent is unsure"', () => {
    // Both are true of the same finding often enough that the order matters:
    // the lock is what tells the reader whether to be looking at the row.
    const locked = finding({ can_act: false, is_suspected: true })
    expect(statusDisplay(locked)).toEqual({ label: 'Blocked', tone: 'blocked' })

    const suspected = finding({ can_act: true, is_suspected: true })
    expect(statusDisplay(suspected)).toEqual({ label: 'Proposal pending', tone: 'pending' })

    expect(statusDisplay(finding()).label).toBe('Open')
    expect(statusDisplay(finding({ status: 'SNOOZED' })).label).toBe('Snoozed')
    // A closed finding reads as closed whoever it belongs to.
    expect(statusDisplay(finding({ status: 'DISMISSED', can_act: false })).label).toBe('Dismissed')
  })

  it('says where in the list you are', () => {
    expect(pageLine(75, 25, 636)).toBe('76–100 of 636')
    expect(pageLine(0, 12, 12)).toBe('1–12 of 12')
    // A short last page must not claim rows that are not there.
    expect(pageLine(625, 11, 636)).toBe('626–636 of 636')
    expect(pageLine(0, 0, 0)).toBe('No findings')
    // The grouped path returns no total, and inventing one would be a lie.
    expect(pageLine(0, 8, null)).toBe('8 shown')
  })

  it('counts pages without ever offering a page zero', () => {
    expect(pageCount(636, 25)).toBe(26)
    expect(pageCount(25, 25)).toBe(1)
    expect(pageCount(26, 25)).toBe(2)
    expect(pageCount(0, 25)).toBe(1)
    expect(pageCount(null, 25)).toBe(1)
  })
})

describe('vendorFix', () => {
  it('sends a missing bank account to Add Account', () => {
    expect(vendorFix({ finding_type: 'vendor_bank_missing' })).toMatchObject({ tab: 'bank', targets: ['bank.add'] })
  })

  it('points at verify first, falling back to add when there is nothing to verify', () => {
    expect(vendorFix({ finding_type: 'vendor_gstin_unverified' })?.targets).toEqual(['gstins.verify', 'gstins.add'])
  })

  it('leaves other findings alone', () => {
    expect(vendorFix({ finding_type: 'approval_stalled' })).toBeNull()
  })
})

describe('vendorFix for a missing PAN', () => {
  it('opens Details and prefers the edit input, falling back to the read-only field', () => {
    const fix = vendorFix({ finding_type: 'vendor_pan_missing' })
    expect(fix?.tab).toBe('details')
    expect(fix?.targets).toEqual(['details.pan-input', 'details.pan'])
    expect(fix?.messages?.['details.pan']).toMatch(/Click Edit/)
  })
})

import { pageItems } from './reviewFormat'

describe('pageItems', () => {
  it('lists every page when there are seven or fewer', () => {
    expect(pageItems(1, 5)).toEqual([1, 2, 3, 4, 5])
  })

  it('always uses seven slots beyond that, so the pager never changes width', () => {
    expect(pageItems(1, 13)).toEqual([1, 2, 3, 4, 5, 'gap', 13])
    expect(pageItems(7, 13)).toEqual([1, 'gap', 6, 7, 8, 'gap', 13])
    expect(pageItems(13, 13)).toEqual([1, 'gap', 9, 10, 11, 12, 13])
    for (let p = 1; p <= 13; p++) expect(pageItems(p, 13)).toHaveLength(7)
  })

  it('always includes the current page', () => {
    for (let p = 1; p <= 40; p++) expect(pageItems(p, 40)).toContain(p)
  })
})
