/**
 * Correcting a value and running the action against what you typed.
 *
 * The case this exists for: an IFSC column holding "New Friends Colony (110025)
 * & IDFB0020102". Verification fails every time and never says why, and the fix
 * is one field deep — so the drawer offers the field rather than sending the
 * broken value again.
 */
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Finding, FindingAction } from '../../api/review'

// The drawer reaches DocumentPane, which loads pdf.js; pdf.js touches DOMMatrix
// at import time and jsdom has no such thing. This finding has no document
// anyway, so the pane is stubbed rather than the whole renderer polyfilled.
vi.mock('./DocumentPane', () => ({ DocumentPane: () => null }))

import { ToastProvider } from '../../ui'
import { FindingDrawer } from './FindingDrawer'

const VERIFY: FindingAction = {
  id: 'verify_bank', label: 'Check and verify', kind: 'safe',
  endpoint: '/api/vendors/v1/verify-bank', method: 'POST',
  fields: [
    { key: 'account_number', label: 'Account number', kind: 'text', value: '78387375298' },
    {
      key: 'ifsc', label: 'IFSC', kind: 'text',
      value: 'New Friends Colony (110025) & IDFB0020102',
      pattern: '^[A-Za-z]{4}0[A-Za-z0-9]{6}$', hint: 'Eleven characters, e.g. IDFB0020102',
    },
  ],
  preview: 'Runs a penny-drop check on these details.',
  capability: 'verify', destructive: false, href: null, body: {}, confirm: null,
}

const FINDING: Finding = {
  finding_id: 'f1', finding_type: 'vendor_bank_unverified', family: 'vendor_identity',
  lane: 'blocking', subject: { type: 'vendor', id: 'v1' }, severity: 'HIGH',
  confidence: 1, verification: 'VERIFIED', is_suspected: false, money_at_risk: 628350,
  money_currency: 'INR', money_at_risk_inr: 628350, due_at: null, rank: 1,
  title: 'Vendor bank account has never been verified',
  detail: 'Payment is blocked until the account passes a penny-drop check.',
  evidence: {}, actions: [VERIFY], deep_link: '', document: null, can_act: true,
  required_capability: 'verify', status: 'OPEN', status_reason: null, status_by: null,
  snoozed_until: null, detector: 'adapter:vendor_identity', source_service: 'kyc_verification',
  model: null, occurrence_count: 1, first_detected_at: '2026-09-13T02:24:00Z',
  last_seen_at: '2026-09-13T02:24:00Z', entities: [],
}

function open(onAct = vi.fn()) {
  render(
    <ToastProvider>
      <FindingDrawer
        finding={FINDING}
        onClose={vi.fn()}
        onStatus={vi.fn()}
        onNavigate={vi.fn()}
        onAct={onAct}
      />
    </ToastProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'Check and verify' }))
  return onAct
}

describe('an action that carries fields', () => {
  it('opens a form prefilled with what is on file', () => {
    open()
    expect(screen.getByDisplayValue('78387375298')).toBeTruthy()
    // The broken value is shown, because that is what is being corrected.
    expect(screen.getByDisplayValue('New Friends Colony (110025) & IDFB0020102')).toBeTruthy()
  })

  it('will not submit a value that cannot be an IFSC', () => {
    const onAct = open()
    const submit = screen.getAllByRole('button', { name: 'Check and verify' })
      .find(b => (b as HTMLButtonElement).type === 'submit') as HTMLButtonElement
    expect(submit.disabled).toBe(true)
    fireEvent.click(submit)
    expect(onAct).not.toHaveBeenCalled()
    expect(screen.getByText(/Eleven characters/)).toBeTruthy()
  })

  it('sends what the user typed, not what the finding was built with', () => {
    const onAct = open()
    fireEvent.change(screen.getByDisplayValue('New Friends Colony (110025) & IDFB0020102'), {
      target: { value: 'IDFB0020102' },
    })
    const submit = screen.getAllByRole('button', { name: 'Check and verify' })
      .find(b => (b as HTMLButtonElement).type === 'submit') as HTMLButtonElement
    expect(submit.disabled).toBe(false)
    fireEvent.click(submit)

    expect(onAct).toHaveBeenCalledWith(VERIFY, {
      account_number: '78387375298',
      ifsc: 'IDFB0020102',
    })
  })
})
