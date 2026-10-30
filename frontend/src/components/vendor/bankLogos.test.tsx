import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

const fetchImage = vi.hoisted(() => vi.fn(async (url: string) => `blob:${url}`))
vi.mock('axios', () => ({ default: { get: vi.fn(async () => ({ data: { codes: ['HDFC', 'SBIN'] } })) } }))
vi.mock('../../api/authedFiles', () => ({ fetchAuthedObjectUrl: fetchImage }))

import { BankMark, bankCode } from './bankLogos'

describe('bank symbols', () => {
  it('reads the bank from the IFSC', () => {
    expect(bankCode('HDFC0001721')).toBe('HDFC')
    expect(bankCode(' icic0003475 ')).toBe('ICIC')
  })

  it('falls back to the bank name, without mixing up similar names', () => {
    expect(bankCode(null, 'HDFC BANK LTD')).toBe('HDFC')
    expect(bankCode(null, 'Central Bank of India')).toBe('CBIN')
    expect(bankCode(null, 'Bank of India')).toBe('BKID')
    expect(bankCode(null, 'Indian Overseas Bank')).toBe('IOBA')
    expect(bankCode(null, 'Indian Bank')).toBe('IDIB')
    expect(bankCode(null, 'Some Co-op Bank')).toBeNull()
  })

  it('loads the symbol through the API, beside the bank name', async () => {
    const { container } = render(<BankMark ifsc="HDFC0001721" name="HDFC BANK" />)
    expect(screen.getByText('HDFC BANK')).toBeInTheDocument()
    await waitFor(() => expect(container.querySelector('.vc__banklogo img')).toHaveAttribute('src', 'blob:/api/bank-logos/HDFC.png'))
  })

  it('keeps the plain bank icon for a bank with no symbol, without asking for one', async () => {
    fetchImage.mockClear()
    const { container } = render(<BankMark ifsc="ZZZZ0001234" name="Local Co-op Bank" />)
    expect(screen.getByText('Local Co-op Bank')).toBeInTheDocument()
    await new Promise(r => setTimeout(r, 0))
    expect(container.querySelector('.vc__banklogo')).toBeNull()
    expect(fetchImage).not.toHaveBeenCalled()
  })
})
