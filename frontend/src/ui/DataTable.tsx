import { Fragment, useMemo, useState, type ReactNode } from 'react'
import { ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-react'
import { Checkbox } from './Field'
import { SkeletonRows } from './States'

export type SortDir = 'asc' | 'desc'

export interface Column<T> {
  /** Stable key — also the sort key and the column-chooser identity. */
  key: string
  header: string
  render: (row: T) => ReactNode
  /** Value used for sorting. Omit to make the column unsortable. */
  sortValue?: (row: T) => string | number | null | undefined
  align?: 'left' | 'right' | 'center'
  /** Excluded from the column chooser and always visible. */
  pinned?: boolean
  width?: number | string
}

export interface DataTableProps<T> {
  rows: T[]
  columns: Column<T>[]
  rowKey: (row: T) => string
  loading?: boolean
  /** Rendered in the tbody when there are no rows and we are not loading. */
  empty?: ReactNode
  onRowClick?: (row: T, e: React.MouseEvent) => void
  /** Controlled selection. Omit to disable the checkbox column entirely. */
  selectedIds?: Set<string>
  onSelectionChange?: (next: Set<string>) => void
  /** Column keys to hide (driven by a column chooser). */
  hiddenColumns?: Set<string>
  density?: 'comfortable' | 'compact'
  sort?: { key: string; dir: SortDir } | null
  onSortChange?: (next: { key: string; dir: SortDir } | null) => void
  /** Accessible name for the table. */
  caption: string
  /** Rows whose expanded detail (via `renderExpanded`) is currently shown. */
  expandedIds?: Set<string>
  /** A full-width row rendered directly under a row in `expandedIds`. Omit
   *  both this and `expandedIds` to leave the table exactly as before. */
  renderExpanded?: (row: T) => ReactNode
}

const isEmpty = (v: unknown) => v === null || v === undefined || v === ''

/** Compares two present values. Emptiness is handled by the caller — see below. */
function compare(a: unknown, b: unknown): number {
  if (typeof a === 'number' && typeof b === 'number') return a - b
  return String(a).localeCompare(String(b), 'en', { numeric: true, sensitivity: 'base' })
}

/**
 * The table primitive.
 *
 * Adds what the audit found missing on a 15-column financial grid: per-column
 * sorting with `aria-sort`, a caption, `scope="col"` headers, keyboard-operable
 * sort controls, a density switch, and column visibility — none of which
 * existed before.
 */
export function DataTable<T>({
  rows,
  columns,
  rowKey,
  loading,
  empty,
  onRowClick,
  selectedIds,
  onSelectionChange,
  hiddenColumns,
  density = 'comfortable',
  sort,
  onSortChange,
  caption,
  expandedIds,
  renderExpanded,
}: DataTableProps<T>) {
  // Uncontrolled fallback so the table is useful without wiring sort state.
  const [innerSort, setInnerSort] = useState<{ key: string; dir: SortDir } | null>(null)
  const activeSort = sort !== undefined ? sort : innerSort
  const setSort = onSortChange ?? setInnerSort

  const visible = useMemo(
    () => columns.filter(c => c.pinned || !hiddenColumns?.has(c.key)),
    [columns, hiddenColumns],
  )

  const sorted = useMemo(() => {
    if (!activeSort) return rows
    const col = columns.find(c => c.key === activeSort.key)
    if (!col?.sortValue) return rows
    const dir = activeSort.dir === 'asc' ? 1 : -1
    // Copy first — sorting the caller's array in place is a classic React bug.
    return [...rows].sort((a, b) => {
      const av = col.sortValue!(a)
      const bv = col.sortValue!(b)
      // Empty values sink to the bottom in BOTH directions. Applying the
      // direction multiplier to them would float "unknown" to the top on a
      // descending sort — so a bill with no due date would outrank a genuinely
      // urgent one.
      const aE = isEmpty(av)
      const bE = isEmpty(bv)
      if (aE && bE) return 0
      if (aE) return 1
      if (bE) return -1
      return compare(av, bv) * dir
    })
  }, [rows, columns, activeSort])

  const selectable = !!selectedIds && !!onSelectionChange
  const pageIds = sorted.map(rowKey)
  const allChecked = selectable && pageIds.length > 0 && pageIds.every(id => selectedIds!.has(id))
  const someChecked = selectable && !allChecked && pageIds.some(id => selectedIds!.has(id))

  const toggleAll = () => {
    if (!selectable) return
    const next = new Set(selectedIds!)
    if (allChecked) pageIds.forEach(id => next.delete(id))
    else pageIds.forEach(id => next.add(id))
    onSelectionChange!(next)
  }

  const toggleOne = (id: string) => {
    if (!selectable) return
    const next = new Set(selectedIds!)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    onSelectionChange!(next)
  }

  const cycleSort = (key: string) => {
    if (activeSort?.key !== key) return setSort({ key, dir: 'asc' })
    if (activeSort.dir === 'asc') return setSort({ key, dir: 'desc' })
    return setSort(null) // third click clears — back to natural order
  }

  const colCount = visible.length + (selectable ? 1 : 0)

  return (
    <div className="u-table-wrap">
      <table className={`u-table${density === 'compact' ? ' u-table--compact' : ''}`}>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {selectable && (
              <th scope="col" style={{ width: 40 }}>
                <Checkbox
                  label={<span className="sr-only">Select all rows</span>}
                  checked={allChecked}
                  indeterminate={someChecked}
                  onChange={toggleAll}
                />
              </th>
            )}
            {visible.map(col => {
              const isSorted = activeSort?.key === col.key
              const ariaSort = isSorted ? (activeSort!.dir === 'asc' ? 'ascending' : 'descending') : 'none'
              return (
                <th
                  key={col.key}
                  scope="col"
                  style={{ width: col.width, textAlign: col.align ?? 'left' }}
                  aria-sort={col.sortValue ? ariaSort : undefined}
                >
                  {col.sortValue ? (
                    <button
                      type="button"
                      className={`u-table__sort${isSorted ? ' u-table__sort--active' : ''}`}
                      onClick={() => cycleSort(col.key)}
                      // Screen readers get the current state from aria-sort on
                      // the th; this tells them what the click will do.
                      aria-label={`${col.header}, ${isSorted ? `sorted ${ariaSort}` : 'not sorted'}. Activate to sort.`}
                    >
                      {col.header}
                      <span className="u-table__sort-icon" aria-hidden="true" style={{ display: 'flex' }}>
                        {!isSorted ? (
                          <ChevronsUpDown size={12} />
                        ) : activeSort!.dir === 'asc' ? (
                          <ChevronUp size={12} />
                        ) : (
                          <ChevronDown size={12} />
                        )}
                      </span>
                    </button>
                  ) : (
                    col.header
                  )}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {loading && <SkeletonRows cols={colCount} />}

          {!loading && sorted.length === 0 && (
            <tr>
              <td colSpan={colCount} style={{ padding: 0 }}>
                {empty}
              </td>
            </tr>
          )}

          {!loading &&
            sorted.map(row => {
              const id = rowKey(row)
              const selected = selectable && selectedIds!.has(id)
              const expanded = !!expandedIds?.has(id) && !!renderExpanded
              return (
                <Fragment key={id}>
                  <tr
                    className={[
                      onRowClick ? 'u-table__row--clickable' : '',
                      selected ? 'u-table__row--selected' : '',
                    ]
                      .filter(Boolean)
                      .join(' ')}
                    onClick={onRowClick ? e => onRowClick(row, e) : undefined}
                  >
                    {selectable && (
                      <td onClick={e => e.stopPropagation()}>
                        <Checkbox
                          label={<span className="sr-only">Select row</span>}
                          checked={selected}
                          onChange={() => toggleOne(id)}
                        />
                      </td>
                    )}
                    {visible.map(col => (
                      <td key={col.key} style={{ textAlign: col.align ?? 'left' }}>
                        {col.render(row)}
                      </td>
                    ))}
                  </tr>
                  {expanded && (
                    <tr className="u-table__row--expanded">
                      <td colSpan={colCount} style={{ padding: 0 }}>
                        {renderExpanded!(row)}
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
        </tbody>
      </table>
    </div>
  )
}
