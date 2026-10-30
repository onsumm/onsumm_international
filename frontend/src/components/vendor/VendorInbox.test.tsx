import { describe, it, expect, vi, beforeEach } from 'vitest'

// These tests type into the card like a user does, which is slow when the
// whole suite runs in parallel; the default 5s timeout makes them flaky.
vi.setConfig({ testTimeout: 20_000 })
import { render, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Bill, VendorContact } from '../../api/invoices'
import type { InboxThread, VendorInbox as Inbox } from '../../api/vendorCard'

const api = vi.hoisted(() => ({
  fetchVendorInboxThread: vi.fn(),
  sendInboxEmail: vi.fn(),
  setThreadDone: vi.fn(),
  draftInboxEmail: vi.fn(),
  saveInboxAttachment: vi.fn(),
  fetchDocumentTypes: vi.fn(async () => [{ label: 'Rate cards', documents: 2 }]),
}))
vi.mock('../../api/vendorCard', async orig => ({ ...(await orig<typeof import('../../api/vendorCard')>()), ...api }))
vi.mock('react-pdf', () => ({ pdfjs: { GlobalWorkerOptions: {} }, Page: () => null, Document: () => null }))

import VendorInbox from './VendorInbox'

const ME = 'dhurandhar@onsumm.ai'

function thread(p: Partial<InboxThread>): InboxThread {
  return {
    thread_id: 't1', subject: 'test', snippet: 'nice man', first_at: '2026-10-11T10:00:00Z', last_at: '2026-10-11T10:28:00Z',
    message_count: 2, started_by: 'vendor', started_by_name: 'ME22B2044 SUBHASH', last_from: 'dhurandhar', last_outbound: true,
    needs_reply: false, has_attachments: false, vendor_addresses: ['me22b2044@iiitdm.ac.in'], contact_id: 'c1', unknown_senders: [],
    status: 'waiting', counterparty: { email: 'me22b2044@iiitdm.ac.in', name: 'ME22B2044 SUBHASH' },
    participants: [
      { email: 'me22b2044@iiitdm.ac.in', name: 'ME22B2044 SUBHASH', vendor: true, me: false },
      { email: 'ops@iiitdm.ac.in', name: 'Ops', vendor: true, me: false },
      { email: ME, name: '', vendor: false, me: true },
    ],
    ...p,
  }
}

const INBOX: Inbox = {
  me: ME, connected: true, read_available: true, error: null, addresses: ['me22b2044@iiitdm.ac.in'], domains: [],
  threads: [
    thread({}),
    thread({ thread_id: 't2', subject: 'Invoice question', snippet: 'When will INV-1042 be paid?', status: 'needs_reply', needs_reply: true, last_outbound: false, message_count: 1 }),
  ],
}

const CONTACTS: VendorContact[] = [{ id: 'c1', name: 'Subhash', role: 'AR', email: 'me22b2044@iiitdm.ac.in', phone: null, is_primary: true, created_at: null }]
const BILLS = [{
  invoice_id: 'inv-uuid', id: 'INV-1042', status: 'APPROVED', payment_status: 'unpaid', total_amount: 132000,
  outstanding_amount: 132000, due_date: '2026-05-15', invoice_date: '2026-04-15', soft_status: 'active',
}] as Bill[]

function open(props: Partial<Parameters<typeof VendorInbox>[0]> = {}) {
  const onActiveThread = vi.fn()
  const onOpenBill = vi.fn()
  const r = render(
    <VendorInbox supplierId="s1" inbox={INBOX} loading={false} emailConn={{ connected: true, email: ME }} connecting={false}
      contacts={CONTACTS} activeThreadId="t2" onActiveThread={onActiveThread} onReload={() => {}} onConnect={() => {}}
      onAddContact={() => {}} attachFiles={[]} setAttachFiles={() => {}} onPickBills={() => {}} notify={() => {}}
      bills={BILLS} onOpenBill={onOpenBill} {...props} />,
  )
  return { ...r, onActiveThread, onOpenBill }
}

beforeEach(() => {
  Object.values(api).forEach(f => f.mockClear())
  api.fetchVendorInboxThread.mockResolvedValue({
    me: ME,
    messages: [{
      id: 'm1', outbound: false, from: 'ME22B2044 SUBHASH <me22b2044@iiitdm.ac.in>', to: ME, cc: 'Ops <ops@iiitdm.ac.in>',
      date: '', internal_date: Date.parse('2026-10-11T10:00:00Z'), subject: 'Invoice question',
      body: 'When will INV-1042 be paid? See https://example.com/soa', attachments: [],
    }],
  })
  try { localStorage.clear() } catch { /* ok */ }
})

