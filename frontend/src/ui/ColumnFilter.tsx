/**
 * Excel's AutoFilter, on our tables.
 *
 * The people using this spent years in spreadsheets, so the interaction is
 * copied rather than reinterpreted: click the column header, get the dropdown —
 * Sort A to Z, Sort Z to A, Clear Filter, a Text/Number/Date Filters submenu, a
 * search box, and the checkbox list of every value in the column with
 * (Select All) and (Blanks). Nothing is applied until OK; Cancel throws the draft
 * away. A column that is filtering is highlighted, exactly as a filtered column
 * is called out in a sheet.
 *
 * No permanent glyphs in the headers: the header itself is the control, and the
 * highlight is the state.
 *
 * The dropdown is portalled to the body and positioned from the header's rect —
 * both tables scroll inside `overflow: auto`, which would clip a menu anchored
 * in the cell.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ArrowDownAZ, ArrowUpZA, ChevronRight, ChevronLeft, Search, FilterX, Filter } from 'lucide-react'
import {
  TEXT_OPS, applyColumnFilters, activeCount, describe, emptyCondition, isActive, optionsFor,
  type Condition, type ConditionMap, type FilterField, type FilterOption, type TextCondition, type TextOp,
} from './columnFilters'
import { displayLocale } from './format'

export type SortState = { key: string; dir: 'asc' | 'desc' } | null

/* ── State ──────────────────────────────────────────────────────────────────── */

export interface ColumnFilterState<T> {
  fields: FilterField<T>[]
  conditions: ConditionMap
  set: (key: string, next: Condition | null) => void
  clearAll: () => void
  count: number
  sort: SortState
  setSort: (next: SortState) => void
  /** Rows through every active condition, then the column sort. */
  apply: (rows: T[]) => T[]
  /**
   * Rows with every filter applied EXCEPT this column's — the set a spreadsheet
   * builds one column's value list from, so a count of 12 next to "Approved"
   * means twelve rows you would actually see.
   */
  rowsExcluding: (key: string, rows: T[]) => T[]
}

const isBlank = (v: unknown) => v === null || v === undefined || v === ''

function compare(a: unknown, b: unknown): number {
  if (typeof a === 'number' && typeof b === 'number') return a - b
  return String(a).localeCompare(String(b), 'en', { numeric: true, sensitivity: 'base' })
}

export function useColumnFilters<T>(fields: FilterField<T>[]): ColumnFilterState<T> {
  const [conditions, setConditions] = useState<ConditionMap>({})
  const [sort, setSort] = useState<SortState>(null)

  const set = useCallback((key: string, next: Condition | null) => {
    setConditions(prev => {
      const copy = { ...prev }
      if (next === null || !isActive(next)) delete copy[key]
      else copy[key] = next
      return copy
    })
  }, [])

  const clearAll = useCallback(() => setConditions({}), [])

  const apply = useCallback(
    (rows: T[]) => {
      const filtered = applyColumnFilters(rows, fields, conditions)
      if (!sort) return filtered
      const field = fields.find(f => f.key === sort.key)
      if (!field) return filtered
      const dir = sort.dir === 'asc' ? 1 : -1
      // Copy before sorting, and sink blanks in both directions — a row with no
      // due date is not the most urgent one just because the sort is descending.
      return [...filtered].sort((a, b) => {
        const av = field.value(a)
        const bv = field.value(b)
        if (isBlank(av) && isBlank(bv)) return 0
        if (isBlank(av)) return 1
        if (isBlank(bv)) return -1
        return compare(av, bv) * dir
      })
    },
    [fields, conditions, sort],
  )

  const rowsExcluding = useCallback(
    (key: string, rows: T[]) => {
      const others = { ...conditions }
      delete others[key]
      return applyColumnFilters(rows, fields, others)
    },
    [fields, conditions],
  )

  return useMemo(
    () => ({
      fields, conditions, set, clearAll, count: activeCount(conditions), sort, setSort, apply, rowsExcluding,
    }),
    [fields, conditions, set, clearAll, sort, apply, rowsExcluding],
  )
}

/* ── Dropdown shell ─────────────────────────────────────────────────────────── */

