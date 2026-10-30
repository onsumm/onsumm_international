import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'

// These tests type into the card like a user does, which is slow when the
// whole suite runs in parallel; the default 5s timeout makes them flaky.
vi.setConfig({ testTimeout: 20_000 })
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { fetchVendorBankAccounts, setPrimaryBankAccount, updateVendor, type Bill, type Vendor } from '../api/invoices'
import type { VendorInbox } from '../api/vendorCard'

/**
 * The vendor card as a whole, with the network mocked: what it shows on open,
 * and that each view and the old route tabs land where they should.
 */

vi.mock('react-pdf', () => ({ Document: () => null, Page: () => null, pdfjs: { GlobalWorkerOptions: {} } }))
// The signed-in role, switchable per test (bank and TDS changes are role-gated).
const auth = vi.hoisted(() => ({ role: 'admin' }))
vi.mock('../auth/AuthContext', () => ({
  useAuth: () => ({ user: { role: auth.role, company_id: 'c1', username: 'ananya', name: 'Ananya Rao' }, activeBrand: null, brands: [] }),
}))
vi.mock('../auth/EntitlementsContext', async () => {
  // An Indian tenant: every India rule pack on, books in INR — the card as it always was.
  const { INDIA_JURISDICTION } = await import('../auth/jurisdiction')
  return { useService: () => true, useJurisdiction: () => INDIA_JURISDICTION, useBaseCurrency: () => 'INR' }
})
// useService() is true for every service above, so the crypto rail's wallet
// panel renders on the Compliance view too. It is tested on its own; here it
// would only need a ToastProvider and live wallet endpoints.
vi.mock('../components/crypto/VendorCryptoWallets', () => ({ default: () => null }))

const isoDay = (offset: number) => {
  const d = new Date(); d.setDate(d.getDate() + offset)
  return d.toISOString().slice(0, 10)
}

function bill(p: Partial<Bill>): Bill {
  return {
    invoice_id: Math.random().toString(36).slice(2), id: 'SGL/0001', supplier_id: 'v1', invoice_date: isoDay(-40), due_date: isoDay(-10),
    status: 'APPROVED', total_amount: 50000, currency_code: 'INR', vendor_name: 'Shree Ganesh Logistics', vendor_gstn: null,
    original_file: null, upload_id: 'u1', stg_bill_id: null, created_at: null, uploaded_by_name: null, uploaded_by_role: null,
    category: 'Freight', department: null, payment_status: 'unpaid', amount_paid: null, outstanding_amount: 50000,
    payment_updated_by: null, payment_updated_at: null, tally_push_pending: false, manual_priority: null, soft_status: 'active',
    subtotal: null, tax_amount: null, tds_status: null, tds_section_code: null, tds_rate: null, tds_amount: null, tds_base_amount: null,
    net_payable: null, original_amount: null, inr_amount_current: null, brand_id: null, cost_center_id: null, cost_center_name: null,
    fx_agent_note: null, fx_agent_severity: null, ...p,
  }
}

const BILLS: Bill[] = [
  bill({ id: 'SGL/0445' }),
  bill({ id: 'SGL/0446', due_date: isoDay(12), invoice_date: isoDay(-18), outstanding_amount: 30000, total_amount: 30000 }),
  bill({ id: 'SGL/0400', invoice_date: isoDay(-100), payment_status: 'paid', outstanding_amount: 0, payment_updated_at: isoDay(-62) }),
  bill({ id: 'OTHER', supplier_id: 'v2', vendor_name: 'Someone else' }),
]

const INBOX: VendorInbox = {
  me: 'ananya@buyer.com', connected: true, read_available: true, error: null,
  addresses: ['priya@sgl.in'], domains: ['sgl.in'],
  threads: [{
    thread_id: 't1', subject: 'Revised rate card', snippet: 'Please find attached', first_at: isoDay(-2), last_at: isoDay(-2),
    message_count: 1, started_by: 'vendor', started_by_name: 'Priya Nair', last_from: 'Priya Nair', last_outbound: false,
    needs_reply: true, has_attachments: true, vendor_addresses: ['priya@sgl.in'], contact_id: 'p1', unknown_senders: [],
    status: 'needs_reply', counterparty: { email: 'priya@sgl.in', name: 'Priya Nair' },
    participants: [{ email: 'priya@sgl.in', name: 'Priya Nair', vendor: true, me: false }, { email: 'ananya@buyer.com', name: 'Ananya', vendor: false, me: true }],
  }],
}

