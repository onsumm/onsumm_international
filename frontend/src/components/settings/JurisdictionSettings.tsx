/**
 * "Country rules" settings: which India-specific checks apply to this tenant.
 *
 * An Indian tenant has every pack on (today's behaviour). An international
 * tenant starts with every pack off; an admin can switch one back on — e.g. a
 * Singapore company that also pays Indian vendors and wants GSTIN and PAN
 * checks for them. Each pack shows whether it is the profile default or an
 * override, and can be reset to the default.
 */
import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '../../auth/AuthContext'
import { ENTITLEMENTS_INVALIDATE_EVENT } from '../../auth/EntitlementsContext'
import type { PackKey } from '../../auth/jurisdiction'
import {
  fetchJurisdictionSettings, saveJurisdictionSettings, type JurisdictionSettings,
} from '../../api/jurisdiction'
import { Badge, Button, Card, CardBody, CardHeader, Checkbox, ErrorState, Input, Select, Skeleton, useToast } from '../../ui'

const PACK_COPY: Record<PackKey, { name: string; detail: string }> = {
  gst: { name: 'GST', detail: 'GSTIN on vendors and invoices, GSTIN verification, CGST/SGST/IGST split, HSN/SAC' },
  pan: { name: 'PAN', detail: 'PAN on vendors and PAN checks' },
  tds_india: { name: 'Indian TDS', detail: 'TDS sections, deduction on bills, 26Q' },
  bank_india: { name: 'Indian bank accounts', detail: 'IFSC accounts and penny-drop verification' },
  imps: { name: 'IMPS payments', detail: 'Pay vendors over IMPS from an Indian bank account' },
  msme: { name: 'MSME rules', detail: 'Udyam registration and the 45-day payment rule' },
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
  'October', 'November', 'December']

export default function JurisdictionSettings() {
  const { user } = useAuth()
  const toast = useToast()
  const canEdit = user?.role === 'admin'
  const [data, setData] = useState<JurisdictionSettings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [labels, setLabels] = useState({ tax_id_label: '', indirect_tax_label: '' })

  const load = useCallback(async () => {
    setError(null)
    try {
      const d = await fetchJurisdictionSettings()
      setData(d)
      setLabels({ tax_id_label: d.jurisdiction.tax_id_label, indirect_tax_label: d.jurisdiction.indirect_tax_label })
    } catch {
      setError('Could not load country rules')
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const save = async (body: Parameters<typeof saveJurisdictionSettings>[0], message: string) => {
    setSaving(true)
    try {
      const d = await saveJurisdictionSettings(body)
      setData(d)
      // Every screen reads the jurisdiction from entitlements; refresh them.
      window.dispatchEvent(new Event(ENTITLEMENTS_INVALIDATE_EVENT))
      toast.success(message)
    } catch {
      toast.error('Could not save country rules')
    } finally {
      setSaving(false)
    }
  }

  if (error) return <ErrorState title="Country rules" message={error} onRetry={() => void load()} />
  if (!data) return <Skeleton height={220} />

  const j = data.jurisdiction
  const overrides = data.overrides.packs ?? {}

  return (
    <Card>
      <CardHeader
        title="Country rules"
        sub={`${j.profile === 'india' ? 'India' : 'International'} profile · ${j.country_code} · books in ${j.base_currency}`}
      />
      <CardBody>
        <p style={{ margin: '0 0 12px', color: 'var(--ink-500)', fontSize: 13 }}>
          Which India-specific checks apply to this company. Indian companies have all of them on.
          International companies start with all off; switch one on if you also deal with Indian vendors.
        </p>
        <div style={{ display: 'grid', gap: 10 }}>
          {data.editable_packs.map(pack => {
            const copy = PACK_COPY[pack]
            const overridden = pack in overrides
            return (
              <div key={pack} style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
                <div style={{ minWidth: 0 }}>
                  <Checkbox
                    label={copy.name}
                    checked={j.packs[pack]}
                    disabled={!canEdit || saving}
                    onChange={e => void save({ packs: { [pack]: e.target.checked } },
                      `${copy.name} ${e.target.checked ? 'on' : 'off'}`)}
                  />
                  <div style={{ fontSize: 12, color: 'var(--ink-500)', marginLeft: 26 }}>{copy.detail}</div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                  <Badge tone={overridden ? 'warn' : 'neutral'}>{overridden ? 'Override' : 'Default'}</Badge>
                  {overridden && canEdit && (
                    <Button size="sm" variant="ghost" disabled={saving}
                      onClick={() => void save({ packs: { [pack]: null } }, `${copy.name} reset to default`)}>
                      Reset
                    </Button>
                  )}
                </div>
              </div>
            )
          })}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12, marginTop: 18 }}>
          <Select
            label="Fiscal year starts in"
            value={String(j.fiscal_year_start_month)}
            disabled={!canEdit || saving}
            options={MONTHS.map((m, i) => ({ value: String(i + 1), label: m }))}
            onChange={e => void save({ fiscal_year_start_month: Number(e.target.value) }, 'Fiscal year updated')}
          />
          <Input
            label="Tax ID is called"
            value={labels.tax_id_label}
            disabled={!canEdit || saving}
            onChange={e => setLabels(l => ({ ...l, tax_id_label: e.target.value }))}
            onBlur={() => labels.tax_id_label !== j.tax_id_label &&
              void save({ tax_id_label: labels.tax_id_label }, 'Tax ID label updated')}
          />
          <Input
            label="Indirect tax is called"
            value={labels.indirect_tax_label}
            disabled={!canEdit || saving}
            onChange={e => setLabels(l => ({ ...l, indirect_tax_label: e.target.value }))}
            onBlur={() => labels.indirect_tax_label !== j.indirect_tax_label &&
              void save({ indirect_tax_label: labels.indirect_tax_label }, 'Tax label updated')}
          />
        </div>
        {!canEdit && (
          <p style={{ marginTop: 12, fontSize: 12, color: 'var(--ink-500)' }}>Only an admin can change country rules.</p>
        )}
      </CardBody>
    </Card>
  )
}
