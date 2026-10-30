import axios from 'axios'
import type { InvoicePayload } from './invoices'

// Vendor Bill Agent — status, activity log, POC watch flags, and the draft
// bills the agent OCR'd (pending review in the Bills "Draft" tab).

export interface AgentStatus {
  agent: string
  enabled: boolean
  poll_interval: number
  watched_pocs: number
  pending_drafts: number
  events_total: number
  last_scan_at: string | null
}

export interface AgentEvent {
  id: string
  supplier_id: string | null
  contact_id: string | null
  from_email: string | null
  subject: string | null
  action: 'skipped_no_pdf' | 'skipped_not_bill' | 'draft_created' | 'ocr_failed' | string
  detail: string | null
  draft_id: string | null
  created_at: string
  supplier_name: string | null
}

export interface AgentPoc {
  id: string
  supplier_id: string
  name: string
  role: string | null
  email: string
  agent_enabled: boolean
  supplier_name: string | null
}

export interface AgentDraft {
  id: string
  supplier_id: string | null
  contact_id: string | null
  subject: string | null
  from_email: string | null
  source_file: string
  created_at: string
  vendor_name: string | null
  invoice_number: string | null
  invoice_date: string | null
  total_amount: number | null
  currency: string
  analysis: 'new' | 'correction' | 'duplicate' | null
  suggestion: string | null
  related_invoice: string | null
}

export interface AgentDraftDetail {
  id: string
  supplier_id: string | null
  source_file: string
  subject: string | null
  from_email: string | null
  payload: InvoicePayload
  analysis: 'new' | 'correction' | 'duplicate' | null
  suggestion: string | null
  related_invoice: string | null
}

export async function fetchAgentStatus(companyId: string): Promise<AgentStatus> {
  const { data } = await axios.get<AgentStatus>('/api/agent/status', { params: { company_id: companyId } })
  return data
}

export async function toggleAgent(companyId: string, enabled: boolean): Promise<void> {
  await axios.post('/api/agent/toggle', { company_id: companyId, enabled })
}

/** Run a mailbox scan for this tenant immediately; resolves when it's done. */
export async function triggerAgentScan(companyId: string): Promise<void> {
  await axios.post('/api/agent/scan', { company_id: companyId })
}

export async function fetchAgentEvents(companyId: string, limit = 50): Promise<AgentEvent[]> {
  const { data } = await axios.get<AgentEvent[]>('/api/agent/events', { params: { company_id: companyId, limit } })
  return data
}

export async function fetchAgentPocs(companyId: string): Promise<AgentPoc[]> {
  const { data } = await axios.get<AgentPoc[]>('/api/agent/pocs', { params: { company_id: companyId } })
  return data
}

export async function setContactAgent(supplierId: string, contactId: string, companyId: string, enabled: boolean): Promise<void> {
  await axios.patch(`/api/vendors/${supplierId}/contacts/${contactId}/agent`, { company_id: companyId, enabled })
}

export async function fetchAgentDrafts(companyId: string): Promise<AgentDraft[]> {
  const { data } = await axios.get<AgentDraft[]>('/api/agent/drafts', { params: { company_id: companyId } })
  return data
}

export async function fetchAgentDraft(draftId: string, companyId: string): Promise<AgentDraftDetail> {
  const { data } = await axios.get<AgentDraftDetail>(`/api/agent/drafts/${draftId}`, { params: { company_id: companyId } })
  return data
}

/** Download the draft's PDF and wrap it as a File for the ReviewPage preview. */
export async function fetchAgentDraftFile(draftId: string, companyId: string, filename: string): Promise<File> {
  const { data } = await axios.get(`/api/agent/drafts/${draftId}/file`, {
    params: { company_id: companyId },
    responseType: 'blob',
  })
  return new File([data], filename, { type: 'application/pdf' })
}

export async function markDraftSubmitted(draftId: string, companyId: string): Promise<void> {
  await axios.post(`/api/agent/drafts/${draftId}/submitted`, { company_id: companyId })
}

export async function discardDraft(draftId: string, companyId: string): Promise<void> {
  await axios.delete(`/api/agent/drafts/${draftId}`, { params: { company_id: companyId } })
}
