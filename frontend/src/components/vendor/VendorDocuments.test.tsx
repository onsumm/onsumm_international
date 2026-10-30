import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const api = vi.hoisted(() => ({
  classifyVendorDocument: vi.fn(),
  uploadVendorDocument: vi.fn(async (..._args: unknown[]) => ({ id: 'd1', file_name: 'x.pdf' })),
}))
vi.mock('../../api/vendorCard', async orig => ({
  ...(await orig<typeof import('../../api/vendorCard')>()),
  fetchDocumentTypes: vi.fn(async () => [{ label: 'Rate cards', documents: 2 }]),
  ...api,
}))

import VendorDocuments, { type BillFilter } from './VendorDocuments'
import type { Bill } from '../../api/invoices'

const onUploadBill = vi.fn()
function open(uploadRequest = 0) {
  return render(
    // The views ease in with a transform; the dialog must not be measured from one.
    <div className="vc__fadein" style={{ transform: 'translateY(4px)' }}>
      <VendorDocuments vendorName="Signzy Technologies" supplierId="s1" bills={[]} docs={[]} now={new Date('2026-10-13')}
        folder="recent" onFolder={() => {}} billFilter="all" onBillFilter={() => {}} currentUser="Sumit" onReload={() => {}}
        onPreviewDoc={() => {}} onDownloadDoc={() => {}} onDeleteDoc={() => {}} onPreviewBill={() => {}} onOpenBill={() => {}}
        onUploadBill={onUploadBill} notify={() => {}} uploadRequest={uploadRequest} />
    </div>,
  )
}

describe('add a document dialog', () => {
  it('opens over the whole page, not inside the view', async () => {
    const { container } = open(1)
    const dialog = await screen.findByRole('dialog', { name: 'Add a document' })
    const layer = dialog.closest('.vc__mov') as HTMLElement
    expect(layer.parentElement).toBe(document.body)
    expect(container.contains(layer)).toBe(false)
  })

  it('closes on Escape and on the backdrop', async () => {
    const { rerender } = open(1)
    await screen.findByRole('dialog', { name: 'Add a document' })
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    rerender(
      <div className="vc__fadein">
        <VendorDocuments vendorName="Signzy Technologies" supplierId="s1" bills={[]} docs={[]} now={new Date('2026-10-13')}
          folder="recent" onFolder={() => {}} billFilter="all" onBillFilter={() => {}} currentUser="Sumit" onReload={() => {}}
          onPreviewDoc={() => {}} onDownloadDoc={() => {}} onDeleteDoc={() => {}} onPreviewBill={() => {}} onOpenBill={() => {}}
          onUploadBill={() => {}} notify={() => {}} uploadRequest={2} />
      </div>,
    )
    const dialog = await screen.findByRole('dialog', { name: 'Add a document' })
    await userEvent.pointer({ keys: '[MouseLeft]', target: dialog.closest('.vc__mov') as HTMLElement })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })
})

