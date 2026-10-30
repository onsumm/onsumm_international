/**
 * Per-column filtering, spreadsheet-style.
 *
 * Bills had one fixed filter panel — status, vendor, GSTIN, two date ranges, an
 * amount range — and Vendors had a single search box, so any question the panel's
 * author had not anticipated ("approved bills from this uploader with no cost
 * centre", "vendors on 60-day terms with nothing outstanding") could not be asked
 * at all. This is the general form: every column carries a filter, one condition
 * per column, ANDed across columns.
 *
 * Pure functions on rows already in memory. Both pages load their full list and
 * filter client-side, so nothing here touches the network; there is no page of
 * server-side rows for a filter to silently miss.
 */
import { displayLocale } from './format'

export type FilterKind = 'text' | 'enum' | 'number' | 'date'

export interface FilterField<T> {
  /** Stable key — also the condition map key. */
  key: string
  label: string
  kind: FilterKind
  /** The comparable value for a row. Dates as ISO (`2026-08-04`, or full ISO). */
  value: (row: T) => string | number | null | undefined
  /** Turns a raw enum value into the label the table itself shows. */
  format?: (raw: string) => string
}

export type TextOp = 'contains' | 'not_contains' | 'equals' | 'starts_with' | 'is_empty' | 'not_empty'

export const TEXT_OPS: { op: TextOp; label: string; needsValue: boolean }[] = [
  { op: 'contains', label: 'contains', needsValue: true },
  { op: 'not_contains', label: 'does not contain', needsValue: true },
  { op: 'equals', label: 'is exactly', needsValue: true },
  { op: 'starts_with', label: 'starts with', needsValue: true },
  { op: 'is_empty', label: 'is empty', needsValue: false },
  { op: 'not_empty', label: 'is not empty', needsValue: false },
]

export interface TextCondition { kind: 'text'; op: TextOp; q: string }
/** Chosen values, ORed within the column. `''` is the blanks bucket. */
export interface EnumCondition { kind: 'enum'; values: string[] }
export interface NumberCondition { kind: 'number'; min: string; max: string }
export interface DateCondition { kind: 'date'; from: string; to: string }

export type Condition = TextCondition | EnumCondition | NumberCondition | DateCondition
/** Keyed by field key: at most one condition per column, as in a spreadsheet. */
export type ConditionMap = Record<string, Condition>

export function emptyCondition(kind: FilterKind): Condition {
  switch (kind) {
    case 'enum': return { kind: 'enum', values: [] }
    case 'number': return { kind: 'number', min: '', max: '' }
    case 'date': return { kind: 'date', from: '', to: '' }
    default: return { kind: 'text', op: 'contains', q: '' }
  }
}

/** A condition that would not remove any row is not applied at all. */
export function isActive(c: Condition | undefined): boolean {
  if (!c) return false
  switch (c.kind) {
    case 'text': return c.op === 'is_empty' || c.op === 'not_empty' || c.q.trim() !== ''
    case 'enum': return c.values.length > 0
    case 'number': return c.min.trim() !== '' || c.max.trim() !== ''
    case 'date': return c.from !== '' || c.to !== ''
  }
}

export function activeCount(conds: ConditionMap): number {
  return Object.values(conds).filter(isActive).length
}

const str = (v: unknown) => (v === null || v === undefined ? '' : String(v))
/** Dates arrive as `2026-08-04` or a full ISO stamp; both compare on the day. */
const day = (v: unknown) => str(v).slice(0, 10)

function matchesText(c: TextCondition, raw: unknown): boolean {
  const v = str(raw).toLowerCase()
  if (c.op === 'is_empty') return v === ''
  if (c.op === 'not_empty') return v !== ''
  const q = c.q.trim().toLowerCase()
  if (q === '') return true
  switch (c.op) {
    case 'contains': return v.includes(q)
    case 'not_contains': return !v.includes(q)
    case 'equals': return v === q
    case 'starts_with': return v.startsWith(q)
  }
}

