import axios from 'axios'

/**
 * The vendor card's own calls: typed documents and the vendor inbox.
 * Tenant and user come from the login token on every request.
 */

// ── Documents ────────────────────────────────────────────────────────────────

/** 'contract' is read and checked against the vendor; 'other' carries a named type. */
export type DocType = 'contract' | 'other'

export interface VendorDocument {
  id: string
  file_name: string
  file_size: number | null
  uploaded_by: string | null
  uploaded_at: string | null
  doc_type: DocType
  /** The type's name when doc_type is 'other', e.g. "Rate cards". */
  doc_label: string | null
  /** Contract reading: PENDING / RUNNING / DONE / SKIPPED / FAILED. */
  claims_status: string | null
}

export async function fetchVendorDocuments(supplierId: string, brandId?: string | null): Promise<VendorDocument[]> {
  const { data } = await axios.get(`/api/vendors/${supplierId}/documents`, { params: brandId ? { brand_id: brandId } : {} })
  return (data.documents ?? []).map((d: VendorDocument) => ({ ...d, doc_type: d.doc_type ?? 'contract' }))
}

export async function uploadVendorDocument(supplierId: string, file: File, opts: {
  docType: DocType; docLabel?: string | null; uploadedBy?: string; brandId?: string | null
}): Promise<{ id: string; file_name: string }> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('uploaded_by', opts.uploadedBy ?? '')
  fd.append('doc_type', opts.docType)
  if (opts.docLabel) fd.append('doc_label', opts.docLabel)
  const { data } = await axios.post(`/api/vendors/${supplierId}/documents`, fd, { params: opts.brandId ? { brand_id: opts.brandId } : {} })
  return data
}

export async function setVendorDocumentType(supplierId: string, docId: string, docType: DocType, docLabel?: string | null): Promise<{ queued: boolean }> {
  const { data } = await axios.patch(`/api/vendors/${supplierId}/documents/${docId}/type`, { doc_type: docType, doc_label: docLabel ?? null })
  return data
}

export async function deleteVendorDocument(supplierId: string, docId: string): Promise<void> {
  await axios.delete(`/api/vendors/${supplierId}/documents/${docId}`)
}

/** Types the team has named across every vendor, most used first. */
export async function fetchDocumentTypes(): Promise<{ label: string; documents: number }[]> {
  const { data } = await axios.get('/api/vendor-document-types')
  return data.types ?? []
}

export function vendorDocumentUrl(supplierId: string, docId: string, preview = false): string {
  const base = import.meta.env.VITE_API_URL ?? ''
  return `${base}/api/vendors/${supplierId}/documents/${docId}/download${preview ? '?preview=true' : ''}`
}

// ── Inbox ────────────────────────────────────────────────────────────────────

export interface InboxThread {
  thread_id: string
  subject: string
  snippet: string
  first_at: string | null
  last_at: string | null
  message_count: number
  /** Who wrote the first message. */
  started_by: 'us' | 'vendor'
  started_by_name: string | null
  last_from: string | null
  last_outbound: boolean
  needs_reply: boolean
  has_attachments: boolean
  vendor_addresses: string[]
  /** The contact the thread is with, when they are one. */
  contact_id: string | null
  /** People at the vendor's domain who wrote in but aren't contacts. */
  unknown_senders: string[]
  /** needs_reply: they wrote last · waiting: we did · done: marked done since the last message. */
  status: 'needs_reply' | 'waiting' | 'done'
  /** The vendor-side person this thread is with (never us). */
  counterparty: { email: string; name: string } | null
  participants: InboxPerson[]
}

export interface InboxPerson { email: string; name: string; vendor: boolean; me: boolean }

export interface VendorInbox {
  me: string | null
  connected: boolean
  /** False when only our send log could be read. */
  read_available: boolean
  error: string | null
  addresses: string[]
  domains: string[]
  threads: InboxThread[]
}

export interface InboxAttachment { filename: string; mime_type: string; size: number; attachment_id: string }
export interface InboxMessage {
  id: string
  outbound: boolean
  from: string
  to: string
  cc?: string
  date: string
  internal_date: number | null
  subject: string
  body: string
  attachments: InboxAttachment[]
}

export async function fetchVendorInbox(supplierId: string): Promise<VendorInbox> {
  const { data } = await axios.get(`/api/vendors/${supplierId}/inbox`)
  return data
}

export async function fetchVendorInboxThread(supplierId: string, threadId: string): Promise<{ me: string | null; messages: InboxMessage[] }> {
  const { data } = await axios.get(`/api/vendors/${supplierId}/inbox/thread`, { params: { thread_id: threadId } })
  return data
}

export async function summarizeVendorThread(supplierId: string, threadId: string): Promise<{ summary: string; actions: { label: string; detail: string }[] }> {
  const { data } = await axios.post(`/api/vendors/${supplierId}/inbox/thread/summarize`, null, { params: { thread_id: threadId } })
  return data
}

export function inboxAttachmentUrl(supplierId: string, threadId: string, messageId: string, a: InboxAttachment): string {
  const base = import.meta.env.VITE_API_URL ?? ''
  const q = new URLSearchParams({ thread_id: threadId, message_id: messageId, attachment_id: a.attachment_id, filename: a.filename })
  return `${base}/api/vendors/${supplierId}/inbox/attachment?${q}`
}

