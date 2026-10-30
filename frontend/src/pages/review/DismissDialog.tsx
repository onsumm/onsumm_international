/**
 * "Why?" — the small dialog dismissal always goes through.
 *
 * Free-text dismissal yields nothing the system can learn from, so there is no
 * text box here, only these five reasons. The last — "never show me this kind
 * again" — is both the user's mute and the signal that retires a noisy
 * detector; picking it is how the system gets better, which a toast swallowed
 * by "OK" never could be.
 *
 * A real dialog (not an inline footer swap) so the same component serves the
 * drawer's "Not an issue" button and a keyboard-triggered dismiss from the
 * queue without duplicating the reason list in two places.
 */
import { useState } from 'react'

import { DISMISS_REASONS, type DismissReason } from '../../api/review'
import { Button, Modal } from '../../ui'

interface Props {
  open: boolean
  title?: string
  busy?: boolean
  onCancel: () => void
  onConfirm: (reason: DismissReason) => void
}

export function DismissDialog({ open, title, busy, onCancel, onConfirm }: Props) {
  const [reason, setReason] = useState<DismissReason>(DISMISS_REASONS[0].id)

  return (
    <Modal
      open={open}
      onClose={busy ? () => {} : onCancel}
      title={title || 'Why is this not an issue?'}
      size="sm"
      closeOnBackdrop={!busy}
      footer={
        <>
          <Button variant="secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button variant="danger" onClick={() => onConfirm(reason)} loading={busy}>
            Dismiss
          </Button>
        </>
      }
    >
      <fieldset className="rh__dismiss-fs">
        <legend className="sr-only">Reason for dismissing</legend>
        {DISMISS_REASONS.map(r => (
          <label key={r.id} className="rh__dismiss-opt">
            <input
              type="radio"
              name="dismiss-reason"
              value={r.id}
              checked={reason === r.id}
              onChange={() => setReason(r.id)}
            />
            {r.label}
          </label>
        ))}
      </fieldset>
    </Modal>
  )
}
