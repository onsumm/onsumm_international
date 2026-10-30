// GSTIN checksum validation.
//
// A GSTIN is 15 chars: 2 state code + 10 PAN + 1 entity + 'Z' + 1 check digit.
// The 15th char is a mod-36 checksum computed over the first 14 chars
// (alternating weights 1,2,1,2…). OCR misreads that transpose or substitute a
// character almost always break this checksum, so we can flag them before the
// value is trusted downstream (e.g. GST portal verification).

const GSTIN_CHARS = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'

/** Returns the correct 15th check digit for the first 14 chars, or null if malformed. */
export function gstinCheckDigit(first14: string): string | null {
  if (first14.length !== 14) return null
  let sum = 0
  for (let i = 0; i < 14; i++) {
    const v = GSTIN_CHARS.indexOf(first14[i])
    if (v < 0) return null
    const code = v * (i % 2 === 0 ? 1 : 2)
    sum += Math.floor(code / 36) + (code % 36)
  }
  return GSTIN_CHARS[(36 - (sum % 36)) % 36]
}

/** True when `raw` is a well-formed 15-char GSTIN whose check digit matches. */
export function isValidGstinChecksum(raw: string): boolean {
  const g = (raw || '').trim().toUpperCase()
  if (g.length !== 15) return false
  const expected = gstinCheckDigit(g.slice(0, 14))
  return expected !== null && expected === g[14]
}

/**
 * Returns a human-readable warning if `raw` looks like a GSTIN (15 chars) but
 * fails the checksum, else null. Shorter values (blank, or a 10-char PAN) are
 * left alone — this only flags a full GSTIN that can't be genuine.
 */
export function gstinChecksumError(raw: string): string | null {
  const g = (raw || '').trim().toUpperCase()
  if (g.length !== 15) return null
  if (isValidGstinChecksum(g)) return null
  return 'Invalid GSTIN checksum . Please verify against the invoice before proceeding.'
}
