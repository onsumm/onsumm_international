import { forwardRef } from 'react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { LoadingMark } from './LoadingMark'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'success'
export type ButtonSize = 'sm' | 'md' | 'lg'

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className'> {
  variant?: ButtonVariant
  size?: ButtonSize
  /** Shows a spinner and blocks interaction. Keeps the label so width is stable. */
  loading?: boolean
  block?: boolean
  iconLeft?: ReactNode
  iconRight?: ReactNode
  children?: ReactNode
}

/**
 * The only button in the system. Replaces 20+ ad-hoc padding combinations and
 * the hand-rolled `background: '#2563eb'` inline styles found across the app.
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', loading, block, iconLeft, iconRight, children, disabled, type, ...rest },
  ref,
) {
  const cls = [
    'u-btn',
    `u-btn--${variant}`,
    `u-btn--${size}`,
    block ? 'u-btn--block' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <button
      ref={ref}
      // Defaulting to "button" prevents the classic bug where a control inside a
      // <form> silently submits it.
      type={type ?? 'button'}
      className={cls}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <LoadingMark size={14} label="Loading" /> : iconLeft}
      {children}
      {!loading && iconRight}
    </button>
  )
})
