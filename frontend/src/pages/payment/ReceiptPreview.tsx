/**
 * The receipt, on screen, before it is filed.
 *
 * Deliberately paper-shaped rather than another dashboard card: this is the
 * artefact someone attaches to a reconciliation, so it should look like the PDF
 * they are about to download — same builder feeds both (receiptContent.ts).
 */

import { Download } from 'lucide-react'
import { Badge, Button, Modal, formatMoney } from '../../ui'
import { buildPdf, downloadPdf } from './receiptPdf'
import {
  receiptFileName, receiptPdfLines, receiptRows, receiptTotal, statusWord, type ReceiptData,
} from './receiptContent'
import { summariseResults } from './usePaymentFlow'

export interface ReceiptPreviewProps {
  open: boolean
  onClose: () => void
  data: ReceiptData
}

export function ReceiptPreview({ open, onClose, data }: ReceiptPreviewProps) {
  const total = receiptTotal(data.results)
  const summary = summariseResults(data.results)

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Payment receipt"
      description="A copy of this payment as it will be filed."
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
          <Button
            variant="primary"
            iconLeft={<Download size={14} />}
            onClick={() => downloadPdf(buildPdf(receiptPdfLines(data), 'Payment receipt'), receiptFileName(data))}
          >
            Download PDF
          </Button>
        </>
      }
    >
      {/* The pop-in is scoped to the sheet, and prefers-reduced-motion zeroes it
          globally via tokens.css. */}
      <div className="pay-receipt">
        <div className="pay-receipt__head">
          <div>
            <div className="pay-receipt__label">Amount paid</div>
            <div className="pay-receipt__amount">{formatMoney(total, { precise: true })}</div>
            <div className="pay-receipt__to">to {data.beneficiaryName}</div>
          </div>
          {data.demo && <Badge tone="warn">DEMO</Badge>}
        </div>

        <dl className="pay-receipt__grid">
          {receiptRows(data).map(r => (
            <div className="pay-receipt__row" key={r.label}>
              <dt>{r.label}</dt>
              <dd className={r.mono ? 'pay-receipt__mono' : undefined}>{r.value}</dd>
            </div>
          ))}
        </dl>

        <div className="pay-receipt__label pay-receipt__section">Bills settled</div>
        {data.results.map(r => (
          <div className="pay-receipt__bill" key={r.disbursement_id}>
            <div>
              <div className="pay-receipt__billid">{r.billLabel || r.invoice_id.slice(0, 8)}</div>
              <div className="pay-receipt__billmeta">
                {statusWord(r.status)}
                {(r.bank_rrn ?? r.tran_ref_no) ? ` · ref ${r.bank_rrn ?? r.tran_ref_no}` : ''}
              </div>
            </div>
            <div className="pay-receipt__billamt">{formatMoney(r.amount, { precise: true })}</div>
          </div>
        ))}

        {data.results.length > 1 && <div className="pay-receipt__summary">{summary.label}</div>}

        <p className="pay-receipt__note">
          Generated from the payment record. This is not a bank statement.
          {data.overridden && ' Beneficiary details for this payment were entered by an admin as an override.'}
        </p>
      </div>
    </Modal>
  )
}