/** Send from the user's mailbox: new, reply, reply all or forward. */
export async function sendInboxEmail(supplierId: string, m: {
  to: string[]; cc?: string[]; subject?: string; body: string; threadId?: string | null; files?: File[]
}): Promise<{ thread_id?: string; to: string[]; cc: string[]; from: string }> {
  const fd = new FormData()
  fd.append('to', m.to.join(', '))
  if (m.cc?.length) fd.append('cc', m.cc.join(', '))
  if (m.subject) fd.append('subject', m.subject)
  fd.append('body', m.body)
  if (m.threadId) fd.append('thread_id', m.threadId)
  for (const f of m.files ?? []) fd.append('files', f)
  const { data } = await axios.post(`/api/vendors/${supplierId}/inbox/send`, fd)
  return data
}

export async function setThreadDone(supplierId: string, threadId: string, done: boolean): Promise<void> {
  await axios.post(`/api/vendors/${supplierId}/inbox/thread/state`, { thread_id: threadId, status: done ? 'resolved' : 'open' })
}

/** An AI-written email body for the user to edit; `intent` is what they want to say. */
export async function draftInboxEmail(supplierId: string, o: {
  threadId?: string | null; intent?: string; signer?: string
  /** Bills the email is about (invoice ids): attached, and mentioned in the thread. */
  attachedBillIds?: string[]; billIds?: string[]
}): Promise<string> {
  const { data } = await axios.post(`/api/vendors/${supplierId}/inbox/draft`, {
    thread_id: o.threadId ?? null, intent: o.intent ?? null, signer: o.signer ?? null,
    attached_bill_ids: o.attachedBillIds ?? [], bill_ids: o.billIds ?? [],
  })
  return data.body ?? ''
}

/** File an email attachment into the vendor's Documents. */
export async function saveInboxAttachment(supplierId: string, o: {
  threadId: string; messageId: string; attachment: InboxAttachment; docType: DocType; docLabel?: string | null; brandId?: string | null
}): Promise<{ id: string; file_name: string }> {
  const { data } = await axios.post(`/api/vendors/${supplierId}/inbox/attachment/save`, {
    thread_id: o.threadId, message_id: o.messageId, attachment_id: o.attachment.attachment_id,
    filename: o.attachment.filename, doc_type: o.docType, doc_label: o.docLabel ?? null, brand_id: o.brandId ?? null,
  })
  return data
}

// ── Logos ────────────────────────────────────────────────────────────────────

/** Vendors with a logo → its version; `pending` vendors are still being looked up. */
export async function fetchVendorLogos(): Promise<{ logos: Record<string, number>; pending: number }> {
  const { data } = await axios.get('/api/vendor-logos')
  return { logos: data.logos ?? {}, pending: data.pending ?? 0 }
}

export function vendorLogoPath(supplierId: string, version: number): string {
  return `/api/vendors/${supplierId}/logo?v=${version}`
}

/** Use this website's logo for the vendor. */
export async function setVendorLogo(supplierId: string, website: string): Promise<{ version: number; domain: string }> {
  const { data } = await axios.put(`/api/vendors/${supplierId}/logo`, { website })
  return data
}

/** Back to initials, for good. */
export async function removeVendorLogo(supplierId: string): Promise<void> {
  await axios.delete(`/api/vendors/${supplierId}/logo`)
}

// ── What a document is ───────────────────────────────────────────────────────

export interface DocVerdict {
  /** contract → Contracts & IOs · existing → one of the team's types · new → a new type · bill → bill upload */
  kind: 'contract' | 'existing' | 'new' | 'bill'
  label?: string
  suggested_name?: string
  title: string
  reason: string
  /** 'ai' read it; 'guess' went by keywords. */
  source: 'ai' | 'guess'
  /** Jev's confidence in the kind (0–1), when Jev decided. */
  confidence?: number
  /** False when Jev wasn't sure: the user is asked to confirm. */
  sure?: boolean
  /** Who decided: jev (from the text), vision (a scan, looked at), keywords. */
  engine?: 'jev' | 'vision' | 'keywords'
}

/** Read a file (nothing is stored) and say what it is, so it can be filed without asking. */
export async function classifyVendorDocument(supplierId: string, file: File): Promise<DocVerdict> {
  const fd = new FormData()
  fd.append('file', file)
  const { data } = await axios.post(`/api/vendors/${supplierId}/documents/classify`, fd)
  return data
}

// ── Activity ─────────────────────────────────────────────────────────────────

export type VendorActivityKind = 'bill' | 'approval' | 'payment' | 'change' | 'compliance' | 'document' | 'contact' | 'email'

export interface VendorActivityEvent {
  id: string
  /** ISO with timezone. */
  at: string
  kind: VendorActivityKind
  /** Machine code: bill_received, payment_failed, bank_primary_changed, … */
  action: string
  /** A plain-English sentence, ready to show. */
  title: string
  detail: string | null
  /** null is the system, or someone we can't name. */
  actor: string | null
  actor_role: string | null
  invoice_id: string | null
  bill_no: string | null
  amount: number | null
  changes: { field: string; from: string | null; to: string | null }[] | null
}

export interface VendorActivityPage {
  events: VendorActivityEvent[]
  /** Pass back as `before` to load older events; null when there are none. */
  next_before: string | null
  /** Some history couldn't be loaded. */
  partial: boolean
}

export async function fetchVendorActivity(supplierId: string, opts: { before?: string | null; limit?: number } = {}): Promise<VendorActivityPage> {
  const params: Record<string, string | number> = { limit: opts.limit ?? 100 }
  if (opts.before) params.before = opts.before
  const { data } = await axios.get(`/api/vendors/${supplierId}/activity`, { params })
  return { events: data.events ?? [], next_before: data.next_before ?? null, partial: !!data.partial }
}
