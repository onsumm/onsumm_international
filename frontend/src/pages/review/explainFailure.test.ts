import { describe, it, expect } from 'vitest'
import { explainFailure } from './explainFailure'

describe('explainFailure', () => {
  it('turns a gateway permission refusal into what to do about it', () => {
    const r = explainFailure('Disbursement gateway returned 403: No permission for operation: vendor-payment:pay')
    expect(r.reason).toMatch(/isn’t allowed to release vendor payments/)
    expect(r.hint).toMatch(/payment permission/)
  })

  it('drops the gateway prefix and keeps the bank’s own reason', () => {
    expect(explainFailure('Disbursement gateway returned 502: beneficiary account closed'))
      .toEqual({ reason: 'Beneficiary account closed', hint: null })
  })

  it('still says something when there is no message', () => {
    expect(explainFailure(null).reason).toMatch(/did not accept/)
  })
})
