/**
 * Plain-language wording for a failed payment's gateway message, used by each
 * line of a vendor's failed-payments card (RollupLines).
 */

/**
 * The gateway's message in words a finance user acts on. A permission refusal
 * is not a bank problem and retrying as-is will fail the same way, so it says
 * what has to change first.
 */
export function explainFailure(raw: string | null): { reason: string; hint: string | null } {
  const msg = (raw || '').replace(/^Disbursement gateway returned \d+:\s*/i, '').trim()
  if (/no permission for operation/i.test(msg)) {
    return {
      reason: 'Your account isn’t allowed to release vendor payments.',
      hint: 'Ask an admin for payment permission, then pay again.',
    }
  }
  if (!msg) return { reason: 'The bank did not accept the transfer.', hint: null }
  return { reason: msg.charAt(0).toUpperCase() + msg.slice(1), hint: null }
}