vi.mock('../api/invoices', async orig => ({
  ...(await orig<typeof import('../api/invoices')>()),
  fetchBills: vi.fn(async () => BILLS),
  fetchVendor: vi.fn(),
  fetchVendorBankAccounts: vi.fn(async () => [{ id: 'b1', account_number: '000123454417', ifsc: 'HDFC0001234', bank_name: 'HDFC Bank', is_primary: true, verified: true }]),
  fetchVendorContacts: vi.fn(async () => [{ id: 'p1', supplier_id: 'v1', name: 'Priya Nair', role: 'Accounts', email: 'priya@sgl.in', phone: null, is_primary: true, created_at: null }]),
  fetchEmailConnection: vi.fn(async () => ({ connected: true, email: 'ananya@buyer.com' })),
  fetchSupplierGstins: vi.fn(async () => []),
  fetchVendorOverrides: vi.fn(async () => []),
  fetchTdsSections: vi.fn(async () => []),
  fetchFyState: vi.fn(async () => null),
  updateVendor: vi.fn(async () => {}),
  setPrimaryBankAccount: vi.fn(async () => []),
}))
const deleteDoc = vi.hoisted(() => vi.fn(async () => ({})))
vi.mock('../api/vendorCard', async orig => ({
  ...(await orig<typeof import('../api/vendorCard')>()),
  fetchVendorDocuments: vi.fn(async () => [
    { id: 'd1', file_name: 'MSA_2025.pdf', file_size: 20480, uploaded_by: 'Karan', uploaded_at: isoDay(-200), doc_type: 'contract', doc_label: null, claims_status: 'DONE' },
    { id: 'd2', file_name: 'Rate_card.pdf', file_size: 10240, uploaded_by: 'Karan', uploaded_at: isoDay(-5), doc_type: 'other', doc_label: 'Rate cards', claims_status: 'SKIPPED' },
  ]),
  fetchVendorInbox: vi.fn(async () => INBOX),
  fetchVendorLogos: vi.fn(async () => ({ logos: {}, pending: 0 })),
  fetchVendorInboxThread: vi.fn(async () => ({ me: 'ananya@buyer.com', messages: [] })),
  fetchDocumentTypes: vi.fn(async () => [{ label: 'Rate cards', documents: 1 }]),
  deleteVendorDocument: deleteDoc,
  fetchVendorActivity: vi.fn(async () => ({
    events: [{ id: 'a1', at: new Date().toISOString(), kind: 'change', action: 'vendor_updated', title: 'Payment terms changed', detail: null,
      actor: 'Ananya', actor_role: 'Finance', invoice_id: null, bill_no: null, amount: null, changes: [{ field: 'Payment terms', from: '30 days', to: '45 days' }] }],
    next_before: null, partial: false,
  })),
}))

const VENDOR: Vendor = {
  supplier_id: 'v1', name: 'Shree Ganesh Logistics', legal_name: 'Shree Ganesh Logistics Private Limited', gstn: '27AAKCS4821M1Z3',
  email: 'accounts@sgl.in', payment_terms_days: 30, status: 'VERIFIED', is_active: true, source: 'EMAIL', total_bills: 3,
  total_billed: 130000, outstanding_amount: 80000, last_invoice_date: isoDay(-18), created_at: '2025-03-12', bank_account: null,
  bank_ifsc: null, bank_verified: true, pan: null, vendor_category_id: null, vendor_category_name: null, brand_id: null,
}

