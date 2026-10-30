import type { ReactNode } from 'react'
import { Inbox, AlertCircle, RefreshCw } from 'lucide-react'
import { Button } from './Button'

/** Shimmering placeholder. Sized by the caller so layout does not shift on load. */
export function Skeleton({ width, height = 12, radius }: { width?: number | string; height?: number; radius?: number }) {
  return (
    <span
      className="u-skel"
      aria-hidden="true"
      style={{ display: 'block', width: width ?? '100%', height, borderRadius: radius }}
    />
  )
}

/** A skeleton shaped like table rows, so the table does not jump when data lands. */
export function SkeletonRows({ rows = 8, cols = 6 }: { rows?: number; cols?: number }) {
  return (
    <>
      {Array.from({ length: rows }, (_, r) => (
        <tr key={r}>
          {Array.from({ length: cols }, (_, c) => (
            <td key={c}>
              <Skeleton width={c === 0 ? '60%' : '80%'} />
            </td>
          ))}
        </tr>
      ))}
    </>
  )
}

export interface EmptyStateProps {
  title: string
  message?: string
  icon?: ReactNode
  action?: ReactNode
}

export function EmptyState({ title, message, icon, action }: EmptyStateProps) {
  return (
    <div className="u-state">
      <div className="u-state__icon" aria-hidden="true">
        {icon ?? <Inbox size={20} />}
      </div>
      <div className="u-state__title">{title}</div>
      {message && <div className="u-state__msg">{message}</div>}
      {action && <div style={{ marginTop: 'var(--sp-2)' }}>{action}</div>}
    </div>
  )
}

export interface ErrorStateProps {
  title?: string
  message: string
  onRetry?: () => void
}

export function ErrorState({ title = 'Something went wrong', message, onRetry }: ErrorStateProps) {
  return (
    <div className="u-state" role="alert">
      <div className="u-state__icon" style={{ color: 'var(--danger-600)', background: 'var(--danger-50)' }}>
        <AlertCircle size={20} aria-hidden="true" />
      </div>
      <div className="u-state__title">{title}</div>
      <div className="u-state__msg">{message}</div>
      {onRetry && (
        <div style={{ marginTop: 'var(--sp-2)' }}>
          <Button variant="secondary" size="sm" iconLeft={<RefreshCw size={13} />} onClick={onRetry}>
            Try again
          </Button>
        </div>
      )}
    </div>
  )
}
