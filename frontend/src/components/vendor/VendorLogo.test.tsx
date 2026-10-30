import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const api = vi.hoisted(() => ({
  fetchVendorLogos: vi.fn(),
  setVendorLogo: vi.fn(),
  removeVendorLogo: vi.fn(),
  fetchAuthedObjectUrl: vi.fn(async (u: string) => `blob:${u}`),
}))
vi.mock('../../api/vendorCard', async orig => ({
  ...(await orig<typeof import('../../api/vendorCard')>()),
  fetchVendorLogos: api.fetchVendorLogos, setVendorLogo: api.setVendorLogo, removeVendorLogo: api.removeVendorLogo,
}))
vi.mock('../../api/authedFiles', () => ({ fetchAuthedObjectUrl: api.fetchAuthedObjectUrl }))

import { LogoEditor, VendorLogo, __resetVendorLogos } from './VendorLogo'

beforeEach(() => {
  Object.values(api).forEach(f => f.mockClear())
  __resetVendorLogos()
})

describe('vendor logo', () => {
  it('shows the logo once found, initials otherwise', async () => {
    api.fetchVendorLogos.mockResolvedValue({ logos: { s1: 7 }, pending: 0 })
    const { container } = render(<>
      <VendorLogo supplierId="s1" className="vp__avatar" fallback="R" />
      <VendorLogo supplierId="s2" className="vp__avatar" fallback="J" />
    </>)
    await waitFor(() => expect(container.querySelector('img')).toHaveAttribute('src', 'blob:/api/vendors/s1/logo?v=7'))
    expect(container.querySelector('img')!.parentElement).toHaveClass('has-logo')
    expect(screen.getByText('J')).toBeInTheDocument()
    expect(screen.queryByText('R')).not.toBeInTheDocument()
    // One list for every logo on screen.
    expect(api.fetchVendorLogos).toHaveBeenCalledTimes(1)
  })

  it('takes a logo from the website you give, and can remove it', async () => {
    api.fetchVendorLogos.mockResolvedValue({ logos: {}, pending: 0 })
    api.setVendorLogo.mockResolvedValue({ version: 9, domain: 'acme.com' })
    api.removeVendorLogo.mockResolvedValue(undefined)
    const notify = vi.fn()
    const { container } = render(
      <VendorLogo supplierId="s1" className="vc__logo" fallback="AC"><LogoEditor supplierId="s1" notify={notify} /></VendorLogo>,
    )
    await waitFor(() => expect(api.fetchVendorLogos).toHaveBeenCalled())
    await userEvent.click(screen.getByRole('button', { name: 'Add logo' }))
    await userEvent.type(screen.getByLabelText('Website'), 'https://www.acme.com/about{Enter}')
    expect(api.setVendorLogo).toHaveBeenCalledWith('s1', 'https://www.acme.com/about')
    await waitFor(() => expect(container.querySelector('img')).toHaveAttribute('src', 'blob:/api/vendors/s1/logo?v=9'))
    expect(notify).toHaveBeenCalledWith('Logo from acme.com', 'info')

    await userEvent.click(screen.getByRole('button', { name: 'Change logo' }))
    await userEvent.click(screen.getByRole('button', { name: /Remove logo/ }))
    expect(api.removeVendorLogo).toHaveBeenCalledWith('s1')
    await waitFor(() => expect(container.querySelector('img')).toBeNull())
    expect(screen.getByText('AC')).toBeInTheDocument()
  })

  it('says why when the website has no logo', async () => {
    api.fetchVendorLogos.mockResolvedValue({ logos: {}, pending: 0 })
    api.setVendorLogo.mockRejectedValue({ response: { data: { detail: 'No logo found for nowhere.in.' } } })
    render(<VendorLogo supplierId="s1" className="vc__logo" fallback="AC"><LogoEditor supplierId="s1" /></VendorLogo>)
    await userEvent.click(screen.getByRole('button', { name: 'Add logo' }))
    await userEvent.type(screen.getByLabelText('Website'), 'nowhere.in{Enter}')
    expect(await screen.findByText('No logo found for nowhere.in.')).toBeInTheDocument()
  })
})
