/**
 * "This change is being made from a Review finding."
 *
 * Set while a bill or vendor card opened from a Review finding is open, and
 * sent with every change request (POST / PUT / PATCH / DELETE) to our API as
 * X-Review-Finding / X-Review-Surface. The server records each such change in
 * the Review Hub audit log, with the before → after of the vendor or bill —
 * backend_ocr/app/review/audit.py. Attached centrally (the fetch wrapper and
 * the axios interceptor in api/invoices.ts), so no card has to remember to.
 */
let current: { findingId: string; surface: string } | null = null

export function setReviewContext(ctx: { findingId: string; surface: string } | null) {
  current = ctx
}

const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

/** The headers to add to a request, or none. */
export function reviewHeaders(method: string | undefined): Record<string, string> {
  if (!current || !MUTATING.has((method || 'GET').toUpperCase())) return {}
  return { 'X-Review-Finding': current.findingId, 'X-Review-Surface': current.surface }
}
