import { useEffect } from 'react'

/** Keep `document.title` in sync with the current screen. */
export function useDocumentTitle(title: string): void {
  useEffect(() => {
    if (document.title !== title) document.title = title
  }, [title])
}
