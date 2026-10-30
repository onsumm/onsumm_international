import { describe, it, expect } from 'vitest'
import { validateForSubmission, blockingIssues } from './reviewValidation'

const good = {
  vendor: { name: 'Acme Ltd', gstin: '27AAPFU0939F1ZV' },
  invoice_number: 'INV-2291',
  invoice_date: '2026-07-01',
  due_date: '2026-07-31',
  subtotal: 100000,
  total_amount: 118000,
}

describe('submission gate', () => {
  it('blocks a bill with no vendor, amount, date or category', () => {
    const fields = blockingIssues(validateForSubmission({}, '')).map(i => i.field)
    expect(fields).toContain('vendor.name')
    expect(fields).toContain('total_amount')
    expect(fields).toContain('invoice_date')
    expect(fields).toContain('category')
  })

  it('blocks a GSTIN that fails its own checksum', () => {
    // The checksum was already computed and shown to the user; it just never
    // stopped the bill entering the approval chain.
    const issues = validateForSubmission({ ...good, vendor: { name: 'Acme', gstin: '27AAPFU0939F1ZZ' } }, 'Software')
    expect(blockingIssues(issues).map(i => i.field)).toContain('vendor.gstin')
  })

  it('blocks a subtotal larger than the total', () => {
    const issues = validateForSubmission({ ...good, subtotal: 200000 }, 'Software')
    expect(blockingIssues(issues).map(i => i.field)).toContain('subtotal')
  })

  it('tolerates sub-rupee rounding between subtotal and total', () => {
    const issues = validateForSubmission({ ...good, subtotal: 118000.4, total_amount: 118000 }, 'Software')
    expect(blockingIssues(issues).map(i => i.field)).not.toContain('subtotal')
  })

  it('blocks a zero or negative total', () => {
    expect(blockingIssues(validateForSubmission({ ...good, total_amount: 0 }, 'Software')).map(i => i.field)).toContain(
      'total_amount',
    )
  })

  it('warns without blocking on a missing GSTIN or invoice number', () => {
    const issues = validateForSubmission(
      { ...good, vendor: { name: 'Acme' }, invoice_number: '' },
      'Software',
    )
    expect(blockingIssues(issues)).toHaveLength(0)
    const warned = issues.filter(i => i.severity === 'warning').map(i => i.field)
    expect(warned).toEqual(expect.arrayContaining(['vendor.gstin', 'invoice_number']))
  })

  it('warns when the due date precedes the bill date', () => {
    const issues = validateForSubmission({ ...good, due_date: '2025-07-31' }, 'Software')
    expect(issues.find(i => i.field === 'due_date')?.severity).toBe('warning')
  })

  it('passes a complete, coherent bill', () => {
    expect(validateForSubmission(good, 'Software')).toHaveLength(0)
  })
})
