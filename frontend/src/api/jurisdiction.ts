/**
 * Tenant jurisdiction settings (backend: routers/jurisdiction.py). Read and
 * change which country's rules apply: rule packs, locale, fiscal year, labels.
 */
import axios from 'axios'
import type { Jurisdiction, PackKey } from '../auth/jurisdiction'

export interface JurisdictionSettings {
  /** What applies now: defaults plus overrides. */
  jurisdiction: Jurisdiction
  /** What the tenant's profile gives with no overrides. */
  defaults: Jurisdiction
  /** Exactly what is overridden in settings_jsonb.jurisdiction. */
  overrides: {
    packs?: Partial<Record<PackKey, boolean>>
    locale?: string
    fiscal_year_start_month?: number
    tax_id_label?: string
    indirect_tax_label?: string
  }
  editable_packs: PackKey[]
}

export interface JurisdictionUpdate {
  /** `null` removes the override, returning the pack to the profile default. */
  packs?: Partial<Record<PackKey, boolean | null>>
  locale?: string
  fiscal_year_start_month?: number
  tax_id_label?: string
  indirect_tax_label?: string
}

export async function fetchJurisdictionSettings(): Promise<JurisdictionSettings> {
  return (await axios.get<JurisdictionSettings>('/api/tenant/jurisdiction')).data
}

export async function saveJurisdictionSettings(body: JurisdictionUpdate): Promise<JurisdictionSettings> {
  return (await axios.put<JurisdictionSettings>('/api/tenant/jurisdiction', body)).data
}
