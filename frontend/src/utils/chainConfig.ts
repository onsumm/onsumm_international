export interface ChainStep {
  id: string
  label: string
  role: string
  locked?: boolean
  assignees?: string[]   // specific user names allowed for this step (empty = all with that role)
}

export const DEFAULT_CHAIN: ChainStep[] = [
  { id: 'submit',     label: 'Submitted',         role: 'member',             locked: true },
  { id: 'approver',   label: 'Approver',           role: 'approver' },
  { id: 'accountant', label: 'Accountant',         role: 'accountant' },
  { id: 'fc',         label: 'Finance Controller', role: 'finance_controller' },
]

const KEY = (companyId?: string) => `onsum_chain_${companyId ?? 'default'}`

export function getChain(companyId?: string): ChainStep[] {
  try {
    const raw = localStorage.getItem(KEY(companyId))
    if (!raw) return DEFAULT_CHAIN
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed) || parsed.length === 0) return DEFAULT_CHAIN
    return parsed
  } catch {
    return DEFAULT_CHAIN
  }
}

export function saveChain(steps: ChainStep[], companyId?: string): void {
  localStorage.setItem(KEY(companyId), JSON.stringify(steps))
}

export function resetChain(companyId?: string): void {
  localStorage.removeItem(KEY(companyId))
}

export function isDefaultChain(_steps: ChainStep[], companyId?: string): boolean {
  return !localStorage.getItem(KEY(companyId))
}

// Maps a chain to the STATUS → step-id and step-id → STATUS lookups
// used by the approval workflow display
export function buildStatusMaps(chain: ChainStep[]) {
  const approvalSteps = chain.filter(s => !s.locked)
  const STATUS_ACTIVE_STEP: Record<string, number> = { DRAFT: 1, NEEDS_REVISION: 1 }
  const _STATUS_FOR_STEP: Record<number, string>   = {}
  const _STEP_FOR_STATUS: Record<string, number>   = {}

  const statuses = ['PENDING_APPROVER', 'PENDING_ACCOUNTANT', 'PENDING_FC']
  approvalSteps.forEach((_step, i) => {
    const stepId = i + 2  // step 1 is Submit
    const status = statuses[i] ?? `PENDING_STEP_${stepId}`
    STATUS_ACTIVE_STEP[status] = stepId
    _STATUS_FOR_STEP[stepId]   = status
    _STEP_FOR_STATUS[status]   = stepId
  })
  return { STATUS_ACTIVE_STEP, _STATUS_FOR_STEP, _STEP_FOR_STATUS }
}

export const AVAILABLE_ROLES: { role: string; label: string; color: string }[] = [
  { role: 'approver',           label: 'Approver',           color: '#8b5cf6' },
  { role: 'accountant',         label: 'Accountant',         color: '#10b981' },
  { role: 'finance_controller', label: 'Finance Controller', color: '#3b82f6' },
  { role: 'admin',              label: 'Admin',              color: '#f59e0b' },
]