function Dropdown({
  anchor, onDismiss, children,
}: { anchor: HTMLElement; onDismiss: () => void; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)

  // Measured after mount, so the menu's own size decides whether it flips.
  useLayoutEffect(() => {
    const r = anchor.getBoundingClientRect()
    const box = ref.current?.getBoundingClientRect()
    const w = box?.width ?? 264
    const h = box?.height ?? 380
    const left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8))
    const below = r.bottom + 2
    const top = below + h > window.innerHeight - 8 ? Math.max(8, r.top - h - 2) : below
    setPos({ top, left })
  }, [anchor])

  useEffect(() => {
    // Escape and a click outside both mean Cancel, as in a sheet.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onDismiss()
      }
    }
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (!ref.current?.contains(t) && !anchor.contains(t)) onDismiss()
    }
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('mousedown', onDown)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('mousedown', onDown)
    }
  }, [anchor, onDismiss])

  return createPortal(
    <div
      ref={ref}
      className="u-xl"
      role="dialog"
      aria-label="Column filter"
      style={pos ? { top: pos.top, left: pos.left } : { top: 0, left: 0, visibility: 'hidden' }}
    >
      {children}
    </div>,
    document.body,
  )
}

/* ── Draft ──────────────────────────────────────────────────────────────────── */

type Draft =
  | { mode: 'values'; checked: Set<string> }
  | { mode: 'custom'; cond: Condition }

function initialDraft(active: Condition | undefined, all: FilterOption[]): Draft {
  if (active?.kind === 'enum') return { mode: 'values', checked: new Set(active.values) }
  if (active) return { mode: 'custom', cond: active }
  // No filter yet: every box ticked, which is what a sheet shows.
  return { mode: 'values', checked: new Set(all.map(o => o.value)) }
}

const CUSTOM_LABEL: Record<string, string> = {
  text: 'Text Filters',
  number: 'Number Filters',
  date: 'Date Filters',
}

/* ── The menu ───────────────────────────────────────────────────────────────── */

