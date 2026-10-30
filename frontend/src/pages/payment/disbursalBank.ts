/**
 * The disbursal bank the user last chose, remembered per tenant in this
 * browser, so a payment started from anywhere (a bill, the vendor card, the
 * Payment Hub) goes out through the bank they picked rather than silently
 * through the remitter account's default. A convenience only: when storage is
 * unavailable it's simply not remembered, and the pay page still shows and lets
 * them change the bank before the OTP goes out.
 */
const key = (companyId?: string | null) => `onsumm.disbursalBank.${companyId || 'default'}`

export function loadDisbursalBank(companyId?: string | null): string | undefined {
  try { return localStorage.getItem(key(companyId)) || undefined } catch { return undefined }
}

export function saveDisbursalBank(companyId: string | null | undefined, bank: string): void {
  try { localStorage.setItem(key(companyId), bank) } catch { /* not remembered; still used */ }
}
