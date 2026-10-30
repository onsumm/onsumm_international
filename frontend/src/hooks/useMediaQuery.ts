import { useEffect, useState } from 'react'

/** The width below which the app swaps the sidebar for the bottom nav. */
export const PHONE_QUERY = '(width <= 768px)'

/** Whether a CSS media query matches right now; follows rotation and resizes. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(
    () => typeof window !== 'undefined' && Boolean(window.matchMedia?.(query).matches),
  )

  useEffect(() => {
    const mql = window.matchMedia?.(query)
    if (!mql) return
    const sync = () => setMatches(mql.matches)
    sync()
    mql.addEventListener('change', sync)
    return () => mql.removeEventListener('change', sync)
  }, [query])

  return matches
}
