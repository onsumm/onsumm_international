import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react'
import { CheckCircle2, AlertTriangle, AlertCircle, Info, X } from 'lucide-react'
import { IconButton } from './IconButton'

export type ToastTone = 'success' | 'error' | 'warn' | 'info'

export interface Toast {
  id: number
  tone: ToastTone
  title: string
  message?: string
}

interface ToastApi {
  show: (t: Omit<Toast, 'id'>) => void
  success: (title: string, message?: string) => void
  error: (title: string, message?: string) => void
  warn: (title: string, message?: string) => void
  dismiss: (id: number) => void
}

const ToastCtx = createContext<ToastApi | null>(null)

const ICONS: Record<ToastTone, ReactNode> = {
  success: <CheckCircle2 size={16} color="var(--success-600)" />,
  error: <AlertCircle size={16} color="var(--danger-600)" />,
  warn: <AlertTriangle size={16} color="var(--warn-600)" />,
  info: <Info size={16} color="var(--info-600)" />,
}

// Errors stay put until dismissed — a failure the user missed is a failure they
// will not retry.
const DURATION: Record<ToastTone, number> = { success: 4000, info: 4000, warn: 7000, error: 0 }

/**
 * One toast system for the whole app, replacing three separate per-page
 * implementations (Bills' bulkToast, Vendors' alert toast, Settings' inline
 * "Saved!" text) that each had different placement, duration and shape — and
 * none of which were announced to assistive technology.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(1)

  const dismiss = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  const show = useCallback(
    (t: Omit<Toast, 'id'>) => {
      const id = nextId.current++
      setToasts(prev => [...prev, { ...t, id }])
      const ms = DURATION[t.tone]
      if (ms > 0) setTimeout(() => dismiss(id), ms)
    },
    [dismiss],
  )

  const api = useMemo<ToastApi>(
    () => ({
      show,
      dismiss,
      success: (title, message) => show({ tone: 'success', title, message }),
      error: (title, message) => show({ tone: 'error', title, message }),
      warn: (title, message) => show({ tone: 'warn', title, message }),
    }),
    [show, dismiss],
  )

  return (
    <ToastCtx.Provider value={api}>
      {children}
      {/* polite: announced at the next natural pause, without interrupting. */}
      <div className="u-toasts" role="region" aria-live="polite" aria-label="Notifications">
        {toasts.map(t => (
          <div key={t.id} className={`u-toast u-toast--${t.tone}`}>
            <span aria-hidden="true" style={{ display: 'flex', marginTop: 1 }}>
              {ICONS[t.tone]}
            </span>
            <div className="u-toast__body">
              <div className="u-toast__title">{t.title}</div>
              {t.message && <div className="u-toast__msg">{t.message}</div>}
            </div>
            <IconButton label="Dismiss notification" icon={<X size={14} />} size="sm" onClick={() => dismiss(t.id)} />
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

// A context provider and its consumer hook are one unit; splitting them would
// force every caller to import from two files for no benefit.
// eslint-disable-next-line react-refresh/only-export-components -- provider and its hook belong in one module
export function useToast(): ToastApi {
  const ctx = useContext(ToastCtx)
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>')
  return ctx
}