function AutoFilterMenu<T>({
  state, field, rows, anchor, onDismiss,
}: {
  state: ColumnFilterState<T>
  field: FilterField<T>
  rows: T[]
  anchor: HTMLElement
  onDismiss: () => void
}) {
  // Values from the rows the other columns already allow — Excel's behaviour.
  const options = useMemo(
    () => optionsFor(state.rowsExcluding(field.key, rows), field),
    [state, field, rows],
  )
  const active = state.conditions[field.key]
  const [draft, setDraft] = useState<Draft>(() => initialDraft(active, options))
  const [q, setQ] = useState('')

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return needle ? options.filter(o => o.label.toLowerCase().includes(needle)) : options
  }, [options, q])

  const checked = draft.mode === 'values' ? draft.checked : new Set<string>()
  const shownAllOn = shown.length > 0 && shown.every(o => checked.has(o.value))
  const shownSomeOn = !shownAllOn && shown.some(o => checked.has(o.value))

  const setChecked = (next: Set<string>) => setDraft({ mode: 'values', checked: next })

  const toggleValue = (v: string) => {
    const next = new Set(checked)
    if (next.has(v)) next.delete(v)
    else next.add(v)
    setChecked(next)
  }

  const toggleShown = () => {
    const next = new Set(checked)
    // With a search in the box this applies to the matches only — the rest of the
    // selection is left alone, so narrowing then ticking is additive.
    if (shownAllOn) shown.forEach(o => next.delete(o.value))
    else shown.forEach(o => next.add(o.value))
    setChecked(next)
  }

  const commit = () => {
    if (draft.mode === 'custom') state.set(field.key, draft.cond)
    else if (draft.checked.size === options.length) state.set(field.key, null) // all ticked = no filter
    else state.set(field.key, { kind: 'enum', values: [...draft.checked] })
    onDismiss()
  }

  const sortAsc = state.sort?.key === field.key && state.sort.dir === 'asc'
  const sortDesc = state.sort?.key === field.key && state.sort.dir === 'desc'
  const applySort = (dir: 'asc' | 'desc') => {
    state.setSort({ key: field.key, dir })
    onDismiss()
  }

  return (
    <Dropdown anchor={anchor} onDismiss={onDismiss}>
      <div
        className="u-xl__body"
        onKeyDown={e => {
          if (e.key === 'Enter' && !(e.target as HTMLElement).matches('button')) {
            e.preventDefault()
            commit()
          }
        }}
      >
        <button type="button" className={`u-xl__item${sortAsc ? ' u-xl__item--on' : ''}`} onClick={() => applySort('asc')}>
          <ArrowDownAZ size={14} aria-hidden="true" /> Sort A to Z
        </button>
        <button type="button" className={`u-xl__item${sortDesc ? ' u-xl__item--on' : ''}`} onClick={() => applySort('desc')}>
          <ArrowUpZA size={14} aria-hidden="true" /> Sort Z to A
        </button>

        <div className="u-xl__sep" />

        <button
          type="button"
          className="u-xl__item"
          disabled={!isActive(active)}
          onClick={() => {
            state.set(field.key, null)
            onDismiss()
          }}
        >
          <FilterX size={14} aria-hidden="true" /> Clear filter from “{field.label}”
        </button>

        {field.kind !== 'enum' && draft.mode === 'values' && (
          <button
            type="button"
            className="u-xl__item"
            onClick={() => setDraft({ mode: 'custom', cond: active && active.kind !== 'enum' ? active : emptyCondition(field.kind) })}
          >
            {CUSTOM_LABEL[field.kind]}
            <ChevronRight size={14} aria-hidden="true" className="u-xl__item-more" />
          </button>
        )}

        <div className="u-xl__sep" />

        {draft.mode === 'custom' ? (
          <>
            <button
              type="button"
              className="u-xl__back"
              onClick={() => setDraft(initialDraft(undefined, options))}
            >
              <ChevronLeft size={13} aria-hidden="true" /> {CUSTOM_LABEL[field.kind]}
            </button>
            <CustomPanel field={field} cond={draft.cond} onChange={c => setDraft({ mode: 'custom', cond: c })} />
          </>
        ) : (
          <>
            <div className="u-xl__search">
              <Search size={13} aria-hidden="true" />
              <input
                autoFocus
                value={q}
                onChange={e => setQ(e.target.value)}
                placeholder="Search"
                aria-label={`Search ${field.label} values`}
              />
            </div>

            <div className="u-xl__list" role="group" aria-label={`${field.label} values`}>
              {shown.length === 0 && <p className="u-xl__none">No matches.</p>}

              {shown.length > 0 && (
                <label className="u-xl__opt u-xl__opt--all">
                  <input
                    type="checkbox"
                    checked={shownAllOn}
                    ref={el => { if (el) el.indeterminate = shownSomeOn }}
                    onChange={toggleShown}
                  />
                  <span className="u-xl__opt-label">(Select All{q ? ' matches' : ''})</span>
                </label>
              )}

              {shown.map(o => (
                <label key={o.value} className="u-xl__opt">
                  <input type="checkbox" checked={checked.has(o.value)} onChange={() => toggleValue(o.value)} />
                  <span className={`u-xl__opt-label${o.value === '' ? ' u-xl__opt-label--blank' : ''}`}>
                    {o.value === '' ? '(Blanks)' : o.label}
                  </span>
                  <span className="u-xl__opt-count">{o.count}</span>
                </label>
              ))}
            </div>
          </>
        )}

        <div className="u-xl__foot">
          <button type="button" className="u-xl__cancel" onClick={onDismiss}>Cancel</button>
          <button type="button" className="u-xl__ok" onClick={commit}>OK</button>
        </div>
      </div>
    </Dropdown>
  )
}

/* ── Text / Number / Date panels ────────────────────────────────────────────── */

function CustomPanel<T>({
  field, cond, onChange,
}: { field: FilterField<T>; cond: Condition; onChange: (c: Condition) => void }) {
  if (field.kind === 'text') {
    const c: TextCondition = cond.kind === 'text' ? cond : { kind: 'text', op: 'contains', q: '' }
    const op = TEXT_OPS.find(o => o.op === c.op)
    return (
      <div className="u-xl__form">
        <label className="u-xl__lbl">
          <span>Show rows where</span>
          <select value={c.op} onChange={e => onChange({ ...c, op: e.target.value as TextOp })}>
            {TEXT_OPS.map(o => <option key={o.op} value={o.op}>{o.label}</option>)}
          </select>
        </label>
        {op?.needsValue && (
          <input
            autoFocus
            className="u-xl__input"
            value={c.q}
            onChange={e => onChange({ ...c, q: e.target.value })}
            aria-label={`${field.label} value`}
          />
        )}
      </div>
    )
  }

  if (field.kind === 'number') {
    const c = cond.kind === 'number' ? cond : { kind: 'number' as const, min: '', max: '' }
    return (
      <div className="u-xl__form u-xl__form--pair">
        <label className="u-xl__lbl">
          <span>At least</span>
          <input
            autoFocus type="number" inputMode="decimal" className="u-xl__input"
            value={c.min} onChange={e => onChange({ ...c, min: e.target.value })}
          />
        </label>
        <label className="u-xl__lbl">
          <span>At most</span>
          <input
            type="number" inputMode="decimal" className="u-xl__input"
            value={c.max} onChange={e => onChange({ ...c, max: e.target.value })}
          />
        </label>
      </div>
    )
  }

  const c = cond.kind === 'date' ? cond : { kind: 'date' as const, from: '', to: '' }
  return (
    <div className="u-xl__form u-xl__form--pair">
      <label className="u-xl__lbl">
        <span>From</span>
        <input autoFocus type="date" className="u-xl__input" value={c.from} onChange={e => onChange({ ...c, from: e.target.value })} />
      </label>
      <label className="u-xl__lbl">
        <span>To</span>
        <input type="date" className="u-xl__input" value={c.to} onChange={e => onChange({ ...c, to: e.target.value })} />
      </label>
    </div>
  )
}

