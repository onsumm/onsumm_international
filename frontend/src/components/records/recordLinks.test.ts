import { describe, it, expect } from 'vitest'
import { formatRecord, parseRecord, withRecord } from './recordLinks'

describe('record card links', () => {
  it('round-trips a bill and a vendor', () => {
    expect(parseRecord(formatRecord({ kind: 'bill', id: 'b1' }))).toEqual({ kind: 'bill', id: 'b1' })
    expect(parseRecord(formatRecord({ kind: 'vendor', id: 'v1', tab: 'bank' }))).toEqual({ kind: 'vendor', id: 'v1', tab: 'bank' })
  })

  it('leaves the default vendor tab out, like vendorPath does', () => {
    expect(formatRecord({ kind: 'vendor', id: 'v1', tab: 'details' })).toBe('vendor:v1')
    expect(formatRecord({ kind: 'vendor', id: 'v1' })).toBe('vendor:v1')
  })

  it('opens an unknown vendor tab on the landing tab rather than not at all', () => {
    expect(parseRecord('vendor:v1:invented')).toEqual({ kind: 'vendor', id: 'v1' })
  })

  it('rejects values that are not a record', () => {
    expect(parseRecord(null)).toBeNull()
    expect(parseRecord('')).toBeNull()
    expect(parseRecord('bill')).toBeNull()
    expect(parseRecord('bill:')).toBeNull()
    expect(parseRecord('bill:b1:extra')).toBeNull()
    expect(parseRecord('vendor:v1:bank:extra')).toBeNull()
    expect(parseRecord('invoice:b1')).toBeNull()
  })

  it('sets and clears the param without touching the others', () => {
    expect(withRecord('?q=acme', { kind: 'bill', id: 'b1' })).toBe('?q=acme&open=bill:b1')
    expect(withRecord('?q=acme&open=bill:b1', { kind: 'vendor', id: 'v1', tab: 'tds' })).toBe('?q=acme&open=vendor:v1:tds')
    expect(withRecord('?q=acme&open=bill:b1', null)).toBe('?q=acme')
    expect(withRecord('?open=bill:b1', null)).toBe('')
  })

  it('does not collide with the payment route\'s pinned bill', () => {
    expect(withRecord('?bill=inv-9', { kind: 'bill', id: 'b1' })).toBe('?bill=inv-9&open=bill:b1')
  })
})
