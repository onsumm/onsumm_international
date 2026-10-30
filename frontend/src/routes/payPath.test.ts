import { describe, it, expect } from 'vitest'
import { payPath } from './paths'

describe('payPath', () => {
  it('carries the bank chosen in the Payment Hub', () => {
    expect(payPath('s1', undefined, undefined, 'HDFC')).toMatch(/\/pay\/s1\?via=HDFC$/)
    expect(payPath('s1', 'inv1', 500, 'AUBANK')).toMatch(/\?bill=inv1&amount=500&via=AUBANK$/)
  })

  it('leaves it out when none was chosen', () => {
    expect(payPath('s1', 'inv1')).toMatch(/\?bill=inv1$/)
  })
})