describe('filing a document by what it says', () => {
  const drop = async (verdict: object, name = 'doc.pdf') => {
    api.classifyVendorDocument.mockResolvedValueOnce({ title: '', reason: '', source: 'ai', ...verdict })
    open(1)
    const dialog = await screen.findByRole('dialog', { name: 'Add a document' })
    const input = dialog.querySelector('input[type=file]') as HTMLInputElement
    await userEvent.upload(input, new File(['%PDF'], name, { type: 'application/pdf' }))
    return dialog
  }

  it('files a contract under Contracts & IOs and uploads it to be checked', async () => {
    const dialog = await drop({ kind: 'contract', title: 'Insertion order, Oct 2026', reason: 'Between Acme and Signzy.' })
    expect(await within(dialog).findByText('Contracts & IOs')).toBeInTheDocument()
    expect(screen.getByText('Insertion order, Oct 2026 · Between Acme and Signzy.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Upload and check' }))
    await waitFor(() => expect(api.uploadVendorDocument).toHaveBeenCalled())
    expect(api.uploadVendorDocument.mock.calls[0][2]).toMatchObject({ docType: 'contract', docLabel: null })
  })

  it('names a new type for something new, editable', async () => {
    await drop({ kind: 'new', suggested_name: 'MSME certificates' })
    const name = await screen.findByLabelText('Type name')
    expect(name).toHaveValue('MSME certificates')
    await userEvent.clear(name)
    await userEvent.type(name, 'Udyam certificates')
    await userEvent.click(screen.getByRole('button', { name: 'Create “Udyam certificates” and upload' }))
    await waitFor(() => expect(api.uploadVendorDocument).toHaveBeenCalled())
    expect(api.uploadVendorDocument.mock.calls.at(-1)![2]).toMatchObject({ docType: 'other', docLabel: 'Udyam certificates' })
  })

  it('can be changed to one of the team’s types', async () => {
    await drop({ kind: 'new', suggested_name: 'Price list' })
    await userEvent.click(await screen.findByRole('button', { name: 'Change' }))
    await userEvent.click(within(screen.getByRole('group', { name: 'File it under' })).getByRole('button', { name: 'Rate cards' }))
    expect(screen.getByRole('button', { name: 'Upload to Rate cards' })).toBeEnabled()
  })

  it('when Jev isn’t sure, says so and opens the choices', async () => {
    await drop({ kind: 'contract', confidence: 0.32, sure: false, engine: 'jev' })
    expect(await screen.findByText('Not sure. Check this')).toBeInTheDocument()
    expect(screen.getByText('32% sure')).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'File it under' })).toBeInTheDocument()
  })

  it('sends a bill to bill upload instead', async () => {
    onUploadBill.mockClear()
    await drop({ kind: 'bill', reason: 'Tax invoice SGL/0445.' })
    expect(await screen.findByText('This looks like a bill')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Upload' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: /Go to bill upload/ }))
    expect(onUploadBill).toHaveBeenCalled()
  })
})

