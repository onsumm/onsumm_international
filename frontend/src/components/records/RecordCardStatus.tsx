import { useEffect } from 'react'
import { AlertCircle, X } from 'lucide-react'
import { LoadingMark } from '../../ui'

/** What a record card shows while it fetches its record by id, or if it can't. */
export default function RecordCardStatus({ label, error, onClose }: {
  label: string
  error?: string
  onClose: () => void
}) {
  // Escape closes, the same as the cards themselves.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="rc__overlay" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="rc__status" role={error ? 'alert' : 'status'}>
        {error ? (
          <>
            <AlertCircle size={18} className="rc__status-icon" />
            <span>{error}</span>
          </>
        ) : (
          <>
            <LoadingMark size={18} />
            <span>Loading {label}…</span>
          </>
        )}
        <button className="rc__status-close" onClick={onClose} aria-label="Close">
          <X size={14} />
        </button>
      </div>
    </div>
  )
}