function matchesNumber(c: NumberCondition, raw: unknown): boolean {
  // A row with no value cannot satisfy a bound. Treating it as 0 would put every
  // amount-less bill inside "under ₹1,000", which reads as a real answer.
  if (raw === null || raw === undefined || raw === '') return false
  const n = typeof raw === 'number' ? raw : parseFloat(String(raw))
  if (!Number.isFinite(n)) return false
  const min = parseFloat(c.min)
  const max = parseFloat(c.max)
  if (Number.isFinite(min) && n < min) return false
  if (Number.isFinite(max) && n > max) return false
  return true
}

function matchesDate(c: DateCondition, raw: unknown): boolean {
  const d = day(raw)
  if (d === '') return false // same reasoning as an absent number
  if (c.from && d < c.from) return false
  if (c.to && d > c.to) return false
  return true
}

export function matches<T>(field: FilterField<T>, c: Condition, row: T): boolean {
  const raw = field.value(row)
  switch (c.kind) {
    case 'text': return matchesText(c, raw)
    case 'enum': return c.values.includes(str(raw))
    case 'number': return matchesNumber(c, raw)
    case 'date': return matchesDate(c, raw)
  }
}

/** Every active condition must hold — AND across columns, OR within one. */
export function applyColumnFilters<T>(rows: T[], fields: FilterField<T>[], conds: ConditionMap): T[] {
  const live = fields
    .map(f => ({ field: f, cond: conds[f.key] }))
    .filter((p): p is { field: FilterField<T>; cond: Condition } => isActive(p.cond))
  if (live.length === 0) return rows
  return rows.filter(row => live.every(p => matches(p.field, p.cond, row)))
}

export interface FilterOption { value: string; label: string; count: number }

/**
 * The distinct values in this column, with counts — the checkbox list.
 *
 * Counted over the rows passed in, so the caller decides whether the list
 * reflects the whole table or what the other filters have already narrowed.
 */
export function optionsFor<T>(rows: T[], field: FilterField<T>): FilterOption[] {
  const counts = new Map<string, number>()
  for (const row of rows) {
    const v = str(field.value(row))
    counts.set(v, (counts.get(v) ?? 0) + 1)
  }
  const out: FilterOption[] = []
  for (const [value, count] of counts) {
    out.push({ value, label: value === '' ? '(Blanks)' : field.format?.(value) ?? value, count })
  }
  return out.sort((a, b) => {
    // Blanks last: they are the absence of a value, not the first one.
    if (a.value === '') return 1
    if (b.value === '') return -1
    return a.label.localeCompare(b.label, 'en', { numeric: true, sensitivity: 'base' })
  })
}

const fmtDay = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString(displayLocale(), { day: '2-digit', month: 'short', year: 'numeric' })

const fmtNum = (s: string) => {
  const n = parseFloat(s)
  return Number.isFinite(n) ? n.toLocaleString(displayLocale()) : s
}

/** What this filter is doing, in words — the status line and its tooltip. */
export function describe<T>(field: FilterField<T>, c: Condition): string {
  switch (c.kind) {
    case 'text': {
      const op = TEXT_OPS.find(o => o.op === c.op)
      return op?.needsValue === false ? `${field.label} ${op.label}` : `${field.label} ${op?.label} “${c.q.trim()}”`
    }
    case 'enum': {
      const labels = c.values.map(v => (v === '' ? '(Blanks)' : field.format?.(v) ?? v))
      const head = labels.slice(0, 2).join(', ')
      return `${field.label}: ${head}${labels.length > 2 ? ` +${labels.length - 2}` : ''}`
    }
    case 'number': {
      if (c.min && c.max) return `${field.label} ${fmtNum(c.min)}–${fmtNum(c.max)}`
      if (c.min) return `${field.label} ≥ ${fmtNum(c.min)}`
      return `${field.label} ≤ ${fmtNum(c.max)}`
    }
    case 'date': {
      if (c.from && c.to) return `${field.label} ${fmtDay(c.from)} – ${fmtDay(c.to)}`
      if (c.from) return `${field.label} on or after ${fmtDay(c.from)}`
      return `${field.label} on or before ${fmtDay(c.to)}`
    }
  }
}
