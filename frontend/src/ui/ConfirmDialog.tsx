import { useEffect, useState, type ReactNode } from 'react'
import { Modal } from './Modal'
import { Button } from './Button'
import { Input } from './Field'

export interface ConfirmImpact {
  label: string
  value: ReactNode
}

export interface ConfirmDialogProps {
  open: boolean
  onCancel: () => void
  onConfirm: () => void | Promise<void>
  title: string
  /** What is about to happen, in plain language. */
  description: string
  /** Headline numbers: how many records, how much money. */
  impact?: ConfirmImpact[]
  /** The specific records affected — never make the user guess. */
  items?: string[]
  confirmLabel?: string
  cancelLabel?: string
  tone?: 'danger' | 'primary'
  /**
   * Require the user to type this exact word before confirming. Reserve it for
   * the genuinely irreversible: deleting bills, removing a vendor bank account.
   */
  requireTyped?: string
  /**
   * Optional visual shown above the impact numbers — a before/after, a preview,
   * a thumbnail. For a change the user has already looked at, showing the same
   * thing again is recognition; describing it in prose a second time is not.
   */
  children?: ReactNode
}

const MAX_LISTED = 50

/**
 * Replaces `window.confirm()`, which the audit found guarding bulk bill
 * deletion, draft discard and permission changes. A native dialog cannot show
 * *which* records are affected, cannot show the total value at risk, cannot be
 * styled, and cannot be tested. Deleting ₹4 crore of payables behind an OS
 * "OK / Cancel" is not acceptable in a finance product.
 */
export function ConfirmDialog({
  open,
  onCancel,
  onConfirm,
  title,
  description,
  impact,
  items,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  tone = 'danger',
  requireTyped,
  children,
}: ConfirmDialogProps) {
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)

  // Never carry a previous confirmation's typed value into the next one.
  useEffect(() => {
    if (open) {
      setTyped('')
      setBusy(false)
    }
  }, [open])

  const unlocked = !requireTyped || typed.trim().toUpperCase() === requireTyped.toUpperCase()

  const run = async () => {
    if (!unlocked || busy) return
    setBusy(true)
    try {
      await onConfirm()
    } finally {
      setBusy(false)
    }
  }

  const shown = items?.slice(0, MAX_LISTED) ?? []
  const hidden = (items?.length ?? 0) - shown.length

  return (
    <Modal
      open={open}
      onClose={busy ? () => {} : onCancel}
      title={title}
      description={description}
      size="sm"
      closeOnBackdrop={!busy}
      footer={
        <>
          <Button variant="secondary" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button
            variant={tone === 'danger' ? 'danger' : 'primary'}
            onClick={run}
            disabled={!unlocked}
            loading={busy}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children}

      {impact && impact.length > 0 && (
        <div className="u-confirm__impact">
          {impact.map(i => (
            <div className="u-confirm__impact-row" key={i.label}>
              <span>{i.label}</span>
              <span className="u-confirm__impact-value">{i.value}</span>
            </div>
          ))}
        </div>
      )}

      {shown.length > 0 && (
        <>
          <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--ink-600)' }}>Affected</div>
          <ul className="u-confirm__list">
            {shown.map((it, i) => (
              <li key={i}>{it}</li>
            ))}
          </ul>
          {hidden > 0 && (
            <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--ink-500)', marginTop: 'var(--sp-1)' }}>
              …and {hidden} more
            </div>
          )}
        </>
      )}

      {requireTyped && (
        <div style={{ marginTop: 'var(--sp-4)' }}>
          <Input
            label={`Type ${requireTyped} to confirm`}
            value={typed}
            onChange={e => setTyped(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            disabled={busy}
          />
        </div>
      )}
    </Modal>
  )
}
