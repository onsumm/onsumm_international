import { useEffect } from 'react'

/**
 * Warn before the browser discards unsaved work.
 *
 * The audit found no `beforeunload` handler anywhere, while the Review editor
 * holds an entire session of hand-corrected OCR in memory. A stray refresh
 * after twenty minutes of fixing a sixty-line invoice lost everything, silently.
 *
 * This covers reload / close / external navigation. In-app navigation is guarded
 * separately by the caller (the Review editor confirms before `onBack`).
 */
export function useUnsavedGuard(dirty: boolean): void {
  useEffect(() => {
    if (!dirty) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      // Browsers ignore custom text now and show their own copy, but a non-empty
      // returnValue is still what triggers the prompt.
      e.returnValue = ''
      return ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])
}
