import { describe, it, expect } from 'vitest'
import {
  amountError, isOverrideComplete, summariseResults, stepsFor, classifyOtpError, SETTLE_BUDGET_MS,
  type PayResult,
} from './usePaymentFlow'
import { receiptTotal } from './receiptContent'

const result = (status: string, id = status): PayResult => ({
  disbursement_id: id,
  invoice_id: `inv-${id}`,
  billLabel: `B-${id}`,
  amount: 1000,
  status,
  status_message: null,
  bank_rrn: null,
})

describe('amount validation', () => {
  it('rejects an empty or unparseable amount', () => {
    expect(amountError(NaN, 5000)).toBe('Enter an amount')
    expect(amountError(null, 5000)).toBe('Enter an amount')
    expect(amountError(undefined, 5000)).toBe('Enter an amount')
  })

  it('enforces the same ₹1 floor as the backend', () => {
    expect(amountError(0, 5000)).toBe('Minimum ₹1')
    expect(amountError(0.5, 5000)).toBe('Minimum ₹1')
    expect(amountError(1, 5000)).toBeNull()
  })

  it('refuses to pay more than the outstanding', () => {
    expect(amountError(5001, 5000)).toBe('More than the outstanding amount')
    expect(amountError(5000, 5000)).toBeNull()
  })

  it('accepts an exact outstanding that carries trailing decimals', () => {
    // fact_invoices stores numeric(…,4): 59000.0000 must not read as "more than".
    expect(amountError(59000, 59000.0)).toBeNull()
    expect(amountError(59000.004, 59000)).toBeNull()
  })
})

describe('override completeness', () => {
  it('requires all four fields, reason included', () => {
    expect(isOverrideComplete({ account: '123', ifsc: 'HDFC0001', name: 'Acme', reason: 'bank pending' })).toBe(true)
    expect(isOverrideComplete({ account: '123', ifsc: 'HDFC0001', name: 'Acme', reason: '' })).toBe(false)
    expect(isOverrideComplete({ account: '', ifsc: 'HDFC0001', name: 'Acme', reason: 'x' })).toBe(false)
  })

  it('treats whitespace as missing', () => {
    expect(isOverrideComplete({ account: '123', ifsc: 'HDFC0001', name: 'Acme', reason: '   ' })).toBe(false)
  })
})

describe('batch summary', () => {
  it('counts a mixed batch the way the receipt reads it', () => {
    const s = summariseResults([result('SUCCESS', 'a'), result('SUCCESS', 'b'), result('PENDING'), result('FAILED')])
    expect(s).toMatchObject({ ok: 2, wait: 1, fail: 1 })
    expect(s.label).toBe('2 succeeded · 1 processing · 1 failed')
  })

  it('omits the categories that did not happen', () => {
    expect(summariseResults([result('SUCCESS')]).label).toBe('1 succeeded')
    expect(summariseResults([]).label).toBe('No payments')
  })

  it('counts any non-success, non-pending status as failed', () => {
    // The bank can return act-code failures we have not enumerated; they must
    // never be presented as anything but failed.
    expect(summariseResults([result('REJECTED')])).toMatchObject({ ok: 0, wait: 0, fail: 1 })
  })
})

describe('OTP failure classification', () => {
  // Exact strings the backend sends today (disbursement.py verify_otp /
  // verify_batch_otp) — this test breaks loudly if that wording drifts.
  it('recognises the backend’s fixed failure strings', () => {
    expect(classifyOtpError('OTP expired — request a new one')).toBe('expired')
    expect(classifyOtpError('Too many attempts — request a new OTP')).toBe('too_many')
    expect(classifyOtpError('Incorrect OTP')).toBe('wrong')
  })

  it('is case-insensitive', () => {
    expect(classifyOtpError('otp EXPIRED')).toBe('expired')
  })

  it('falls back to "other" for anything unrecognised — a network error, a 500', () => {
    expect(classifyOtpError('Network Error')).toBe('other')
    expect(classifyOtpError('')).toBe('other')
  })
})

describe('step sequence', () => {
  it('drops selection when the caller pinned one bill', () => {
    expect(stepsFor(true).map(s => s.key)).toEqual(['review', 'verify', 'receipt'])
    expect(stepsFor(false).map(s => s.key)).toEqual(['select', 'review', 'verify', 'receipt'])
  })
})

describe('the settling budget', () => {
  it('is a minute, matching the copy shown on the settling screen', () => {
    // The screen tells the user what happens at the cut-off, so the number it
    // quotes and the number the loop enforces have to be the same one.
    expect(SETTLE_BUDGET_MS).toBe(60_000)
  })

  it('reports a transfer the bank never resolved as processing, not as paid or failed', () => {
    // What the user sees after the wait: one row still in flight.
    const rows = [result('PENDING')]
    const s = summariseResults(rows)
    expect(s).toMatchObject({ ok: 0, wait: 1, fail: 0 })
    expect(s.label).toBe('1 processing')
  })

  it('does not let a still-processing row count toward the receipt total', () => {
    const rows = [result('SUCCESS', 'a'), { ...result('PENDING', 'b'), amount: 5000 }]
    // Only settled money is receipted; the pending row is listed, not totalled.
    expect(receiptTotal(rows)).toBe(1000)
  })
})
