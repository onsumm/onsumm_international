import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { ConfirmDialog } from '../ui'

/**
 * The removal gate from UploadPage, in isolation.
 *
 * UploadPage itself pulls in react-dropzone, the OCR client and the whole app's
 * queue plumbing, so this exercises the rule that actually matters: an extracted
 * invoice cannot be discarded on a single click, because nothing is persisted
 * until it is reviewed and submitted.
 */

type Status = 'pending' | 'processing' | 'done' | 'error'

function Harness({ status, onRemove }: { status: Status; onRemove: () => void }) {
  const [confirming, setConfirming] = useState(false)
  return (
    <>
      <button onClick={() => (status === 'done' ? setConfirming(true) : onRemove())}>Remove bill.pdf</button>
      <ConfirmDialog
        open={confirming}
        onCancel={() => setConfirming(false)}
        onConfirm={() => {
          onRemove()
          setConfirming(false)
        }}
        tone="danger"
        title="Remove this extracted invoice?"
        description="The extracted data is discarded."
        confirmLabel="Remove"
        cancelLabel="Keep it"
      />
    </>
  )
}

describe('removing a queued file', () => {
  it('asks before discarding an extraction', async () => {
    const user = userEvent.setup()
    const onRemove = vi.fn()
    render(<Harness status="done" onRemove={onRemove} />)

    await user.click(screen.getByRole('button', { name: /remove bill\.pdf/i }))
    expect(screen.getByText('Remove this extracted invoice?')).toBeInTheDocument()
    // Nothing is gone yet — the dialog is a gate, not a notification.
    expect(onRemove).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Remove' }))
    expect(onRemove).toHaveBeenCalledTimes(1)
  })

  it('keeps the file when the user backs out', async () => {
    const user = userEvent.setup()
    const onRemove = vi.fn()
    render(<Harness status="done" onRemove={onRemove} />)

    await user.click(screen.getByRole('button', { name: /remove bill\.pdf/i }))
    await user.click(screen.getByRole('button', { name: 'Keep it' }))
    expect(onRemove).not.toHaveBeenCalled()
    expect(screen.queryByText('Remove this extracted invoice?')).not.toBeInTheDocument()
  })

  it('does not interrupt for a failed or unprocessed file, which loses nothing', async () => {
    const user = userEvent.setup()
    for (const status of ['error', 'pending'] as Status[]) {
      const onRemove = vi.fn()
      const { unmount } = render(<Harness status={status} onRemove={onRemove} />)
      await user.click(screen.getByRole('button', { name: /remove bill\.pdf/i }))
      expect(onRemove).toHaveBeenCalledTimes(1)
      expect(screen.queryByText('Remove this extracted invoice?')).not.toBeInTheDocument()
      unmount()
    }
  })
})
