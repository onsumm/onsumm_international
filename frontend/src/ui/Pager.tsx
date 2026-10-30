import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Dropdown } from './Dropdown'
import { displayLocale } from './format'

/** Rows-per-page choices offered by every pager. */
export const PAGE_SIZES = [25, 50, 100] as const

/**
 * The page buttons to draw: always seven slots once there are more than seven
 * pages — first, last, the current page and its neighbours, and ellipses —
 * so the pager keeps the same width and nothing moves under the pointer.
 *
 *   page 1 of 13   1 2 3 4 5 … 13
 *   page 7 of 13   1 … 6 7 8 … 13
 *   page 13 of 13  1 … 9 10 11 12 13
 */
export function pageItems(page: number, pages: number): (number | 'gap')[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1)
  if (page <= 4) return [1, 2, 3, 4, 5, 'gap', pages]
  if (page >= pages - 3) return [1, 'gap', pages - 4, pages - 3, pages - 2, pages - 1, pages]
  return [1, 'gap', page - 1, page, page + 1, 'gap', pages]
}

export function pageCount(total: number | null, perPage: number): number {
  if (!total || perPage <= 0) return 1
  return Math.max(Math.ceil(total / perPage), 1)
}

/**
 * The pager at the foot of a table: rows per page, where you are, and the
 * page buttons. Shared by Review, Bills and Vendors so they read the same.
 */
export function Pager({ page, perPage, total, loading, onGo, onPerPage, label = 'Pages', compact = false }: {
  page: number
  perPage: number
  total: number
  loading?: boolean
  onGo: (n: number) => void
  onPerPage: (n: number) => void
  /** For screen readers: what is being paged, e.g. "Pages of bills". */
  label?: string
  /** A slimmer bar for tables where every row of height counts (Bills). */
  compact?: boolean
}) {
  const pages = pageCount(total, perPage)
  const first = total ? (page - 1) * perPage + 1 : 0
  const last = Math.min(page * perPage, total)

  return (
    <nav className={`u-pager${compact ? ' u-pager--compact' : ''}`} aria-label={label}>
      <span className="u-pager__left">
        <span className="u-pager__size">
          <Dropdown
            value={perPage}
            options={PAGE_SIZES.map(n => ({ value: n, label: String(n) }))}
            onChange={n => onPerPage(Number(n))}
            ariaLabel="Rows per page"
            disabled={loading}
          />
          <span>per page</span>
        </span>
        <span className="u-pager__count tnum">
          {total === 0 ? 'Nothing to show' : `${first}–${last} of ${total.toLocaleString(displayLocale())}`}
        </span>
      </span>
      {pages > 1 && (
        <span className="u-pager__controls">
          <button type="button" className="u-pager__btn u-pager__step"
                  disabled={page <= 1 || loading} onClick={() => onGo(page - 1)}
                  aria-label="Previous page">
            <ChevronLeft size={compact ? 14 : 15} aria-hidden="true" />
            {!compact && ' Previous'}
          </button>
          {pageItems(page, pages).map((n, i) =>
            n === 'gap' ? (
              <span key={`gap-${i}`} className="u-pager__gap" aria-hidden="true">…</span>
            ) : (
              <button
                key={n}
                type="button"
                className={`u-pager__btn u-pager__n${n === page ? ' u-pager__n--on' : ''}`}
                aria-current={n === page ? 'page' : undefined}
                aria-label={`Page ${n}`}
                disabled={loading}
                onClick={() => onGo(n)}
              >
                {n}
              </button>
            ),
          )}
          <button type="button" className="u-pager__btn u-pager__step"
                  disabled={page >= pages || loading} onClick={() => onGo(page + 1)}
                  aria-label="Next page">
            {!compact && 'Next '}
            <ChevronRight size={compact ? 14 : 15} aria-hidden="true" />
          </button>
        </span>
      )}
    </nav>
  )
}

function readSize(key: string): number {
  try {
    const n = Number(localStorage.getItem(`pager.${key}`))
    return (PAGE_SIZES as readonly number[]).includes(n) ? n : PAGE_SIZES[0]
  } catch {
    return PAGE_SIZES[0]
  }
}

/**
 * Client-side paging for a list already in memory (Bills, Vendors).
 *
 * The page size is remembered per list (`key`) in this browser. The page goes
 * back to 1 whenever `resetOn` changes — a tab, a search, a filter — since
 * page 3 of a different list is nowhere in particular; and it is pulled back
 * if the list shrinks under it.
 */
export function usePagedRows<T>(rows: T[], key: string, resetOn: unknown) {
  const [perPage, setPerPageState] = useState(() => readSize(key))
  const [page, setPage] = useState(1)
  const resetSig = JSON.stringify(resetOn)

  useEffect(() => { setPage(1) }, [resetSig])

  const pages = pageCount(rows.length, perPage)
  const current = Math.min(page, pages)
  const pageRows = useMemo(
    () => rows.slice((current - 1) * perPage, current * perPage),
    [rows, current, perPage],
  )

  const setPerPage = (n: number) => {
    try { localStorage.setItem(`pager.${key}`, String(n)) } catch { /* private mode */ }
    setPerPageState(n)
    setPage(1)
  }

  return { pageRows, page: current, perPage, total: rows.length, setPage, setPerPage }
}

/**
 * Phone counterpart to `usePagedRows`: the list grows by `step` rows as the
 * reader scrolls near its end, instead of asking them to hit page buttons.
 * Like the pager it starts over whenever `resetOn` changes.
 */
export function useInfiniteRows<T>(rows: T[], resetOn: unknown, step = 50) {
  const [count, setCount] = useState(step)
  const resetSig = JSON.stringify(resetOn)

  useEffect(() => { setCount(step) }, [resetSig, step])

  const visible = useMemo(() => rows.slice(0, count), [rows, count])
  const loadMore = useCallback(() => setCount(c => c + step), [step])

  return { rows: visible, shown: visible.length, total: rows.length, hasMore: count < rows.length, loadMore }
}

/** The nearest ancestor that scrolls vertically: the list's own viewport. */
function scrollParent(el: HTMLElement): HTMLElement | null {
  for (let p = el.parentElement; p; p = p.parentElement) {
    if (/(auto|scroll)/.test(getComputedStyle(p).overflowY)) return p
  }
  return null
}

export interface LoadMoreProps {
  shown: number
  total: number
  hasMore: boolean
  onLoadMore: () => void
  /** Plural noun for the footer, e.g. "bills". */
  unit: string
}

/**
 * Sits after the last row and asks for more once it comes within a screen of
 * view. Observed against the list's scroll container rather than the page:
 * the rows scroll inside a card, so the window never sees the sentinel move.
 * Re-observing after each batch keeps loading while it is still in view.
 */
export function LoadMore({ shown, total, hasMore, onLoadMore, unit }: LoadMoreProps) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el || !hasMore || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver(
      entries => { if (entries.some(e => e.isIntersecting)) onLoadMore() },
      { root: scrollParent(el), rootMargin: '0px 0px 600px 0px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [hasMore, onLoadMore, shown])

  if (total === 0) return null
  return (
    <div ref={ref} className="u-loadmore" role="status">
      {hasMore ? (
        // Also a button: a fallback if the observer never fires, and the
        // keyboard route for anyone not scrolling.
        <button type="button" className="u-loadmore__btn" onClick={onLoadMore}>
          Showing {shown.toLocaleString(displayLocale())} of {total.toLocaleString(displayLocale())} · load more
        </button>
      ) : (
        <span>All {total.toLocaleString(displayLocale())} {unit} shown</span>
      )}
    </div>
  )
}
