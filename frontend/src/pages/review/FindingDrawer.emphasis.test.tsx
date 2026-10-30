/**
 * A conflict is an either/or, and the two buttons have to read as one.
 *
 * "Use this payment terms" told the reader neither which value it meant nor
 * what became of the other, and a second action rendered identically to the
 * first — two equally weighted primary buttons, with nothing to say which one
 * was the change and which was the status quo.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Finding, FindingAction } from '../../api/review'

vi.mock('./DocumentPane', () => ({ DocumentPane: () => null }))

import { ToastProvider } from '../../ui'
import { FindingDrawer } from './FindingDrawer'

const base = {
  kind: 'safe' as const, method: 'POST', fields: [], preview: '', capability: 'edit',
  destructive: false, href: null, body: {}, confirm: null,
}

const KEEP_NEW: FindingAction = {
  ...base, id: 'keep_new', label: "Keep the contract's payment terms (45 days)",
  endpoint: '/api/vendors/v1', emphasis: 'primary',
}
const KEEP_CURRENT: FindingAction = {
  ...base, id: 'keep_current', label: 'Keep current payment terms (30 days)',
  endpoint: '/api/vendors/v1/documents/d1/claims/0/discard', emphasis: 'secondary',
}

const FINDING: Finding = {
  finding_id: 'f1', finding_type: 'payment_term_conflict', family: 'vendor_identity',
  lane: 'compliance', subject: { type: 'vendor', id: 'v1' }, severity: 'MEDIUM',
  confidence: 1, verification: 'VERIFIED', is_suspected: false, money_at_risk: null,
  money_currency: 'INR', money_at_risk_inr: null, due_at: null, rank: 0.29,
  title: 'A vendor contract states a different payment terms than what is on file',
  detail: 'Quoted from the contract: "Payment shall be made by the 45 days".',
  evidence: {}, actions: [KEEP_NEW, KEEP_CURRENT], deep_link: '', document: null,
  can_act: true, required_capability: 'edit', status: 'OPEN', status_reason: null,
  status_by: null, snoozed_until: null, detector: 'adapter:master_data_conflict',
  source_service: 'vendors', model: null, occurrence_count: 1,
  first_detected_at: '2026-09-18T02:24:00Z', last_seen_at: '2026-09-18T02:24:00Z',
  entities: [],
}

describe('a conflict drawer weights its two choices', () => {
  it('names both values and mutes the one that changes nothing', () => {
    render(
      <ToastProvider>
        <FindingDrawer
          finding={FINDING}
          onClose={vi.fn()} onStatus={vi.fn()} onNavigate={vi.fn()} onAct={vi.fn()}
        />
      </ToastProvider>,
    )

    const keepNew = screen.getByRole('button', { name: /Keep the contract's payment terms \(45 days\)/ })
    const keepCurrent = screen.getByRole('button', { name: /Keep current payment terms \(30 days\)/ })

    // The change leads, the status quo is muted — not two identical buttons.
    expect(keepNew.className).toContain('u-btn--primary')
    expect(keepCurrent.className).toContain('u-btn--secondary')
    expect(keepCurrent.className).not.toContain('u-btn--primary')
  })

  it('still renders a lone action as the primary one', () => {
    render(
      <ToastProvider>
        <FindingDrawer
          finding={{ ...FINDING, actions: [{ ...KEEP_NEW, emphasis: undefined }] }}
          onClose={vi.fn()} onStatus={vi.fn()} onNavigate={vi.fn()} onAct={vi.fn()}
        />
      </ToastProvider>,
    )

    const only = screen.getByRole('button', { name: /Keep the contract's payment terms/ })
    expect(only.className).toContain('u-btn--primary')
  })
})
