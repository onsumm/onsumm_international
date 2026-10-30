import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { PaymentSidebar } from './PaymentSidebar'
import type { DisbursementStats } from '../../api/invoices'

const AU: DisbursementStats = {
  remitter_account_number: '2602201113751781', bank_name: 'AU Small Finance Bank (Demo)', bank_provider: 'AUBANK',
  ifsc: 'AUBL0002026', balance: 5000000, total_transactions: 0, successful_payments: 0, failed_payments: 0,
  pending_payments: 0, total_disbursed: 0, demo: true,
}
const BANKS = [
  { code: 'ICICI', label: 'ICICI Bank', wire: 'ICICI' },
  { code: 'HDFC', label: 'HDFC Bank', wire: 'HDFC' },
  { code: 'AUBANK', label: 'AU Small Finance Bank', wire: 'AUBANK' },
]

describe('payment sidebar', () => {
  it('shows the chosen bank’s own account and lets it be changed', async () => {
    const onBank = vi.fn()
    render(<PaymentSidebar stats={AU} banks={BANKS} bank="AUBANK" onBank={onBank} />)
    expect(screen.getByText('AU Small Finance Bank (Demo)')).toBeInTheDocument()
    expect(screen.getByText('···1781')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'AU Bank' })).toHaveAttribute('aria-checked', 'true')
    await userEvent.click(screen.getByRole('radio', { name: 'HDFC Bank' }))
    expect(onBank).toHaveBeenCalledWith('HDFC')
  })

  it('fixes the bank once the confirmation code has gone out', () => {
    render(<PaymentSidebar stats={AU} banks={BANKS} bank="AUBANK" onBank={() => {}} locked />)
    expect(screen.getByRole('radio', { name: 'HDFC Bank' })).toBeDisabled()
  })
})
