/**
 * Step 4 — what the bank did.
 *
 * Every outcome gets a next action, which the old result card did not: a PENDING
 * payment sat on "Payment Processing" forever because `refreshDisbursementStatus`
 * was written but never called from anywhere, and a FAILED one offered no way to
 * try again. The bank reference is selectable and copyable — it is what
 * reconciliation and vendor support actually quote.
 */

import { useState } from 'react'
import { CheckCircle2, Clock, Copy, Download, Eye, RefreshCw, RotateCcw, XCircle } from 'lucide-react'
import { Button, formatMoney } from '../../ui'
import { summariseResults, type PayResult } from './usePaymentFlow'
import { ReceiptPreview } from './ReceiptPreview'
import { ReceiptPrintout } from './ReceiptPrintout'
import { buildPdf, downloadPdf } from './receiptPdf'
import { receiptFileName, receiptPdfLines, type ReceiptData } from './receiptContent'

export interface ReceiptStepProps {
  results: PayResult[]
  /** Everything the receipt needs that isn't in the results themselves. */
  receipt: ReceiptData
  /** Re-checks a payment the bank left in flight. */
  onCheckStatus: (r: PayResult) => Promise<void> | void
  checking: Record<string, boolean>
  onPayMore: () => void
  onDone: () => void
}

function toneOf(status: string): 'ok' | 'wait' | 'fail' {
  if (status === 'SUCCESS') return 'ok'
  if (status === 'PENDING') return 'wait'
  return 'fail'
}

const HEADLINE: Record<'ok' | 'wait' | 'fail', string> = {
  ok: 'Paid',
  wait: 'Processing at the bank',
  fail: 'Payment failed',
}

function CopyRef({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <span className="pay-ref">
      {label} <span className="pay-ref__val">{value}</span>
      <Button
        variant="ghost"
        size="sm"
        iconLeft={<Copy size={12} />}
        onClick={() => {
          void navigator.clipboard?.writeText(value).then(
            () => {
              setCopied(true)
              setTimeout(() => setCopied(false), 1500)
            },
            () => {},
          )
        }}
      >
        {copied ? 'Copied' : 'Copy'}
      </Button>
    </span>
  )
}

export function ReceiptStep({ results, receipt, onCheckStatus, checking, onPayMore, onDone }: ReceiptStepProps) {
  const summary = summariseResults(results)
  const [previewing, setPreviewing] = useState(false)
  // Nothing settled means there is nothing to receipt yet.
  const hasReceipt = summary.ok > 0
  // Print straight away, but only for a run that needs no further decisions. A
  // pending or failed row has its own action ("Check status", "Try again") on the
  // card below, and a slip covering the screen would bury it.
  const autoPrint = hasReceipt && summary.fail === 0 && summary.wait === 0

  return (
    <div className="pay-card">
      <h2 className="pay-card__title">
        {results.length > 1 ? 'Payment results' : HEADLINE[toneOf(results[0]?.status ?? '')]}
      </h2>

      {results.length > 1 && <div className="pay-summary">{summary.label}</div>}

      {results.map(r => {
        const tone = toneOf(r.status)
        const Icon = tone === 'ok' ? CheckCircle2 : tone === 'wait' ? Clock : XCircle
        return (
          <div key={r.disbursement_id} className={`pay-result pay-result--${tone}`}>
            <Icon size={18} className={`pay-result__icon--${tone}`} aria-hidden="true" />
            <div style={{ minWidth: 0 }}>
              <div className="pay-result__head">
                {HEADLINE[tone]}
                {r.billLabel ? ` · ${r.billLabel}` : ''}
              </div>
              {r.status_message && <div className="pay-result__msg">{r.status_message}</div>}
              {r.bank_rrn && <CopyRef label="Bank reference" value={r.bank_rrn} />}
              {!r.bank_rrn && r.tran_ref_no && <CopyRef label="Reference" value={r.tran_ref_no} />}
              {tone === 'wait' && (
                <div style={{ marginTop: 'var(--sp-2)' }}>
                  <Button
                    variant="secondary"
                    size="sm"
                    iconLeft={<RefreshCw size={12} />}
                    loading={!!checking[r.disbursement_id]}
                    onClick={() => onCheckStatus(r)}
                  >
                    Check status
                  </Button>
                </div>
              )}
            </div>
            <div className="pay-result__amt">{formatMoney(r.amount, { precise: true })}</div>
          </div>
        )
      })}

      <div className="pay-actions">
        {hasReceipt && (
          <div style={{ display: 'flex', gap: 'var(--sp-2)', flexWrap: 'wrap' }}>
            <Button variant="secondary" iconLeft={<Eye size={13} />} onClick={() => setPreviewing(true)}>
              Preview receipt
            </Button>
            <Button
              variant="ghost"
              iconLeft={<Download size={13} />}
              onClick={() => downloadPdf(buildPdf(receiptPdfLines(receipt), 'Payment receipt'), receiptFileName(receipt))}
            >
              Download PDF
            </Button>
          </div>
        )}
        <div className="pay-actions__right">
          {summary.fail > 0 && (
            <Button variant="secondary" iconLeft={<RotateCcw size={13} />} onClick={onPayMore}>
              Try again
            </Button>
          )}
          {summary.fail === 0 && (
            <Button variant="secondary" onClick={onPayMore}>
              Pay another bill
            </Button>
          )}
          <Button variant="primary" onClick={onDone}>
            Done
          </Button>
        </div>
      </div>

      <ReceiptPreview open={previewing} onClose={() => setPreviewing(false)} data={receipt} />

      {/* The receipt, printed and then held. The same three choices as the card
          below, so the user never has to dismiss the slip to reach them. */}
      {autoPrint && (
        <ReceiptPrintout
          data={receipt}
          actions={
            <>
              <Button
                variant="secondary"
                iconLeft={<Download size={13} />}
                onClick={() => downloadPdf(buildPdf(receiptPdfLines(receipt), 'Payment receipt'), receiptFileName(receipt))}
              >
                Download PDF
              </Button>
              <Button variant="secondary" onClick={onPayMore}>
                Pay another bill
              </Button>
              <Button variant="primary" onClick={onDone}>
                Done
              </Button>
            </>
          }
        />
      )}
    </div>
  )
}
