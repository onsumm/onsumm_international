import { describe, it, expect } from 'vitest'
import { buildPdf } from './receiptPdf'
import { receiptFileName, receiptPdfLines, receiptTotal, type ReceiptData } from './receiptContent'
import type { PayResult } from './usePaymentFlow'

const result = (over: Partial<PayResult> = {}): PayResult => ({
  disbursement_id: 'd1',
  invoice_id: 'inv-1',
  billLabel: 'B-018',
  amount: 186440,
  status: 'SUCCESS',
  status_message: 'Transaction Successful',
  bank_rrn: '144803323197',
  ...over,
})

const data = (over: Partial<ReceiptData> = {}): ReceiptData => ({
  vendorName: 'NIFTY LAW SERVICES LLP',
  beneficiaryName: 'Nifty Law Services',
  beneficiaryAccount: '******6778',
  beneficiaryIfsc: 'HDFC0005555',
  beneficiaryBank: 'HDFC Bank',
  remitterLabel: 'ICICI Bank (Demo) ···1000',
  results: [result()],
  paidAt: new Date('2026-08-03T10:30:00Z'),
  initiatedBy: 'Demo Admin',
  overridden: false,
  demo: true,
  ...over,
})

function asText(bytes: Uint8Array): string {
  return new TextDecoder('latin1').decode(bytes)
}

describe('pdf structure', () => {
  it('writes a well-formed single-page PDF whose xref offsets point at real objects', () => {
    const pdf = asText(buildPdf(receiptPdfLines(data())))

    expect(pdf.startsWith('%PDF-1.4\n')).toBe(true)
    expect(pdf.trimEnd().endsWith('%%EOF')).toBe(true)

    // startxref must land exactly on the xref table, or readers reject the file.
    const startxref = Number(pdf.match(/startxref\n(\d+)/)?.[1])
    expect(pdf.slice(startxref, startxref + 4)).toBe('xref')

    // Every offset in the table must land on that object's own header.
    const rows = [...pdf.matchAll(/^(\d{10}) 00000 n $/gm)].map(m => Number(m[1]))
    expect(rows).toHaveLength(7)
    rows.forEach((off, i) => {
      expect(pdf.slice(off, off + `${i + 1} 0 obj`.length)).toBe(`${i + 1} 0 obj`)
    })

    expect(pdf).toContain('/Type /Catalog')
    expect(pdf).toContain('/Type /Page ')
    expect(pdf).toContain('/BaseFont /Helvetica')
  })

  it('declares a stream length that matches the stream it wrote', () => {
    const pdf = asText(buildPdf(receiptPdfLines(data())))
    const declared = Number(pdf.match(/<< \/Length (\d+) >>/)?.[1])
    const actual = pdf.match(/stream\n([\s\S]*?)\nendstream/)?.[1] ?? ''
    expect(actual).toHaveLength(declared)
  })

  it('escapes parentheses and backslashes rather than corrupting the file', () => {
    const pdf = asText(buildPdf([{ text: 'Acme (India) Pvt \\ Ltd' }]))
    expect(pdf).toContain('(Acme \\(India\\) Pvt \\\\ Ltd) Tj')
  })

  it('renders the rupee sign as INR, since WinAnsi has no ₹ glyph', () => {
    const pdf = asText(buildPdf([{ text: '₹1,86,440.00' }]))
    expect(pdf).toContain('(INR 1,86,440.00) Tj')
    expect(pdf).not.toContain('₹')
  })

  it('survives an empty document instead of writing a broken stream', () => {
    const pdf = asText(buildPdf([]))
    expect(pdf).toContain('BT')
    expect(pdf).toContain('ET')
    expect(pdf.trimEnd().endsWith('%%EOF')).toBe(true)
  })
})

describe('receipt content', () => {
  it('carries the amount, vendor, reference and bill onto the page', () => {
    const pdf = asText(buildPdf(receiptPdfLines(data())))
    expect(pdf).toContain('Payment receipt')
    expect(pdf).toContain('Nifty Law Services')
    expect(pdf).toContain('144803323197')
    expect(pdf).toContain('B-018')
    expect(pdf).toContain('INR 1,86,440.00')
  })

  it('marks a demo receipt so it can never be mistaken for a real transfer', () => {
    expect(asText(buildPdf(receiptPdfLines(data({ demo: true }))))).toContain('DEMO ENVIRONMENT')
    expect(asText(buildPdf(receiptPdfLines(data({ demo: false }))))).not.toContain('DEMO ENVIRONMENT')
  })

  it('records an override on the receipt itself', () => {
    expect(asText(buildPdf(receiptPdfLines(data({ overridden: true }))))).toContain('Override')
  })

  it('totals only what actually settled', () => {
    const rows = [result(), result({ disbursement_id: 'd2', amount: 5000, status: 'FAILED' })]
    // A failed row is listed but must never be counted as money paid.
    expect(receiptTotal(rows)).toBe(186440)
  })

  it('names the file after the vendor, date and reference', () => {
    expect(receiptFileName(data())).toBe('receipt-nifty-law-services-llp-2026-08-03-144803323197.pdf')
  })
})
