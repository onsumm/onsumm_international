import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ReceiptStep } from './ReceiptStep'
import type { ReceiptData } from './receiptContent'
import type { PayResult } from './usePaymentFlow'

const RRN = '690415062015'

function result(over: Partial<PayResult> = {}): PayResult {
  return {
    disbursement_id: 'd1',
    invoice_id: 'inv-42d4c5b7',
    status: 'SUCCESS',
    amount: 100,
    bank_rrn: RRN,
    tran_ref_no: null,
    status_message: 'Transaction Successful',
    billLabel: 'SIGNZY2526/08775',
    ...over,
  } as PayResult
}

function receiptFor(results: PayResult[]): ReceiptData {
  return {
    vendorName: 'Signzy Technologies Pvt. Ltd.',
    beneficiaryName: 'SIGNZY TECHNOLOGIES PRIVATE LIMITED',
    beneficiaryAccount: '···7889',
    beneficiaryIfsc: 'UTIB0000086',
    beneficiaryBank: 'Axis Bank',
    remitterLabel: null,
    results,
    paidAt: new Date('2026-08-22T10:00:00Z'),
    overridden: false,
    demo: true,
  }
}

function renderStepC(results: PayResult[], onDone = vi.fn()) {
  return render(
    <ReceiptStep
      results={results}
      receipt={receiptFor(results)}
      onCheckStatus={() => {}}
      checking={{}}
      onPayMore={() => {}}
      onDone={onDone}
    />,
  )
}

function renderStep(results: PayResult[], onDone = vi.fn()) {
  render(
    <ReceiptStep
      results={results}
      receipt={receiptFor(results)}
      onCheckStatus={() => {}}
      checking={{}}
      onPayMore={() => {}}
      onDone={onDone}
    />,
  )
  return onDone
}

describe('receipt printing', () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }))
  afterEach(() => vi.useRealTimers())

  it('prints as soon as a settled payment lands, without being asked', () => {
    const onDone = renderStep([result()])
    // No click: the money has moved, so the receipt is already on screen.
    expect(screen.getByRole('dialog', { name: /printing payment receipt/i })).toBeInTheDocument()
    expect(onDone).not.toHaveBeenCalled()
  })

  it('never dismisses itself once printed', async () => {
    const onDone = renderStep([result()])
    await act(async () => { vi.advanceTimersByTime(10_000) })
    // The slip is the receipt; leaving is the user's decision.
    expect(screen.getByRole('dialog', { name: /payment receipt/i })).toBeInTheDocument()
    expect(onDone).not.toHaveBeenCalled()
  })

  it('offers download, pay another and done once the paper is out', async () => {
    renderStep([result()])
    const slip = screen.getByRole('dialog', { name: /receipt/i })
    // Held back while the paper is still moving.
    expect(within(slip).queryByRole('button', { name: 'Done' })).toBeNull()
    await act(async () => { vi.advanceTimersByTime(5000) })
    expect(within(slip).getByRole('button', { name: /download pdf/i })).toBeInTheDocument()
    expect(within(slip).getByRole('button', { name: 'Pay another bill' })).toBeInTheDocument()
    expect(within(slip).getByRole('button', { name: 'Done' })).toBeInTheDocument()
  })

  it('closes only when the user picks Done', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const onDone = renderStep([result()])
    await act(async () => { vi.advanceTimersByTime(5000) })
    const slip = screen.getByRole('dialog', { name: /receipt/i })
    await user.click(within(slip).getByRole('button', { name: 'Done' }))
    expect(onDone).toHaveBeenCalledTimes(1)
  })

  it('a backdrop click finishes the print instead of closing', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const onDone = renderStep([result()])
    const slip = screen.getByRole('dialog', { name: /receipt/i })
    await user.click(slip)
    // Fast-forwarded, not dismissed — a mis-click must not skip past a receipt.
    expect(within(slip).getByRole('button', { name: 'Done' })).toBeInTheDocument()
    expect(onDone).not.toHaveBeenCalled()
  })

  it('quotes the same reference and amount the filed receipt does', () => {
    renderStep([result()])
    const slip = screen.getByRole('dialog', { name: /receipt/i })
    expect(slip).toHaveTextContent(RRN)
    expect(slip).toHaveTextContent('SIGNZY2526/08775')
    expect(slip).toHaveTextContent('DEMO — NO REAL FUNDS MOVED')
  })

  it('carries the tear line and the head of the next form', () => {
    const { container } = renderStepC([result()])
    // Continuous roll: the slip is still attached to the next, unprinted form.
    expect(container.querySelector('.rcpt-perf')).toBeTruthy()
    expect(container.querySelector('.rcpt-next')).toBeTruthy()
    // Decorative paper, not content — a screen reader should not announce them.
    expect(container.querySelector('.rcpt-perf')).toHaveAttribute('aria-hidden', 'true')
    expect(container.querySelector('.rcpt-next')).toHaveAttribute('aria-hidden', 'true')
  })

  it('measures the roll including the tear line, so all of it feeds out', () => {
    const { container } = renderStepC([result()])
    const paper = container.querySelector('.rcpt-paper')
    // The perforation and next-form stub sit inside .rcpt-paper, so the measured
    // travel covers them; outside it they would never leave the printer.
    expect(paper?.querySelector('.rcpt-perf')).toBeTruthy()
    expect(paper?.querySelector('.rcpt-next')).toBeTruthy()
  })

  it('does not print over a payment that still needs a decision', () => {
    renderStep([result({ status: 'FAILED', bank_rrn: null })])
    // "Try again" lives on the card underneath; a slip would bury it.
    expect(screen.queryByRole('dialog', { name: /receipt/i })).toBeNull()
  })

  it('does not print while the bank is still deciding', () => {
    renderStep([result({ status: 'PENDING', bank_rrn: null })])
    expect(screen.queryByRole('dialog', { name: /receipt/i })).toBeNull()
    expect(screen.getByRole('button', { name: /check status/i })).toBeInTheDocument()
  })
})
