/**
 * RawEvidence is the component that makes open-ended detection safe to display:
 * a finding type invented at runtime lands here. So these tests are deliberately
 * hostile — the bar is that it never throws, never renders empty, and never
 * prints "[object Object]" at a finance user.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { EvidenceView, RawEvidence } from './EvidenceView'

const hostile: [string, unknown][] = [
  ['null', null],
  ['undefined', undefined],
  ['an empty object', {}],
  ['an empty array', []],
  ['a bare string', 'something went wrong'],
  ['a number', 42],
  ['a boolean', false],
  ['a deeply nested object', { a: { b: { c: { d: { e: 'deep' } } } } }],
  ['an array of objects', [{ x: 1 }, { x: 2 }]],
  ['mixed nulls', { a: null, b: undefined, c: 0, d: '' }],
  ['a very long string', { note: 'x'.repeat(5000) }],
  ['keys that collide with our own', { kind: 'diff', rows: 'not an array' }],
  ['numeric keys', { 1: 'one', 2: 'two' }],
  ['a circular-ish shape', { self: { self: { self: 'stop' } } }],
]

describe('RawEvidence survives anything', () => {
  it.each(hostile)('renders %s without throwing', (_name, payload) => {
    expect(() => render(<RawEvidence data={payload} />)).not.toThrow()
  })

  it('never prints [object Object]', () => {
    const { container } = render(<RawEvidence data={{ nested: { a: 1 }, plain: 'ok' }} />)
    expect(container.textContent).not.toContain('[object Object]')
  })

  it('says something rather than nothing when there is no detail', () => {
    const { container } = render(<RawEvidence data={{}} />)
    expect(container.textContent?.trim().length).toBeGreaterThan(0)
  })

  it('humanises keys instead of showing snake_case', () => {
    render(<RawEvidence data={{ vendor_name: 'Acme Ltd' }} />)
    expect(screen.getByText(/vendor name/i)).toBeTruthy()
  })

  it('formats money-ish keys as currency', () => {
    const { container } = render(<RawEvidence data={{ total_amount: 1240000 }} />)
    expect(container.textContent).toMatch(/₹/)
  })
})

describe('EvidenceView dispatches on shape, not on problem type', () => {
  it('renders a diff', () => {
    render(<EvidenceView evidence={{
      kind: 'diff', left_label: 'On this bill', right_label: 'Vendor master',
      rows: [{ label: 'GSTIN', left: '27AABCU9603R1ZX', right: '29AABCU9603R1ZJ' }],
    }} />)
    expect(screen.getByText('On this bill')).toBeTruthy()
    expect(screen.getByText('27AABCU9603R1ZX')).toBeTruthy()
  })

  it('renders a table', () => {
    render(<EvidenceView evidence={{
      kind: 'table', columns: ['Module', 'Variance'], rows: [['PAN', '12']],
    }} />)
    expect(screen.getByRole('columnheader', { name: 'Module' })).toBeTruthy()
  })

  it('renders a timeline and labels each step for a screen reader', () => {
    render(<EvidenceView evidence={{
      kind: 'timeline',
      steps: [
        { label: 'Submitted', state: 'done' },
        { label: 'Approver', state: 'blocked', actor: 'Someone Who Left' },
      ],
    }} />)
    expect(screen.getByText('Submitted')).toBeTruthy()
    expect(screen.getByText('blocked')).toBeTruthy()
  })

  it('formats every money field, not just the ones called "amount"', () => {
    // An outstanding balance rendered as 1833167 next to a ₹18,33,167 total is
    // the kind of detail that makes a finance user stop trusting the screen.
    render(<EvidenceView evidence={{
      kind: 'fields',
      fields: [
        { label: 'Total amount', value: 1833167 },
        { label: 'Outstanding', value: 1833167 },
        { label: 'Net payable', value: 1833167 },
      ],
    }} />)
    expect(screen.getAllByText('₹18,33,167').length).toBe(3)
  })

  it('does not print the actor twice when the step is named after them', () => {
    render(<EvidenceView evidence={{
      kind: 'timeline',
      steps: [{ label: 'Mohd Salman', actor: 'Mohd Salman', state: 'current' }],
    }} />)
    expect(screen.getAllByText('Mohd Salman').length).toBe(1)
  })

  it('shows when an audit step happened and what the approver said', () => {
    // The same shape carries both a plan and an audit trail. A step that has a
    // time and a remark must show them; one that has neither is unchanged.
    render(<EvidenceView evidence={{
      kind: 'timeline',
      steps: [
        { label: 'Send Back', state: 'blocked', actor: 'Asha · Finance Controller',
          at: '2026-06-25', note: 'PO missing' },
        { label: 'Pending Approver', state: 'current' },
      ],
    }} />)
    expect(screen.getByText('PO missing')).toBeTruthy()
    expect(screen.getByText('Asha · Finance Controller')).toBeTruthy()
    expect(screen.getByText('Pending Approver')).toBeTruthy()
  })

  it('falls back to the raw renderer for an unknown shape', () => {
    // This is the case that matters: a detector nobody anticipated.
    const { container } = render(<EvidenceView evidence={{
      kind: 'some_future_shape', whatever_it_found: 'a thing', confidence_score: 0.8,
    }} />)
    expect(container.textContent).toContain('a thing')
    expect(container.textContent).not.toContain('[object Object]')
  })

  it('falls back when a known shape has the wrong inner types', () => {
    const { container } = render(<EvidenceView evidence={{ kind: 'table', rows: 'nope' }} />)
    expect(container.textContent?.length).toBeGreaterThan(0)
  })

  it('renders nothing at all for empty evidence', () => {
    const { container } = render(<EvidenceView evidence={{}} />)
    expect(container.innerHTML).toBe('')
  })
})

describe('CodeDiff shows the mismatch rather than describing it', () => {
  // The real case: OCR read 09AAKCP6454B1ZE for 09AAKCP4645B1ZE.
  const WRONG = '09AAKCP6454B1ZE'
  const RIGHT = '09AAKCP4645B1ZE'

  const payload = {
    kind: 'code_diff',
    left: { label: 'Read from the bill', value: WRONG, status: 'failed' },
    right: { label: 'On the verified vendor', value: RIGHT, status: 'verified' },
    facts: [{ label: 'Bills attached', left: '1', right: '2' }],
  }

  it('marks exactly the characters that differ', () => {
    const { container } = render(<EvidenceView evidence={payload} />)
    const bad = container.querySelectorAll('.rh__cd-ch--bad')
    const good = container.querySelectorAll('.rh__cd-ch--good')
    // positions 8, 9, 10, 11 (1-indexed) — four substitutions
    expect(bad).toHaveLength(4)
    expect(good).toHaveLength(4)
    expect([...bad].map(n => n.textContent).join('')).toBe('6454')
    expect([...good].map(n => n.textContent).join('')).toBe('4645')
  })

  it('states the count in text as well, so colour is never the only signal', () => {
    const { container } = render(<EvidenceView evidence={payload} />)
    const count = container.querySelector('.rh__cd-count')
    expect(count?.textContent).toBe('4 of 15 characters differ')
  })

  it('badges each side with a word, not just a colour', () => {
    render(<EvidenceView evidence={payload} />)
    expect(screen.getByText('Verified')).toBeTruthy()
    expect(screen.getByText('Failed verification')).toBeTruthy()
  })

  it('renders both codes in full', () => {
    const { container } = render(<EvidenceView evidence={payload} />)
    const values = [...container.querySelectorAll('.rh__cd-value')].map(n => n.textContent)
    expect(values).toEqual([WRONG, RIGHT])
  })

  it('does not mark anything when the codes are the same length but identical', () => {
    const { container } = render(<EvidenceView evidence={{
      ...payload, left: { ...payload.left, value: RIGHT },
    }} />)
    expect(container.querySelectorAll('.rh__cd-ch--bad')).toHaveLength(0)
  })

  it('still renders when the codes are different lengths', () => {
    // Nothing can be aligned character-by-character, so it marks nothing rather
    // than marking the wrong things.
    const { container } = render(<EvidenceView evidence={{
      ...payload, left: { ...payload.left, value: 'SHORT' },
    }} />)
    expect(container.textContent).toContain('SHORT')
    expect(container.querySelectorAll('.rh__cd-ch--bad')).toHaveLength(0)
  })

  it('survives a malformed payload', () => {
    expect(() => render(<EvidenceView evidence={{ kind: 'code_diff' }} />)).not.toThrow()
    expect(() =>
      render(<EvidenceView evidence={{ kind: 'code_diff', left: 'nope', right: 42 }} />),
    ).not.toThrow()
  })
})
