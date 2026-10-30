import { useId, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { IconButton } from './IconButton'
import { useFocusTrap, useScrollLock } from './useFocusTrap'

export interface ModalProps {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  size?: 'sm' | 'md' | 'lg' | 'xl'
  /** Footer actions, right-aligned. */
  footer?: ReactNode
  children: ReactNode
  /** Set false for flows where a stray backdrop click would lose work. */
  closeOnBackdrop?: boolean
}

/**
 * An accessible dialog: role=dialog, aria-modal, a real accessible name and
 * description, focus trapped inside, focus restored on close, Escape to
 * dismiss, and the page behind locked from scrolling.
 *
 * Rendered through a portal so it is never clipped by a parent's `overflow`
 * and always sits on the --z-modal layer rather than an ad-hoc z-index.
 */
export function Modal({
  open,
  onClose,
  title,
  description,
  size = 'md',
  footer,
  children,
  closeOnBackdrop = true,
}: ModalProps) {
  const ref = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const descId = useId()

  useFocusTrap(ref, open, onClose)
  useScrollLock(open)

  if (!open) return null

  return createPortal(
    // Click-outside-to-dismiss is a pointer convenience only. The keyboard path
    // is Escape (useFocusTrap) plus the always-present close button; both are
    // covered by tests in ui.test.tsx.
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions -- pointer convenience; Escape and the close button cover keyboard
    <div
      className="u-modal__backdrop"
      onMouseDown={e => {
        // mousedown-on-backdrop only: a click that *started* inside the dialog
        // and drifted out (text selection) must not close it.
        if (closeOnBackdrop && e.target === e.currentTarget) onClose()
      }}
    >
      <div
        ref={ref}
        className={`u-modal u-modal--${size}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descId : undefined}
      >
        <div className="u-modal__head">
          <div style={{ minWidth: 0 }}>
            <h2 className="u-modal__title" id={titleId}>
              {title}
            </h2>
            {description && (
              <p className="u-modal__desc" id={descId}>
                {description}
              </p>
            )}
          </div>
          <IconButton label="Close dialog" icon={<X size={16} />} onClick={onClose} />
        </div>

        <div className="u-modal__body">{children}</div>

        {footer && <div className="u-modal__foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  )
}
