import { forwardRef } from 'react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className'> {
  /**
   * Required. An icon-only control with no accessible name is invisible to
   * screen readers — the audit found several (close, discard draft, delete bank
   * account). `title` alone does not count: it is unreliable for assistive tech
   * and never appears on touch devices.
   */
  label: string
  icon: ReactNode
  size?: 'sm' | 'md' | 'lg'
  tone?: 'default' | 'danger'
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon, size = 'md', tone = 'default', type, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? 'button'}
      className={`u-iconbtn u-iconbtn--${size}${tone === 'danger' ? ' u-iconbtn--danger' : ''}`}
      aria-label={label}
      title={label}
      {...rest}
    >
      <span aria-hidden="true" style={{ display: 'flex' }}>
        {icon}
      </span>
    </button>
  )
})
