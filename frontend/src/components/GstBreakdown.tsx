/**
 * GstBreakdown — the CGST/SGST/IGST split behind one "GST Amount" figure.
 *
 * The OCR reads every component and its rate, and the upload review screen
 * shows all of them as editable fields. Ingest then added them together and
 * kept only the sum, so from the Bills screen onward the split existed
 * nowhere a person could see it — a GST return, an ITC claim or a plain "is
 * this intra-state?" had to be answered off the PDF.
 *
 * Laid out as the review screen's own "Tax Breakdown" section: the same field
 * order, the same labels, the same three columns. Someone who corrected these
 * numbers at upload meets them here in the positions they left them in, rather
 * than re-reading a differently shaped summary panel. Absent components keep
 * their slot and show a dash, again as on review — where the field sits says
 * as much as what is in it.
 *
 * Renders as a fragment of grid cells, so it must be placed inside the bill
 * detail's `.bm__grid` (three columns) alongside the other Field cells.
 *
 * On a phone it folds shut by default, and the absent components — the dashes
 * — are dropped: one column has no slots for a dash to hold.
 */
import BillSection from './BillSection'


export interface GstBreakdownData {
  cgst?: number | null
  sgst?: number | null
  igst?: number | null
  cess?: number | null
  cgst_rate?: number | null
  sgst_rate?: number | null
  igst_rate?: number | null
  taxable_amount?: number | null
  /** As printed on the invoice, e.g. "Haryana (06)". The state whose GST
   *  applies — and not always the buyer's address. */
  place_of_supply?: string | null
  components_total?: number | null
  /** False when the bill's tax total has been edited since the split was read. */
  reconciles?: boolean
  is_interstate?: boolean
}

const inr = (v: number | null | undefined) =>
  // Indian GST only exists in rupees; shown only for tenants with the GST pack.
  v == null || v === 0 ? '—' : `₹${v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

/** Rates print bare, as they do on review: the label already carries the %. */
const pct = (v: number | null | undefined) =>
  v == null || v === 0 ? '—' : `${Number(v) % 1 === 0 ? v : Number(v).toFixed(2)}`

export default function GstBreakdown({
  data,
  taxAmount,
  title = 'Tax Breakdown',
}: {
  data?: GstBreakdownData | null
  /** The bill's current tax total, for the reconciliation line. */
  taxAmount?: number | null
  title?: string
}) {
  if (!data) return null

  // A bill with no component at all has nothing to break down — that is the
  // pre-split bill, not a bill whose tax is zero, and a grid of ten dashes
  // would say nothing.
  const hasAnyComponent = [data.cgst, data.sgst, data.igst, data.cess]
    .some(v => v != null && v !== 0)
  if (!hasAnyComponent) return null

  return (
    <BillSection
      title={title}
      closedOnPhone
      aside={
        // Which one it is follows from which components are present, so it is
        // stated rather than left to be inferred from three row labels.
        <span style={{
          fontSize: 10, fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase',
          padding: '2px 8px', borderRadius: 999,
          background: data.is_interstate ? '#eff6ff' : '#ecfdf5',
          color: data.is_interstate ? '#1d4ed8' : '#047857',
        }}>
          {data.is_interstate ? 'Inter-state' : 'Intra-state'}
        </span>
      }
    >

      <Cell label="Taxable Amt"  value={inr(data.taxable_amount)} />
      <Cell label="CGST Rate %"  value={pct(data.cgst_rate)} />
      <Cell label="CGST Amount"  value={inr(data.cgst)} />
      <Cell label="SGST Rate %"  value={pct(data.sgst_rate)} />
      <Cell label="SGST Amount"  value={inr(data.sgst)} />
      <Cell label="IGST Rate %"  value={pct(data.igst_rate)} />
      <Cell label="IGST Amount"  value={inr(data.igst)} />
      <Cell label="Cess Amount"  value={inr(data.cess)} />
      {/* Review calls this "Total Tax (as printed)" because there it is a
          field of its own, filled only when the invoice states a total and no
          split. A posted bill keeps no such figure — what it has is the sum of
          the components above — so the label says what this actually is. */}
      <Cell label="Total Tax"       value={inr(data.components_total)} strong />
      <Cell label="Place of Supply" value={data.place_of_supply || '—'} />

      {/* The split is written once, at ingest, from what the document said.
          Editing the bill's tax total afterwards cannot know how the new
          figure divides, so the components stay true of the document and stop
          adding up to the bill. Saying so beats letting someone add three
          numbers and wonder which one is wrong. */}
      {data.reconciles === false && (
        <div style={{ gridColumn: '1 / -1', fontSize: 11, lineHeight: 1.5, color: '#92400e' }}>
          This split is what the invoice stated. The bill's tax total has been
          edited since{taxAmount != null ? `, and now reads ${inr(taxAmount)}` : ''}.
        </div>
      )}
    </BillSection>
  )
}

function Cell({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className={`bm__kv${value === '—' ? ' bm__kv--empty' : ''}`}>
      <div className="bm__lbl">{label}</div>
      <div
        className="bm__val"
        style={{
          fontVariantNumeric: 'tabular-nums',
          fontWeight: strong ? 700 : undefined,
          color: value === '—' ? '#9ca3af' : undefined,
        }}
      >
        {value}
      </div>
    </div>
  )
}
