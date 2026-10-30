/**
 * One source of truth for what a receipt says.
 *
 * The preview modal and the downloaded PDF read from the same builder, so the
 * document a user checks on screen is the document they file — the two cannot
 * drift into disagreeing about an amount or a reference.
 */

import { formatMoney, formatDateTime } from '../../ui'
import type { PdfLine } from './receiptPdf'
import { summariseResults, type PayResult } from './usePaymentFlow'

export interface ReceiptData {
  vendorName: string
  beneficiaryName: string
  beneficiaryAccount: string
  beneficiaryIfsc: string | null
  beneficiaryBank: string | null
  remitterLabel: string | null
  results: PayResult[]
  paidAt: Date
  initiatedBy?: string
  overridden: boolean
  demo: boolean
}

export interface ReceiptRow {
  label: string
  value: string
  mono?: boolean
}

const STATUS_WORD: Record<string, string> = {
  SUCCESS: 'Paid',
  PENDING: 'Processing at the bank',
}

export function statusWord(status: string): string {
  return STATUS_WORD[status] ?? 'Failed'
}

export function receiptTotal(results: PayResult[]): number {
  // Only settled money belongs on a receipt total; a failed row is listed but
  // must not be counted as paid.
  return results.filter(r => r.status === 'SUCCESS').reduce((s, r) => s + r.amount, 0)
}

export function receiptRows(d: ReceiptData): ReceiptRow[] {
  const rows: ReceiptRow[] = [
    { label: 'Paid to', value: d.beneficiaryName },
    { label: 'Vendor', value: d.vendorName },
    { label: 'Bank account', value: [d.beneficiaryBank, d.beneficiaryAccount].filter(Boolean).join(' · '), mono: true },
  ]
  if (d.beneficiaryIfsc) rows.push({ label: 'IFSC', value: d.beneficiaryIfsc, mono: true })
  if (d.remitterLabel) rows.push({ label: 'Paid from', value: d.remitterLabel, mono: true })
  rows.push({ label: 'Date', value: formatDateTime(d.paidAt) })
  if (d.initiatedBy) rows.push({ label: 'Released by', value: d.initiatedBy })
  rows.push({ label: 'Method', value: 'IMPS bank transfer' })
  if (d.overridden) rows.push({ label: 'Override', value: 'Beneficiary details entered by an admin' })
  return rows
}

/** The same document, laid out for the one-page PDF writer. */
export function receiptPdfLines(d: ReceiptData): PdfLine[] {
  const lines: PdfLine[] = [
    { text: 'Payment receipt', size: 20, bold: true },
    { text: `${formatMoney(receiptTotal(d.results), { precise: true })} to ${d.beneficiaryName}`, size: 12, spaceBefore: 4 },
  ]
  if (d.demo) lines.push({ text: 'DEMO ENVIRONMENT — no real funds were transferred', size: 9, bold: true, spaceBefore: 6 })

  lines.push({ text: 'Details', size: 11, bold: true, spaceBefore: 16 })
  for (const r of receiptRows(d)) lines.push({ text: `${r.label}: ${r.value}`, size: 10 })

  lines.push({ text: 'Bills settled', size: 11, bold: true, spaceBefore: 16 })
  for (const r of d.results) {
    lines.push({
      text: `${r.billLabel || r.invoice_id.slice(0, 8)} — ${formatMoney(r.amount, { precise: true })} — ${statusWord(r.status)}`,
      size: 10,
    })
    const ref = r.bank_rrn ?? r.tran_ref_no
    if (ref) lines.push({ text: `    Reference: ${ref}`, size: 9 })
    if (r.status_message) lines.push({ text: `    ${r.status_message}`, size: 9 })
  }

  if (d.results.length > 1) {
    lines.push({ text: summariseResults(d.results).label, size: 10, bold: true, spaceBefore: 10 })
  }
  lines.push({
    text: 'This receipt is generated from the payment record and is not a bank statement.',
    size: 8,
    spaceBefore: 20,
  })
  return lines
}

/** A filename a user can find again in their downloads folder. */
export function receiptFileName(d: ReceiptData): string {
  const ref = d.results[0]?.bank_rrn ?? d.results[0]?.tran_ref_no ?? ''
  const who = d.vendorName.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '').slice(0, 40) || 'vendor'
  const day = d.paidAt.toISOString().slice(0, 10)
  return `receipt-${who}-${day}${ref ? `-${ref}` : ''}.pdf`.toLowerCase()
}
