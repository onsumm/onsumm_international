import { describe as suite, it, expect } from 'vitest'
import {
  applyColumnFilters, optionsFor, isActive, describe, emptyCondition, activeCount,
  type FilterField,
} from './columnFilters'

interface Row {
  vendor: string | null
  status: string
  amount: number | null
  due: string | null
}

const rows: Row[] = [
  { vendor: 'GAIL India', status: 'APPROVED', amount: 186440, due: '2026-07-17' },
  { vendor: 'Nifty Law', status: 'PENDING_L1', amount: 75000, due: '2026-05-26' },
  { vendor: 'Onsumm Tech', status: 'APPROVED', amount: null, due: null },
  { vendor: null, status: 'REJECTED', amount: 5, due: '2026-08-04T10:30:00Z' },
]

const F: FilterField<Row>[] = [
  { key: 'vendor', label: 'Vendor', kind: 'text', value: r => r.vendor },
  { key: 'status', label: 'Status', kind: 'enum', value: r => r.status, format: s => s.replace('PENDING_', 'Pending ') },
  { key: 'amount', label: 'Bill Amount', kind: 'number', value: r => r.amount },
  { key: 'due', label: 'Due Date', kind: 'date', value: r => r.due },
]

const names = (rs: Row[]) => rs.map(r => r.vendor)

suite('isActive', () => {
  it('ignores a condition that would remove nothing', () => {
    expect(isActive(emptyCondition('text'))).toBe(false)
    expect(isActive(emptyCondition('enum'))).toBe(false)
    expect(isActive(emptyCondition('number'))).toBe(false)
    expect(isActive(emptyCondition('date'))).toBe(false)
    expect(isActive({ kind: 'text', op: 'contains', q: '   ' })).toBe(false)
  })

  it('counts the emptiness operators as active without a value', () => {
    expect(isActive({ kind: 'text', op: 'is_empty', q: '' })).toBe(true)
    expect(isActive({ kind: 'text', op: 'not_empty', q: '' })).toBe(true)
  })

  it('counts only active conditions', () => {
    expect(activeCount({ a: { kind: 'enum', values: [] }, b: { kind: 'enum', values: ['x'] } })).toBe(1)
  })
})

suite('applyColumnFilters', () => {
  it('returns the same array when nothing is active', () => {
    expect(applyColumnFilters(rows, F, {})).toBe(rows)
    expect(applyColumnFilters(rows, F, { vendor: { kind: 'text', op: 'contains', q: '' } })).toBe(rows)
  })

  it('filters text case-insensitively', () => {
    expect(names(applyColumnFilters(rows, F, { vendor: { kind: 'text', op: 'contains', q: 'gail' } })))
      .toEqual(['GAIL India'])
  })

  it('supports the negative and emptiness text operators', () => {
    expect(names(applyColumnFilters(rows, F, { vendor: { kind: 'text', op: 'is_empty', q: '' } })))
      .toEqual([null])
    // A row with no vendor does not contain anything, so it stays — matching how
    // a spreadsheet treats blanks under a negative operator.
    expect(names(applyColumnFilters(rows, F, { vendor: { kind: 'text', op: 'not_contains', q: 'a' } })))
      .toEqual(['Onsumm Tech', null])
    expect(names(applyColumnFilters(rows, F, { vendor: { kind: 'text', op: 'starts_with', q: 'ons' } })))
      .toEqual(['Onsumm Tech'])
  })

  it('ORs the values chosen within one enum column', () => {
    const out = applyColumnFilters(rows, F, { status: { kind: 'enum', values: ['APPROVED', 'REJECTED'] } })
    expect(names(out)).toEqual(['GAIL India', 'Onsumm Tech', null])
  })

  it('ANDs across columns', () => {
    const out = applyColumnFilters(rows, F, {
      status: { kind: 'enum', values: ['APPROVED'] },
      amount: { kind: 'number', min: '100000', max: '' },
    })
    expect(names(out)).toEqual(['GAIL India'])
  })

  it('excludes rows with no value from a numeric bound rather than reading them as zero', () => {
    const out = applyColumnFilters(rows, F, { amount: { kind: 'number', min: '', max: '1000' } })
    expect(names(out)).toEqual([null]) // the ₹5 row only — not the null-amount row
  })

  it('excludes rows with no date from a range', () => {
    const out = applyColumnFilters(rows, F, { due: { kind: 'date', from: '2026-01-01', to: '2026-12-31' } })
    expect(names(out)).toEqual(['GAIL India', 'Nifty Law', null])
  })

  it('compares full ISO stamps on the day', () => {
    const out = applyColumnFilters(rows, F, { due: { kind: 'date', from: '2026-08-04', to: '2026-08-04' } })
    expect(names(out)).toEqual([null])
  })

  it('is inclusive at both bounds', () => {
    expect(applyColumnFilters(rows, F, { amount: { kind: 'number', min: '5', max: '5' } })).toHaveLength(1)
    expect(applyColumnFilters(rows, F, { due: { kind: 'date', from: '2026-07-17', to: '2026-07-17' } })).toHaveLength(1)
  })
})

suite('optionsFor', () => {
  it('counts distinct values and sorts blanks last', () => {
    const opts = optionsFor(rows, F[0])
    expect(opts.map(o => o.label)).toEqual(['GAIL India', 'Nifty Law', 'Onsumm Tech', '(Blanks)'])
    expect(opts.every(o => o.count === 1)).toBe(true)
  })

  it('labels enum values the way the table does', () => {
    const opts = optionsFor(rows, F[1])
    expect(opts.find(o => o.value === 'PENDING_L1')?.label).toBe('Pending L1')
    expect(opts.find(o => o.value === 'APPROVED')?.count).toBe(2)
  })
})

suite('describe', () => {
  it('states each kind in words', () => {
    expect(describe(F[0], { kind: 'text', op: 'contains', q: 'gail' })).toBe('Vendor contains “gail”')
    expect(describe(F[0], { kind: 'text', op: 'is_empty', q: '' })).toBe('Vendor is empty')
    expect(describe(F[1], { kind: 'enum', values: ['APPROVED', 'REJECTED', 'PENDING_L1'] }))
      .toBe('Status: APPROVED, REJECTED +1')
    expect(describe(F[2], { kind: 'number', min: '1000', max: '' })).toBe('Bill Amount ≥ 1,000')
    expect(describe(F[2], { kind: 'number', min: '', max: '5000' })).toBe('Bill Amount ≤ 5,000')
  })

  it('names the blanks bucket in a chip', () => {
    expect(describe(F[1], { kind: 'enum', values: [''] })).toBe('Status: (Blanks)')
  })
})
