/**
 * The chain's cache and its pure helpers, kept out of the component file.
 *
 * Not taste: a module that exports both a component and plain functions loses
 * fast refresh for everything in it, so the stepper would remount — losing the
 * chain it had fetched — on every edit to this file.
 */
import { fetchChainConfig, type ChainEntry } from '../api/invoices'

// ── Approval chain cache ─────────────────────────────────────────────────────
// Keyed by `${companyId}__${department}`. Populated eagerly when bills load.
export const _chainCache = new Map<string, { id: number; label: string; role: string }[]>()

export function chainCacheKey(companyId?: string, department?: string | null) {
  return `${companyId ?? ''}__${department ?? ''}`
}

export async function prefetchChains(companyId: string, departments: string[]) {
  const keys = ['', ...departments]  // '' = default chain
  await Promise.all(keys.map(async dept => {
    const key = chainCacheKey(companyId, dept || null)
    if (_chainCache.has(key)) return
    try {
      const steps = await fetchChainConfig(companyId, dept || undefined)
      if (steps.length > 0)
        _chainCache.set(key, steps.map((s, i) => ({ id: i + 1, label: s.label, role: s.role, assignees: s.assignees ?? [] })))
    } catch {}
  }))
}

// ── Approval chain helpers ───────────────────────────────────────────────────
export function buildStatusActiveStep(steps: { id: number; label: string; role: string }[]): Record<string, number> {
  const approvalSteps = steps.filter((_, i) => i > 0)  // skip Submitted (id=1)
  const statuses = ['PENDING_APPROVER', 'PENDING_ACCOUNTANT', 'PENDING_FC']
  const map: Record<string, number> = { DRAFT: 1, NEEDS_REVISION: 1, APPROVED: 99 }
  approvalSteps.forEach((_, i) => {
    const status = statuses[i] ?? `PENDING_STEP_${i + 1}`
    map[status] = i + 2
  })
  return map
}

export function stepStateFromChain(
  stepId: number, chain: ChainEntry[], status: string,
  statusActiveStep: Record<string, number>
) {
  const active = statusActiveStep[status] ?? 0
  const entries = chain.filter(c => c.step === stepId)

  // Current active step always shows as pending — even if it was previously approved
  if (active === stepId) return 'pending'

  if (entries.length) {
    const last = entries[entries.length - 1]
    if (last.action === 'approve' || last.action === 'approved') {
      // Was approved but bill is now before this step → sent back past it
      return active > stepId ? 'approved' : 'sent_back'
    }
    if (last.action === 'submit' || last.action === 'submitted') return active > stepId ? 'approved' : 'submitted'
    if (last.action === 'send_back') return active > stepId ? 'approved' : 'sent_back'
  }

  if (active > stepId) return 'approved'
  return 'idle'
}
