import type { ReactNode } from 'react'

export function Card({ children, style }: { children: ReactNode; style?: React.CSSProperties }) {
  return (
    <div className="u-card" style={style}>
      {children}
    </div>
  )
}

export function CardHeader({ title, sub, actions }: { title: string; sub?: string; actions?: ReactNode }) {
  return (
    <div className="u-card__header">
      <div style={{ minWidth: 0 }}>
        <div className="u-card__title">{title}</div>
        {sub && <div className="u-card__sub">{sub}</div>}
      </div>
      {actions}
    </div>
  )
}

export function CardBody({ children }: { children: ReactNode }) {
  return <div className="u-card__body">{children}</div>
}

export interface PageHeaderProps {
  title: string
  sub?: ReactNode
  actions?: ReactNode
}

/** The one page title treatment. Replaces five different sizes (20–26px). */
export function PageHeader({ title, sub, actions }: PageHeaderProps) {
  return (
    <div className="u-pagehead">
      <div style={{ minWidth: 0 }}>
        <h1 className="u-pagehead__title">{title}</h1>
        {sub && <p className="u-pagehead__sub">{sub}</p>}
      </div>
      {actions && <div className="u-pagehead__actions">{actions}</div>}
    </div>
  )
}

export function Tooltip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="u-tip-wrap">
      {children}
      <span className="u-tip" role="tooltip">
        {label}
      </span>
    </span>
  )
}

export interface FilterChipProps {
  label: string
  onRemove: () => void
}

/**
 * A removable, visible filter. The audit found active filters indicated only by
 * a count badge, so users could not see *which* filters were on without
 * reopening the panel.
 */
export function FilterChip({ label, onRemove }: FilterChipProps) {
  return (
    <span className="u-chip">
      {label}
      <button type="button" className="u-chip__remove" onClick={onRemove} aria-label={`Remove filter: ${label}`}>
        <span aria-hidden="true">×</span>
      </button>
    </span>
  )
}
