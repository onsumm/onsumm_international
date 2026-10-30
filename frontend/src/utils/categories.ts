// ── Category utilities ────────────────────────────────────────────────────────
export const BASE_CATEGORIES = [
  'Rent',
  'Reimbursement',
  'Travel & Expenses',
  'Utilities',
  'Software / SaaS',
  'Food & Beverages',
]

export const EXP_HEADS = [
  'ACCOUNTS','ADMINISTRATION','ASSETS','DUTIES & TAXES','IT','LEGAL',
  'MAN POWER','MARKETING','OFFICE','PANTRY','PROFESSIONAL','RENT','UTILITY','OTHER',
]

const LS_KEY = 'custom_categories'

/** Return base + custom categories (custom ones appended after base) */
export function getAllCategories(): string[] {
  const custom = getCustomCategories()
  return [...BASE_CATEGORIES, ...custom.filter(c => !BASE_CATEGORIES.includes(c))]
}

export function getCustomCategories(): string[] {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) ?? '[]') as string[]
  } catch {
    return []
  }
}

export function addCustomCategory(name: string): void {
  const trimmed = name.trim()
  if (!trimmed) return
  const existing = getCustomCategories()
  if (existing.includes(trimmed) || BASE_CATEGORIES.includes(trimmed)) return
  localStorage.setItem(LS_KEY, JSON.stringify([...existing, trimmed]))
}

export function removeCustomCategory(name: string): void {
  const existing = getCustomCategories()
  localStorage.setItem(LS_KEY, JSON.stringify(existing.filter(c => c !== name)))
}

/**
 * Fetch custom categories from the API and merge them into the localStorage
 * cache so synchronous getAllCategories() callers see them on next render.
 * Returns the merged list.
 */
export async function syncCategoriesFromApi(companyId?: string): Promise<string[]> {
  try {
    const params = companyId ? `?company_id=${companyId}` : ''
    const baseUrl = import.meta.env.VITE_API_URL ?? ''
    const resp = await fetch(`${baseUrl}/api/categories${params}`)
    if (!resp.ok) return getAllCategories()
    const data: { categories: { id: string; name: string }[] } = await resp.json()
    const apiNames = data.categories.map(c => c.name)
    const custom = apiNames.filter(n => !BASE_CATEGORIES.includes(n))
    localStorage.setItem(LS_KEY, JSON.stringify(custom))
    return getAllCategories()
  } catch {
    return getAllCategories()
  }
}