describe('vendor inbox', () => {
  it('names the vendor person on each thread, never our own address', async () => {
    open()
    const list = screen.getByText('Waiting on them', { selector: 'button' }).closest('.vc__mail-list') as HTMLElement
    expect(within(list).getAllByText('ME22B2044 SUBHASH').length).toBeGreaterThan(0)
    expect(within(list).queryByText(ME)).not.toBeInTheDocument()
    expect(within(list).getByText('You:')).toBeInTheDocument()
  })

  it('filters by status and searches', async () => {
    const { container } = open()
    const filters = within(container.querySelector('.vc__mail-filters') as HTMLElement)
    await userEvent.click(filters.getByRole('button', { name: /Needs reply/ }))
    expect(screen.getAllByText('Invoice question').length).toBeGreaterThan(0)
    await userEvent.click(filters.getByRole('button', { name: /^Done/ }))
    expect(screen.getByText('Nothing here.')).toBeInTheDocument()
    await userEvent.click(filters.getByRole('button', { name: /^All/ }))
    await userEvent.type(screen.getByPlaceholderText('Search conversations'), 'zzz')
    expect(screen.getByText('No conversations match.')).toBeInTheDocument()
  })

  it('links bill numbers in an email to the bill', async () => {
    const { onOpenBill } = open()
    await userEvent.click(await screen.findByTitle('Open bill'))
    expect(onOpenBill).toHaveBeenCalledWith('inv-uuid')
    expect(screen.getByRole('link', { name: 'https://example.com/soa' })).toHaveAttribute('target', '_blank')
  })

  it('reply all addresses the sender and copies everyone else, then sends', async () => {
    api.sendInboxEmail.mockResolvedValue({ thread_id: 't2', to: ['me22b2044@iiitdm.ac.in'], cc: ['ops@iiitdm.ac.in'], from: ME })
    const { container } = open()
    await screen.findByText(/to you/)
    await userEvent.click(within(container.querySelector('.vc__cmodes') as HTMLElement).getByRole('button', { name: /Reply all/ }))
    const to = screen.getByLabelText('To').closest('.vc__rcpt') as HTMLElement
    const cc = screen.getByLabelText('Cc').closest('.vc__rcpt') as HTMLElement
    expect(within(to).getByText('me22b2044@iiitdm.ac.in')).toBeInTheDocument()
    expect(within(cc).getByText('ops@iiitdm.ac.in')).toBeInTheDocument()
    await userEvent.type(screen.getByPlaceholderText('Write a reply…'), 'Paying on Friday.')
    await userEvent.click(screen.getByRole('button', { name: /^Send$/ }))
    await waitFor(() => expect(api.sendInboxEmail).toHaveBeenCalled())
    expect(api.sendInboxEmail.mock.calls[0][1]).toMatchObject({
      to: ['me22b2044@iiitdm.ac.in'], cc: ['ops@iiitdm.ac.in'], body: 'Paying on Friday.', threadId: 't2',
    })
    expect(await screen.findByText('Paying on Friday.')).toBeInTheDocument()
  })

  it('forwards with a note on top and the original quoted below', async () => {
    api.sendInboxEmail.mockResolvedValue({ thread_id: 't9', to: ['finance@buyer.com'], cc: [], from: ME })
    const { container } = open()
    await screen.findByText(/to you/)
    await userEvent.click(within(container.querySelector('.vc__cmodes') as HTMLElement).getByRole('button', { name: /Forward/ }))
    expect(screen.getByPlaceholderText('Add a note…')).toHaveValue('')
    const quote = screen.getByLabelText('Forwarded message')
    expect(within(quote).getByText('ME22B2044 SUBHASH')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Fwd: Invoice question')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('To'), 'finance@buyer.com{Enter}')
    await userEvent.type(screen.getByPlaceholderText('Add a note…'), 'Can you check this?')
    await userEvent.click(screen.getByRole('button', { name: /^Send$/ }))
    await waitFor(() => expect(api.sendInboxEmail).toHaveBeenCalled())
    const sent = api.sendInboxEmail.mock.calls[0][1]
    expect(sent).toMatchObject({ to: ['finance@buyer.com'], subject: 'Fwd: Invoice question', threadId: null })
    expect(sent.body).toMatch(/^Can you check this\?\n\n---------- Forwarded message ----------\nFrom: ME22B2044 SUBHASH/)
    expect(sent.body).toContain('When will INV-1042 be paid?')
  })

  it('suggests what to say about an attached bill, and drafts with it', async () => {
    api.draftInboxEmail.mockResolvedValue('Hi, INV-1042 will be paid on Friday.')
    open({ attachFiles: [new File(['%PDF'], 'Bill_INV-1042.pdf')], billFiles: { 'Bill_INV-1042.pdf': 'inv-uuid' } })
    expect(screen.getByText(/INV-1042 · ₹1,32,000/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Write with AI/ }))
    const grp = screen.getByRole('group', { name: 'About INV-1042' })
    await userEvent.click(within(grp).getByRole('menuitem', { name: /Tell them when INV-1042 .* will be paid/ }))
    await waitFor(() => expect(api.draftInboxEmail).toHaveBeenCalled())
    expect(api.draftInboxEmail.mock.calls[0][1]).toMatchObject({ attachedBillIds: ['inv-uuid'], billIds: ['inv-uuid'] })
    expect(await screen.findByDisplayValue('Hi, INV-1042 will be paid on Friday.')).toBeInTheDocument()
  })

  it('keeps an empty reply folded to one line until it is wanted, and folds back after sending', async () => {
    api.sendInboxEmail.mockResolvedValue({ thread_id: 't2', to: ['me22b2044@iiitdm.ac.in'], cc: [], from: ME })
    open()
    await screen.findByText(/to you/)
    expect(screen.queryByPlaceholderText('Write a reply…')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Reply to ME22B2044 SUBHASH/ }))
    const box = screen.getByPlaceholderText('Write a reply…')
    await waitFor(() => expect(box).toHaveFocus())
    await userEvent.type(box, 'On it.')
    await userEvent.click(screen.getByRole('button', { name: /^Send$/ }))
    expect(await screen.findByText('On it.')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /Sent.*Reply to ME22B2044 SUBHASH/ })).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('Write a reply…')).not.toBeInTheDocument()
  })

  it('marks a conversation done', async () => {
    api.setThreadDone.mockResolvedValue(undefined)
    open()
    await userEvent.click(await screen.findByRole('button', { name: /Mark done/ }))
    expect(api.setThreadDone).toHaveBeenCalledWith('s1', 't2', true)
    expect(await screen.findByRole('button', { name: /Reopen/ })).toBeInTheDocument()
  })

  it('starts the next thread with an empty reply, not the text typed in the last one', async () => {
    const { rerender, onActiveThread } = open()
    await screen.findByText(/to you/)
    await userEvent.click(screen.getByRole('button', { name: /Reply to ME22B2044 SUBHASH/ }))
    await userEvent.type(screen.getByPlaceholderText('Write a reply…'), 'For thread two only')
    rerender(
      <VendorInbox supplierId="s1" inbox={INBOX} loading={false} emailConn={{ connected: true, email: ME }} connecting={false}
        contacts={CONTACTS} activeThreadId="t1" onActiveThread={onActiveThread} onReload={() => {}} onConnect={() => {}}
        onAddContact={() => {}} attachFiles={[]} setAttachFiles={() => {}} onPickBills={() => {}} notify={() => {}} bills={BILLS} />,
    )
    await screen.findByText(/to you/)
    expect(screen.queryByDisplayValue('For thread two only')).not.toBeInTheDocument()
    await new Promise(r => setTimeout(r, 450))
    expect(localStorage.getItem('vc-draft:s1:t1')).toBeNull()
  })

  it('clears attachments added in another thread, and says so', async () => {
    const setAttachFiles = vi.fn(), notify = vi.fn()
    const files = [new File(['%PDF'], 'a.pdf')]
    const { rerender, onActiveThread } = open({ attachFiles: files, setAttachFiles, notify })
    rerender(
      <VendorInbox supplierId="s1" inbox={INBOX} loading={false} emailConn={{ connected: true, email: ME }} connecting={false}
        contacts={CONTACTS} activeThreadId="t1" onActiveThread={onActiveThread} onReload={() => {}} onConnect={() => {}}
        onAddContact={() => {}} attachFiles={files} setAttachFiles={setAttachFiles} onPickBills={() => {}} notify={notify} bills={BILLS} />,
    )
    expect(setAttachFiles).toHaveBeenCalledWith([])
    expect(notify).toHaveBeenCalledWith(expect.stringMatching(/Removed 1 attachment/), 'info')
  })

  it('sends once on a double Cmd+Enter', async () => {
    let finish: (v: unknown) => void = () => {}
    api.sendInboxEmail.mockImplementation(() => new Promise(r => { finish = r }))
    open()
    await screen.findByText(/to you/)
    await userEvent.click(screen.getByRole('button', { name: /Reply to ME22B2044 SUBHASH/ }))
    const box = screen.getByPlaceholderText('Write a reply…')
    await userEvent.type(box, 'Once please')
    await userEvent.type(box, '{Meta>}{Enter}{Enter}{/Meta}')
    await userEvent.keyboard('{Control>}{Enter}{/Control}')
    expect(api.sendInboxEmail).toHaveBeenCalledTimes(1)
    finish({ thread_id: 't2', to: ['me22b2044@iiitdm.ac.in'], cc: [], from: ME })
  })

  it('blocks Send while an invalid address is left in To', async () => {
    open()
    await screen.findByText(/to you/)
    await userEvent.click(screen.getByRole('button', { name: /Reply to ME22B2044 SUBHASH/ }))
    await userEvent.type(screen.getByPlaceholderText('Write a reply…'), 'Hello')
    await userEvent.type(screen.getByLabelText('To'), 'ravi@vendor{Enter}')
    expect(screen.getByText('Not a valid email')).toBeInTheDocument()
    const send = screen.getByRole('button', { name: /^Send$/ })
    expect(send).toBeDisabled()
    await userEvent.type(screen.getByPlaceholderText('Write a reply…'), '{Meta>}{Enter}{/Meta}')
    expect(api.sendInboxEmail).not.toHaveBeenCalled()
    await userEvent.clear(screen.getByLabelText('To'))
    expect(send).toBeEnabled()
  })

  it('asks before AI replaces typed text, and can undo it', async () => {
    api.draftInboxEmail.mockResolvedValue('AI wrote this.')
    open()
    await screen.findByText(/to you/)
    await userEvent.click(screen.getByRole('button', { name: /Reply to ME22B2044 SUBHASH/ }))
    await userEvent.type(screen.getByPlaceholderText('Write a reply…'), 'My own words')
    await userEvent.click(screen.getByRole('button', { name: /Write with AI/ }))
    await userEvent.click(screen.getAllByRole('menuitem')[0])
    expect(api.draftInboxEmail).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Replace' }))
    expect(await screen.findByDisplayValue('AI wrote this.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }))
    expect(screen.getByDisplayValue('My own words')).toBeInTheDocument()
  })

  it('shows a failed inbox load as an error, not as a mailbox to connect', () => {
    open({ inbox: { ...INBOX, connected: false, threads: [], error: 'Couldn’t load conversations.' }, emailConn: null, activeThreadId: null })
    expect(screen.getByRole('alert')).toHaveTextContent('Couldn’t load conversations.')
    expect(screen.queryByRole('button', { name: 'Connect Gmail' })).not.toBeInTheDocument()
  })

  it('keeps an attachment unfiled when saving it fails', async () => {
    api.saveInboxAttachment.mockRejectedValue(new Error('nope'))
    api.fetchVendorInboxThread.mockResolvedValue({
      me: ME,
      messages: [{
        id: 'm1', outbound: false, from: 'Vendor <me22b2044@iiitdm.ac.in>', to: ME, cc: '', date: '', internal_date: Date.parse('2026-10-11T10:00:00Z'),
        subject: 'Invoice question', body: 'Attached', attachments: [{ attachment_id: 'a1', filename: 'rates.xlsx', mime_type: '', size: 2048 }],
      }],
    })
    const notify = vi.fn()
    open({ notify })
    await userEvent.click(await screen.findByRole('button', { name: 'Save rates.xlsx to Documents' }))
    await userEvent.click(screen.getByRole('menuitem', { name: 'Contracts & IOs' }))
    await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.stringMatching(/Couldn’t save/), 'error'))
    expect(screen.queryByText(/in Contracts/)).not.toBeInTheDocument()
  })

  it('jumps to a menu item by its first letters', async () => {
    api.fetchVendorInboxThread.mockResolvedValue({
      me: ME,
      messages: [{
        id: 'm1', outbound: false, from: 'Vendor <me22b2044@iiitdm.ac.in>', to: ME, cc: '', date: '', internal_date: Date.parse('2026-10-11T10:00:00Z'),
        subject: 'Invoice question', body: 'Attached', attachments: [{ attachment_id: 'a1', filename: 'rates.xlsx', mime_type: '', size: 2048 }],
      }],
    })
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Save rates.xlsx to Documents' }))
    screen.getByRole('menuitem', { name: 'Contracts & IOs' }).focus()
    await userEvent.keyboard('n')
    expect(screen.getByRole('menuitem', { name: /New type/ })).toHaveFocus()
  })
})
