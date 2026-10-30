import type { ReactNode } from 'react'

export type BadgeTone = 'neutral' | 'info' | 'success' | 'warn' | 'danger' | 'brand'

export interface BadgeProps {
  tone?: BadgeTone
  /**
   * Optional leading glyph. Supplying one makes the badge legible without
   * colour — required wherever a badge is the only signal (audit finding: FX
   * warning vs opportunity was an amber dot vs a green dot, indistinguishable
   * to red-green colour-blind users).
   */
  icon?: ReactNode
  children: ReactNode
  title?: string
}

export function Badge({ tone = 'neutral', icon, children, title }: BadgeProps) {
  return (
    <span className={`u-badge u-badge--${tone}`} title={title}>
      {icon && (
        <span aria-hidden="true" style={{ display: 'flex' }}>
          {icon}
        </span>
      )}
      {children}
    </span>
  )
}