/* ── The header cell ────────────────────────────────────────────────────────── */

export interface FilterThProps<T> {
  state: ColumnFilterState<T>
  /** Which field this header filters. An unknown key renders a plain header. */
  fieldKey: string
  /** Rows the value list and counts are drawn from. */
  rows: T[]
  children: React.ReactNode
  align?: 'left' | 'right' | 'center'
  className?: string
  style?: React.CSSProperties
  colSpan?: number
}

/**
 * A `<th>` whose label opens the AutoFilter menu. Replaces the plain `<th>` in a
 * hand-rolled table, so the page keeps its own header styling and stickiness.
 */
export function FilterTh<T>({
  state, fieldKey, rows, children, align, className, style, colSpan,
}: FilterThProps<T>) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const field = state.fields.find(f => f.key === fieldKey)
  const on = isActive(state.conditions[fieldKey])
  const sorted = state.sort?.key === fieldKey

  const cls = [
    'u-xl-th',
    on ? 'u-xl-th--on' : '',
    sorted && !on ? 'u-xl-th--sorted' : '',
    className ?? '',
  ].filter(Boolean).join(' ')

  if (!field) {
    return <th className={className} style={{ textAlign: align, ...style }} colSpan={colSpan}>{children}</th>
  }

  return (
    <th
      className={cls}
      style={{ textAlign: align, ...style }}
      colSpan={colSpan}
      aria-sort={sorted ? (state.sort!.dir === 'asc' ? 'ascending' : 'descending') : undefined}
    >
      <button
        type="button"
        className="u-xl-th__btn"
        aria-haspopup="dialog"
        aria-expanded={!!anchor}
        title={on ? `Filtered — click to change` : `Click to sort or filter ${field.label}`}
        onClick={e => {
          e.stopPropagation()
          setAnchor(prev => (prev ? null : e.currentTarget))
        }}
      >
        {children}
        {/* Always shown, faint until hover or filtering: the only hint that
            the header is a control and not just a label. */}
        <Filter size={10} strokeWidth={2.4} className="u-xl-th__icon" aria-hidden="true" />
        {on && <span className="sr-only">(filtered)</span>}
      </button>
      {anchor && (
        <AutoFilterMenu
          state={state}
          field={field}
          rows={rows}
          anchor={anchor}
          onDismiss={() => setAnchor(null)}
        />
      )}
    </th>
  )
}

/* ── Status line ────────────────────────────────────────────────────────────── */

/**
 * The sheet's status bar: how many rows survived, and the way back out. Shown
 * only while something is filtering — a highlighted header explains which column,
 * this explains the cost.
 */
export function FilterStatus<T>({
  state, total, shown, unit = 'rows',
}: { state: ColumnFilterState<T>; total: number; shown: number; unit?: string }) {
  if (state.count === 0) return null
  const live = state.fields.filter(f => isActive(state.conditions[f.key]))
  return (
    <div className="u-xl-status" aria-live="polite">
      <strong>{shown.toLocaleString(displayLocale())}</strong> of {total.toLocaleString(displayLocale())} {unit} —
      filtered by{' '}
      {/* Names the columns; the title spells each condition out in full. */}
      <span title={live.map(f => describe(f, state.conditions[f.key])).join(' · ')}>
        {live.map(f => f.label).join(', ')}
      </span>
      <button type="button" onClick={state.clearAll}>Clear filters</button>
    </div>
  )
}
