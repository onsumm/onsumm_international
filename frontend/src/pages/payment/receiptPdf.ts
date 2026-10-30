/**
 * A minimal, dependency-free PDF writer for payment receipts.
 *
 * The project has no PDF generator — `react-pdf` is a viewer — and a receipt is
 * text only, so a one-page writer is cheaper than pulling in jsPDF (~90 kB gzip)
 * for eight lines of Helvetica. Routing "Download" through window.print() was
 * the alternative, but that is a print dialog, not a download: the user asked
 * for a file, so they get a file.
 *
 * Deliberately narrow: one page, two fonts, left-aligned text. If receipts ever
 * need tables, logos or wrapping, replace this with a real library rather than
 * growing it.
 */

export interface PdfLine {
  text: string
  /** Points. Defaults to 10. */
  size?: number
  bold?: boolean
  /** Extra space above this line, in points. */
  spaceBefore?: number
}

const PAGE_W = 595 // A4 at 72dpi
const PAGE_H = 842
const MARGIN = 56
const LEADING = 6

/**
 * WinAnsiEncoding has no ₹ (and no rupee in the standard 14 fonts), so a naive
 * write turns every amount into mojibake. Callers format money as "INR 1,234.00";
 * this is the backstop for anything else that slips through, including a vendor
 * name with an em dash or curly quote.
 */
function toWinAnsi(s: string): string {
  return s
    .replace(/₹/g, 'INR ')
    .replace(/[·•]/g, '-')
    .replace(/[—–]/g, '-')
    .replace(/[’‘]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/…/g, '...')
    // Anything still outside printable ASCII would need a real encoding table.
    .replace(/[^\x20-\x7E]/g, '?')
}

/** Backslash, and both parens, are the only characters that break a PDF string. */
function escapePdfText(s: string): string {
  return toWinAnsi(s).replace(/\\/g, '\\\\').replace(/\(/g, '\\(').replace(/\)/g, '\\)')
}

function contentStream(lines: PdfLine[]): string {
  const out: string[] = ['BT']
  let y = PAGE_H - MARGIN
  let first = true
  for (const line of lines) {
    const size = line.size ?? 10
    const drop = (line.spaceBefore ?? 0) + size + LEADING
    y -= drop
    out.push(`/${line.bold ? 'F2' : 'F1'} ${size} Tf`)
    // Absolute placement per line: simpler to reason about than chained Td, and
    // a wrong offset can't cascade down the page.
    out.push(`1 0 0 1 ${MARGIN} ${Math.round(y)} Tm`)
    out.push(`(${escapePdfText(line.text)}) Tj`)
    first = false
  }
  if (first) out.push(`/F1 10 Tf`, `1 0 0 1 ${MARGIN} ${PAGE_H - MARGIN} Tm`, '() Tj')
  out.push('ET')
  return out.join('\n')
}

/** Builds a single-page PDF and returns its bytes. */
export function buildPdf(lines: PdfLine[], title = 'Payment receipt'): Uint8Array {
  const stream = contentStream(lines)
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PAGE_W} ${PAGE_H}] /Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> /Contents 4 0 R >>`,
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>',
    `<< /Title (${escapePdfText(title)}) /Producer (Onsumm) >>`,
  ]

  let body = ''
  const offsets: number[] = []
  const header = '%PDF-1.4\n'
  let pos = header.length
  objects.forEach((obj, i) => {
    const chunk = `${i + 1} 0 obj\n${obj}\nendobj\n`
    offsets.push(pos)
    body += chunk
    pos += chunk.length
  })

  // xref offsets are byte counts, and every string here is ASCII after
  // toWinAnsi, so character length is byte length.
  const xrefStart = pos
  let xref = `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`
  for (const off of offsets) xref += `${String(off).padStart(10, '0')} 00000 n \n`
  const trailer = `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R /Info ${objects.length} 0 R >>\nstartxref\n${xrefStart}\n%%EOF\n`

  return new TextEncoder().encode(header + body + xref + trailer)
}

/** Triggers a browser download without leaking the object URL. */
export function downloadPdf(bytes: Uint8Array, fileName: string): void {
  const blob = new Blob([bytes as unknown as BlobPart], { type: 'application/pdf' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = fileName
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