describe('opening Documents', () => {
  it('does not open the upload dialog by itself', async () => {
    open(0)
    await screen.findByRole('navigation', { name: 'Document types' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

describe('the bills folder', () => {
  const NOW = new Date('2026-10-22T12:00:00')
  const bill = (p: Partial<Bill>): Bill => ({
    invoice_id: Math.random().toString(36).slice(2), id: 'CMPL/001', invoice_date: '2026-06-03', due_date: '2026-06-03',
    status: 'PENDING_APPROVAL', total_amount: 10000, payment_status: 'unpaid', outstanding_amount: null,
    category: 'Lead Partner Generation', upload_id: 'u1', soft_status: 'active', ...p,
  } as Bill)
  const show = (bills: Bill[], billFilter: BillFilter = 'all', onBillFilter = vi.fn()) => render(
    <VendorDocuments vendorName="Cheq" supplierId="s1" bills={bills} docs={[]} now={NOW}
      folder="bills" onFolder={() => {}} billFilter={billFilter} onBillFilter={onBillFilter} currentUser="Sumit" onReload={() => {}}
      onPreviewDoc={() => {}} onDownloadDoc={() => {}} onDeleteDoc={() => {}} onPreviewBill={() => {}} onOpenBill={() => {}}
      onUploadBill={() => {}} notify={() => {}} />,
  )
  const unpaidFive = () => [
    bill({ id: 'A', invoice_date: '2026-04-01', due_date: '2026-04-01' }),
    bill({ id: 'B', invoice_date: '2026-05-05', due_date: '2026-05-05' }),
    bill({ id: 'C', invoice_date: '2026-06-03', due_date: '2026-07-02', status: 'APPROVED' }),
    bill({ id: 'D', invoice_date: '2026-06-03', due_date: '2026-06-03' }),
    bill({ id: 'E', invoice_date: '2026-07-03', due_date: '2026-07-03' }),
  ]

  it('sums up what is owed and who is holding it up', () => {
    show(unpaidFive())
    expect(screen.getByTestId('bills-summary')).toHaveTextContent('5 unpaid · ₹50,000 · oldest 204d late · 4 waiting on approval')
  })

  it('says a shared category once and drops columns that repeat others', () => {
    const { container } = show(unpaidFive())
    expect(screen.getAllByText('Lead Partner Generation')).toHaveLength(1)
    const heads = [...container.querySelectorAll('thead th')].map(th => th.textContent)
    expect(heads).toEqual(['Bill', 'Due', 'Status', 'Amount', ''])
  })

  it('tells a late bill in approval apart from an overdue payment', () => {
    show(unpaidFive())
    expect(screen.getByText('In approval · 204d late')).toBeInTheDocument()
    expect(screen.getByText('Overdue 112d')).toBeInTheDocument()
  })

  it('hides filters that would show the same bills as All', () => {
    show(unpaidFive())
    const seg = screen.getByRole('group', { name: 'Filter bills' })
    // C is the only approved late bill, so Overdue is its own filter; the late ones in approval are just In approval.
    expect(within(seg).getAllByRole('button').map(b => b.firstChild?.textContent)).toEqual(['All', 'Overdue', 'In approval'])
  })

  it('lists a short statement flat and groups a long one by month', () => {
    const { container, unmount } = show(unpaidFive())
    expect(container.querySelectorAll('.vc__grp')).toHaveLength(0)
    unmount()
    const many = Array.from({ length: 9 }, (_, i) => bill({ id: `M${i}`, invoice_date: `2026-0${4 + (i % 3)}-1${i}` }))
    const r = show(many)
    expect(r.container.querySelectorAll('.vc__grp')).toHaveLength(3)
  })

  it('adds an Outstanding column only when a bill is part paid', () => {
    const { container } = show([...unpaidFive(), bill({ id: 'P', payment_status: 'partial', outstanding_amount: 4000 })])
    expect([...container.querySelectorAll('thead th')].map(th => th.textContent)).toContain('Outstanding')
  })
})

describe('bills folder fixes', () => {
  const NOW = new Date('2026-10-22T12:00:00')
  const bill = (p: Partial<Bill>): Bill => ({
    invoice_id: Math.random().toString(36).slice(2), id: 'CMPL/001', invoice_date: '2026-06-03', due_date: '2026-06-03',
    status: 'APPROVED', total_amount: 10000, currency_code: 'INR', payment_status: 'unpaid', outstanding_amount: null,
    category: null, upload_id: 'u1', soft_status: 'active', ...p,
  } as Bill)
  const show = (bills: Bill[], extra: Partial<Parameters<typeof VendorDocuments>[0]> = {}) => render(
    <VendorDocuments vendorName="Cheq" supplierId="s1" bills={bills} docs={[]} now={NOW}
      folder="bills" onFolder={() => {}} billFilter="all" onBillFilter={() => {}} currentUser="Sumit" onReload={() => {}}
      onPreviewDoc={() => {}} onDownloadDoc={() => {}} onDeleteDoc={() => {}} onPreviewBill={() => {}} onOpenBill={() => {}}
      onUploadBill={() => {}} notify={() => {}} {...extra} />,
  )

  it('shows a foreign bill in its own currency and leaves it out of the ₹ totals (B-H1)', () => {
    show([bill({ id: 'USD-1', currency_code: 'USD', total_amount: 5000 }), bill({ id: 'INR-1' })])
    expect(screen.getByText('USD 5,000 · not converted')).toBeInTheDocument()
    expect(screen.getByTestId('bills-summary')).toHaveTextContent('2 unpaid · ₹10,000')
    expect(screen.getByTestId('bills-fx-note')).toHaveTextContent('1 bill in USD not converted to ₹, so not in the totals')
  })

  it('finds a bill by its amount as displayed (B-M1)', async () => {
    show([bill({ id: 'BIG', total_amount: 125000 }), bill({ id: 'SMALL', total_amount: 900 })])
    await userEvent.type(screen.getByLabelText('Search bills'), '₹1,25,000')
    expect(screen.getByText('BIG')).toBeInTheDocument()
    expect(screen.queryByText('SMALL')).not.toBeInTheDocument()
  })

  it('opens a bill from its number by keyboard (B-M2)', async () => {
    const onOpenBill = vi.fn()
    show([bill({ id: 'KB-1', invoice_id: 'inv-kb' })], { onOpenBill })
    screen.getByRole('button', { name: 'KB-1' }).focus()
    await userEvent.keyboard('{Enter}')
    expect(onOpenBill).toHaveBeenCalledWith('inv-kb')
  })

  it('filters Overdue to approved late bills only (B-M5)', () => {
    show([bill({ id: 'LATE-OK' }), bill({ id: 'LATE-HELD', status: 'PENDING_APPROVAL' })], { billFilter: 'overdue' })
    expect(screen.getByText('LATE-OK')).toBeInTheDocument()
    expect(screen.queryByText('LATE-HELD')).not.toBeInTheDocument()
  })

  it('never says "No bills" while loading or after a failed load (B-H2)', async () => {
    const { unmount } = show([], { billsStatus: 'loading' })
    expect(screen.getByRole('status')).toHaveAttribute('aria-busy', 'true')
    expect(screen.queryByText(/No bills/)).not.toBeInTheDocument()
    unmount()
    const onRetry = vi.fn()
    show([], { billsStatus: 'error', onRetry })
    expect(screen.getByRole('alert')).toHaveTextContent('Couldn’t load bills')
    expect(screen.queryByText(/No bills/)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalled()
  })

  it('says contracts failed to load rather than "No contracts" (B-H2)', () => {
    show([], { folder: 'contracts', docsStatus: 'error' })
    expect(screen.getByRole('alert')).toHaveTextContent('Couldn’t load documents')
    expect(screen.queryByText('No contracts yet.')).not.toBeInTheDocument()
  })
})

describe('adding from a folder keeps that folder (B-H3)', () => {
  it('files an upload from Contracts as a contract, and the read only suggests', async () => {
    api.uploadVendorDocument.mockClear()
    api.classifyVendorDocument.mockResolvedValueOnce({ kind: 'existing', label: 'Rate cards', title: '', reason: '', source: 'ai' })
    render(
      <VendorDocuments vendorName="Signzy" supplierId="s1" bills={[]} docs={[]} now={new Date('2026-10-13')}
        folder="contracts" onFolder={() => {}} billFilter="all" onBillFilter={() => {}} currentUser="Sumit" onReload={() => {}}
        onPreviewDoc={() => {}} onDownloadDoc={() => {}} onDeleteDoc={() => {}} onPreviewBill={() => {}} onOpenBill={() => {}}
        onUploadBill={() => {}} notify={() => {}} uploadRequest={1} />,
    )
    const dialog = await screen.findByRole('dialog', { name: 'Add a document' })
    await userEvent.upload(dialog.querySelector('input[type=file]') as HTMLInputElement, new File(['%PDF'], 'msa.pdf', { type: 'application/pdf' }))
    expect(await within(dialog).findByTestId('filing-suggestion')).toHaveTextContent('It reads like Rate cards.')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Upload and check' }))
    await waitFor(() => expect(api.uploadVendorDocument).toHaveBeenCalled())
    expect(api.uploadVendorDocument.mock.calls.at(-1)![2]).toMatchObject({ docType: 'contract', docLabel: null })
  })

  it('turns away a file it can’t take', async () => {
    open(1)
    const dialog = await screen.findByRole('dialog', { name: 'Add a document' })
    const input = dialog.querySelector('input[type=file]') as HTMLInputElement
    expect(input.accept).toContain('.pdf')
    await userEvent.upload(input, new File(['x'], 'tool.exe', { type: 'application/octet-stream' }), { applyAccept: false })
    expect(within(dialog).getByRole('alert')).toHaveTextContent('tool.exe isn’t a PDF, Excel, Word or image file.')
  })
})