async function open(tab: Parameters<typeof import('./VendorsPage')['VendorDetailPanel']>[0]['tab'] = 'details') {
  const { VendorDetailPanel } = await import('./VendorsPage')
  const onTabChange = vi.fn()
  const utils = render(
    <MemoryRouter>
      <VendorDetailPanel vendor={VENDOR} companyId="c1" tab={tab} onTabChange={onTabChange} onClose={() => {}} onNavigateToBill={() => {}} />
    </MemoryRouter>,
  )
  await screen.findByRole('dialog', { name: VENDOR.name })
  return { ...utils, onTabChange }
}

// VendorsPage is a large module (react-pdf, xlsx preview): load it once, outside
// any single test's time limit, so a busy parallel run doesn't time the first test out.
beforeAll(async () => { await import('./VendorsPage') }, 30_000)

beforeEach(() => {
  auth.role = 'admin'
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => [] })))
})

describe('vendor card', () => {
  it('opens on the overview with the vendor’s own bills only', async () => {
    await open()
    expect(screen.getByRole('dialog', { name: 'Shree Ganesh Logistics' })).toBeInTheDocument()
    expect(await screen.findByText('Open bills')).toBeInTheDocument()
    expect(screen.getAllByText('SGL/0445').length).toBeGreaterThan(0)
    expect(screen.queryByText('OTHER')).not.toBeInTheDocument()
    // ₹50,000 overdue + ₹30,000 due later
    expect(screen.getAllByText('₹80,000').length).toBeGreaterThan(0)
  })

  it('has no Copilot banner on the overview', async () => {
    await open()
    expect(await screen.findByText('Open bills')).toBeInTheDocument()
    expect(screen.queryByText('Onsumm Copilot')).not.toBeInTheDocument()
  })

  it('switches views through the route tab', async () => {
    const { onTabChange } = await open()
    await userEvent.click(screen.getByRole('tab', { name: /Spend/ }))
    expect(onTabChange).toHaveBeenCalledWith('spend')
    await userEvent.click(screen.getByRole('tab', { name: /Overview/ }))
    expect(onTabChange).toHaveBeenCalledWith('details')
  })

  it('files documents by type, with bills built in', async () => {
    await open('documents')
    const rail = await screen.findByRole('navigation', { name: 'Document types' })
    expect(within(rail).getByText('Bills')).toBeInTheDocument()
    expect(within(rail).getByText('Contracts & IOs')).toBeInTheDocument()
    expect(within(rail).getByText('Rate cards')).toBeInTheDocument()
  })

  it('opens the old statements link on Documents › Bills', async () => {
    await open('statements')
    expect(await screen.findByTestId('bills-summary')).toHaveTextContent('2 unpaid · ₹80,000')
  })

  it('lists threads the vendor started', async () => {
    await open('conversations')
    expect(await screen.findAllByText('Revised rate card')).not.toHaveLength(0)
    expect(screen.getAllByText('They started').length).toBeGreaterThan(0)
  })

  it('has an Activity tab between Conversations and Compliance that opens the timeline', async () => {
    const { onTabChange } = await open()
    const names = screen.getAllByRole('tab').map(t => t.textContent ?? '')
    const i = names.findIndex(n => n.startsWith('Activity'))
    expect(names[i - 1]).toMatch(/^Conversations/)
    expect(names[i + 1]).toMatch(/^Compliance/)
    await userEvent.click(screen.getByRole('tab', { name: /Activity/ }))
    expect(onTabChange).toHaveBeenCalledWith('activity')
  })

  it('shows the timeline on the activity route', async () => {
    await open('activity')
    expect(await screen.findByText('Payment terms changed')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Today' })).toBeInTheDocument()
  })

  it('folds sidebar sections and remembers it, keeping their anchors', async () => {
    const { container } = await open()
    const toggle = screen.getByRole('button', { name: 'Vendor details' })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await userEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(localStorage.getItem('vc.side.details')).toBe('0')
    expect(container.ownerDocument.querySelector('[data-fix="gstin"]')).not.toBeNull()
    await userEvent.click(toggle)
    expect(localStorage.getItem('vc.side.details')).toBe('1')
  })

  it('keeps the field anchors Review points at', async () => {
    const { container } = await open()
    for (const f of ['name', 'gstin', 'pan', 'payment_terms']) {
      const el = container.ownerDocument.querySelector(`[data-fix="${f}"]`)
      expect(el, f).not.toBeNull()
      expect(el!.querySelector(':scope > .vd__field-lbl'), f).not.toBeNull()
    }
    expect(container.ownerDocument.querySelector('[data-guide="details.pan"]')).not.toBeNull()
  })
})

describe('deleting a document', () => {
  it('removes it at once and Undo keeps it, without asking the server', async () => {
    deleteDoc.mockClear()
    await open('documents')
    const rail = await screen.findByRole('navigation', { name: 'Document types' })
    await userEvent.click(within(rail).getByText('Contracts & IOs'))
    await userEvent.click(await screen.findByRole('button', { name: 'Delete MSA_2025.pdf' }))
    expect(screen.queryByRole('button', { name: 'Delete MSA_2025.pdf' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }))
    expect(await screen.findByRole('button', { name: 'Delete MSA_2025.pdf' })).toBeInTheDocument()
    expect(deleteDoc).not.toHaveBeenCalled()
  })
})

describe('vendor card compliance', () => {
  it('shows GSTINs, bank accounts and TDS together', async () => {
    await open('compliance')
    expect(await screen.findByText('GST registrations')).toBeInTheDocument()
    expect(screen.getByText('Bank accounts')).toBeInTheDocument()
    expect(screen.getByText('TDS')).toBeInTheDocument()
    // Laid out as tiles in one grid: GST, bank, TDS, plus the crypto wallets
    // tile, since every service (crypto_payouts included) is licensed here.
    expect(document.querySelectorAll('.vc__cgrid > .vc__ctile')).toHaveLength(4)
  })
})

describe('editing vendor details', () => {
  it('edits in place: same rows, boxes where the values were, verified ones left alone', async () => {
    await open()
    const side = screen.getByRole('complementary', { name: 'Vendor details' })
    const rowsBefore = [...side.querySelectorAll('.vc__kvs > .vc__kv .vd__field-lbl')].map(e => e.textContent)
    await userEvent.click(within(side).getByRole('button', { name: 'Edit vendor details' }))
    const rowsAfter = [...side.querySelectorAll('.vc__kvs > .vc__kv .vd__field-lbl')].map(e => e.textContent)
    expect(rowsAfter).toEqual(rowsBefore)
    expect(within(side).getByLabelText('Brand name')).toHaveValue('Shree Ganesh Logistics')
    expect(within(side).getByLabelText('Email')).toHaveValue('accounts@sgl.in')
    expect(within(side).getByLabelText('Payment terms (days)')).toHaveValue(30)
    // A verified vendor's GSTIN, the PAN inside it and the GST legal name stay as they are.
    expect(within(side).queryByLabelText('PAN')).not.toBeInTheDocument()
    expect(within(side).queryByLabelText('Legal name')).not.toBeInTheDocument()
    expect(within(side).getAllByLabelText('Verified').length).toBe(3)
    await userEvent.click(within(side).getByRole('button', { name: 'Cancel' }))
    expect(within(side).queryByLabelText('Brand name')).not.toBeInTheDocument()
  })
})

describe('payment terms (D-H1)', () => {
  it('saves 0 days as 0, not 30', async () => {
    vi.mocked(updateVendor).mockClear()
    await open()
    const side = screen.getByRole('complementary', { name: 'Vendor details' })
    await userEvent.click(within(side).getByRole('button', { name: 'Edit vendor details' }))
    const terms = within(side).getByLabelText('Payment terms (days)')
    await userEvent.clear(terms)
    await userEvent.type(terms, '0')
    await userEvent.click(within(side).getByRole('button', { name: 'Save' }))
    expect(updateVendor).toHaveBeenCalledWith('v1', expect.objectContaining({ payment_terms_days: 0 }), 'c1')
  })

  it('refuses a blank term with a message instead of saving 30', async () => {
    vi.mocked(updateVendor).mockClear()
    await open()
    const side = screen.getByRole('complementary', { name: 'Vendor details' })
    await userEvent.click(within(side).getByRole('button', { name: 'Edit vendor details' }))
    await userEvent.clear(within(side).getByLabelText('Payment terms (days)'))
    await userEvent.click(within(side).getByRole('button', { name: 'Save' }))
    expect(within(side).getByRole('alert')).toHaveTextContent(/payment terms/i)
    expect(updateVendor).not.toHaveBeenCalled()
  })
})

describe('bank accounts on Compliance', () => {
  const TWO = [
    { id: 'b1', account_number: '000123454417', ifsc: 'HDFC0001234', bank_name: 'HDFC Bank', is_primary: true, verified: true },
    { id: 'b2', account_number: '998877669876', ifsc: 'ICIC0004321', bank_name: 'ICICI Bank', is_primary: false, verified: false },
  ]

  it('masks the account number until Show is pressed (K-H2)', async () => {
    await open('compliance')
    const tile = await screen.findByText('Bank accounts').then(el => el.closest('section') as HTMLElement)
    await within(tile).findByText('•••• •••• 4417')
    expect(within(tile).queryByText('000123454417')).not.toBeInTheDocument()
    await userEvent.click(within(tile).getByRole('button', { name: 'Show account number' }))
    expect(within(tile).getByText('000123454417')).toBeInTheDocument()
  })

  it('confirms a primary change, naming the old and new accounts (K-H1)', async () => {
    vi.mocked(fetchVendorBankAccounts).mockResolvedValueOnce(TWO as never)
    vi.mocked(setPrimaryBankAccount).mockClear()
    await open('compliance')
    await userEvent.click(await screen.findByRole('button', { name: 'Set as primary' }))
    const dlg = await screen.findByRole('dialog', { name: 'Change the primary bank account?' })
    expect(within(dlg).getByText('HDFC Bank •••• 4417')).toBeInTheDocument()
    expect(within(dlg).getAllByText('ICICI Bank •••• 9876').length).toBeGreaterThan(0)
    // Nothing changes until it's confirmed.
    expect(setPrimaryBankAccount).not.toHaveBeenCalled()
    await userEvent.click(within(dlg).getByRole('button', { name: 'Make primary' }))
    expect(setPrimaryBankAccount).toHaveBeenCalledWith('v1', 'b2', 'c1')
  })

  it('hides add, delete and set-primary from roles that can’t edit (K-H1)', async () => {
    auth.role = 'viewer'
    vi.mocked(fetchVendorBankAccounts).mockResolvedValueOnce(TWO as never)
    await open('compliance')
    const tile = await screen.findByText('Bank accounts').then(el => el.closest('section') as HTMLElement)
    await within(tile).findByText('•••• •••• 9876')
    expect(within(tile).queryByRole('button', { name: 'Set as primary' })).not.toBeInTheDocument()
    expect(within(tile).queryByRole('button', { name: /Delete bank account/ })).not.toBeInTheDocument()
    expect(within(tile).queryByRole('button', { name: /Add/ })).not.toBeInTheDocument()
  })
})

describe('closing the card', () => {
  it('asks before dropping unsaved edits (S-H1)', async () => {
    const { VendorDetailPanel } = await import('./VendorsPage')
    const onClose = vi.fn()
    render(
      <MemoryRouter>
        <VendorDetailPanel vendor={VENDOR} companyId="c1" tab="details" onTabChange={() => {}} onClose={onClose} />
      </MemoryRouter>,
    )
    const side = await screen.findByRole('complementary', { name: 'Vendor details' })
    await userEvent.click(within(side).getByRole('button', { name: 'Edit vendor details' }))
    await userEvent.type(within(side).getByLabelText('Brand name'), ' Corp')
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).not.toHaveBeenCalled()
    const dlg = await screen.findByRole('dialog', { name: 'Discard your changes?' })
    await userEvent.click(within(dlg).getByRole('button', { name: 'Discard changes' }))
    expect(onClose).toHaveBeenCalled()
  })
})
