import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Eye } from 'lucide-react'
import {
  Badge, Button, Card, CardBody, CardHeader, ConfirmDialog, EmptyState, ErrorState,
  Input, Modal, PageHeader, SkeletonRows, Switch, formatDate, formatDateTime, useToast,
} from '../../ui'
import {
  fetchAudit, fetchTenant, fetchTenantUsage, fetchTenantUsers, saveTenantServices,
  startImpersonation, type AuditEntry, type ServiceChange, type TenantServiceRow,
} from '../../api/admin'
import { TENANT_TABS, tenantPath, type TenantTab } from '../../routes/adminPaths'
import { SERVICE_CATEGORIES, SERVICE_DESCRIPTIONS, SERVICE_LABELS, type ServiceKey } from '../../auth/services'
import { useAdminCan } from '../AdminAuthContext'
import { enterImpersonation } from '../impersonation'

export default function TenantDetailPage() {
  const { tenantId = '', tab } = useParams()
  const navigate = useNavigate()
  const activeTab: TenantTab = (TENANT_TABS as readonly string[]).includes(tab ?? '')
    ? (tab as TenantTab)
    : 'overview'

  const [data, setData] = useState<Awaited<ReturnType<typeof fetchTenant>> | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    fetchTenant(tenantId)
      .then(setData)
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load tenant'))
  }, [tenantId])

  useEffect(() => { load() }, [load])

  if (error) return <ErrorState title="Could not load tenant" message={error} />
  if (!data) return <SkeletonRows rows={6} />

  return (
    <>
      <PageHeader
        title={data.tenant.company_name}
        sub={data.tenant.gstn ?? data.tenant.tenant_id}
        actions={<ImpersonateButton tenantId={tenantId} tenantName={data.tenant.company_name} />}
      />

      <div className="adm__tabs" role="tablist">
        {TENANT_TABS.map(t => (
          <button
            key={t}
            role="tab"
            aria-selected={activeTab === t}
            className={`adm__tab${activeTab === t ? ' adm__tab--active' : ''}`}
            onClick={() => navigate(tenantPath(tenantId, t))}
          >
            {t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {activeTab === 'overview' && <OverviewTab tenant={data.tenant} services={data.services} />}
      {activeTab === 'licensing' && (
        <LicensingTab tenantId={tenantId} services={data.services} onSaved={load} />
      )}
      {activeTab === 'users' && <UsersTab tenantId={tenantId} />}
      {activeTab === 'usage' && <UsageTab tenantId={tenantId} />}
      {activeTab === 'audit' && <AuditTab tenantId={tenantId} />}
    </>
  )
}

// ── Overview ─────────────────────────────────────────────────────────────────
function OverviewTab({ tenant, services }: {
  tenant: Record<string, unknown>; services: TenantServiceRow[]
}) {
  const on = services.filter(s => s.enabled).length
  const stats: [string, string][] = [
    ['Status', tenant.is_active ? 'Active' : 'Suspended'],
    ['Plan', String(tenant.plan_tier ?? '—')],
    ['Services enabled', `${on} of ${services.length}`],
    ['Pipeline', tenant.pipeline_enabled ? 'Processing' : 'Paused'],
    ['Timezone', String(tenant.timezone ?? '—')],
    ['Onboarded', tenant.onboarded_at ? formatDate(String(tenant.onboarded_at)) : '—'],
  ]
  return (
    <div className="adm__grid">
      {stats.map(([label, value]) => (
        <Card key={label}>
          <CardBody>
            <div className="adm__stat-label">{label}</div>
            <div className="adm__stat-value">{value}</div>
          </CardBody>
        </Card>
      ))}
    </div>
  )
}

// ── Licensing ────────────────────────────────────────────────────────────────
interface Draft { enabled: boolean; limit: string }

function LicensingTab({ tenantId, services, onSaved }: {
  tenantId: string; services: TenantServiceRow[]; onSaved: () => void
}) {
  const canEdit = useAdminCan('admin')
  const toast = useToast()
  const byKey = useMemo(
    () => Object.fromEntries(services.map(s => [s.service_key, s])) as Record<ServiceKey, TenantServiceRow>,
    [services],
  )

  const initial = useMemo(() => {
    const d: Partial<Record<ServiceKey, Draft>> = {}
    for (const s of services) {
      d[s.service_key] = {
        enabled: s.enabled,
        limit: s.limits?.limit != null ? String(s.limits.limit) : '',
      }
    }
    return d as Record<ServiceKey, Draft>
  }, [services])

  const [draft, setDraft] = useState<Record<ServiceKey, Draft>>(initial)
  useEffect(() => { setDraft(initial) }, [initial])

  const [reviewing, setReviewing] = useState(false)
  const [saving, setSaving] = useState(false)

  const changes = useMemo(() => (
    (Object.keys(draft) as ServiceKey[]).filter(k => {
      const a = initial[k], b = draft[k]
      return a && b && (a.enabled !== b.enabled || a.limit !== b.limit)
    })
  ), [draft, initial])

  const revokingCore = changes.some(k => !draft[k].enabled && byKey[k]?.is_core)

  const save = async () => {
    setSaving(true)
    try {
      const payload: ServiceChange[] = changes.map(k => ({
        service_key: k,
        enabled: draft[k].enabled,
        limits: (draft[k].limit ? { limit: Number(draft[k].limit) } : {}) as Record<string, number>,
      }))
      await saveTenantServices(tenantId, payload, revokingCore)
      toast.success(`Saved ${payload.length} licensing change${payload.length === 1 ? '' : 's'}`)
      setReviewing(false)
      onSaved()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      {!canEdit && (
        <Card style={{ marginBottom: 'var(--sp-3)' }}>
          <CardBody>
            <span className="adm__warn">
              Your ops role is read-only for licensing. Ask an admin or owner to make changes.
            </span>
          </CardBody>
        </Card>
      )}

      {SERVICE_CATEGORIES.map(cat => {
        const rows = cat.services.filter(k => byKey[k])
        if (!rows.length) return null
        const onCount = rows.filter(k => draft[k]?.enabled).length
        return (
          <Card key={cat.label} style={{ marginBottom: 'var(--sp-3)' }}>
            <CardHeader title={cat.label} sub={`${onCount} of ${rows.length} enabled`} />
            <CardBody>
              <ul className="adm__svc">
                {rows.map(key => {
                  const meta = byKey[key]
                  const d = draft[key]
                  return (
                    <li key={key} className="adm__svc-row">
                      <div className="adm__svc-meta">
                        <span className="adm__svc-name">{SERVICE_LABELS[key]}</span>
                        {meta.is_core && <Badge tone="neutral"> core</Badge>}
                        <p className="adm__svc-desc">{SERVICE_DESCRIPTIONS[key]}</p>
                      </div>
                      {meta.metered_unit && d?.enabled && (
                        <div className="adm__svc-limit">
                          <Input
                            label={`${meta.metered_unit}/month`}
                            type="number"
                            placeholder="Unlimited"
                            disabled={!canEdit}
                            value={d.limit}
                            onChange={e => setDraft(s => ({ ...s, [key]: { ...s[key], limit: e.target.value } }))}
                          />
                        </div>
                      )}
                      <Switch
                        checked={Boolean(d?.enabled)}
                        disabled={!canEdit}
                        label={SERVICE_LABELS[key]}
                        onChange={v => setDraft(s => ({ ...s, [key]: { ...s[key], enabled: v } }))}
                      />
                    </li>
                  )
                })}
              </ul>
            </CardBody>
          </Card>
        )
      })}

      {changes.length > 0 && canEdit && (
        <div className="adm__savebar">
          <span className="adm__savebar-summary">
            {changes.length} change{changes.length === 1 ? '' : 's'} —{' '}
            {changes.filter(k => draft[k].enabled).length} enabled,{' '}
            {changes.filter(k => !draft[k].enabled).length} revoked
          </span>
          <span style={{ display: 'flex', gap: 'var(--sp-2)' }}>
            <Button variant="secondary" onClick={() => setDraft(initial)}>Discard</Button>
            <Button onClick={() => setReviewing(true)}>Review &amp; save</Button>
          </span>
        </div>
      )}

      <ConfirmDialog
        open={reviewing}
        title="Save licensing changes"
        description={
          revokingCore
            ? 'This revokes a core service. The tenant will lose access to part of the product immediately.'
            : 'These take effect immediately for this tenant.'
        }
        items={changes.map(k => `${SERVICE_LABELS[k]} — ${draft[k].enabled ? 'enabled' : 'revoked'}`)}
        tone={revokingCore ? 'danger' : 'primary'}
        confirmLabel={saving ? 'Saving…' : 'Save'}
        onConfirm={save}
        onCancel={() => setReviewing(false)}
      />
    </>
  )
}

// ── Impersonation ────────────────────────────────────────────────────────────
function ImpersonateButton({ tenantId, tenantName }: { tenantId: string; tenantName: string }) {
  const canImpersonate = useAdminCan('support')
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)

  if (!canImpersonate) return null

  const go = async () => {
    setBusy(true)
    try {
      const grant = await startImpersonation(tenantId, reason)
      enterImpersonation(grant)   // full reload into the tenant session
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not start session')
      setBusy(false)
    }
  }

  return (
    <>
      <Button variant="secondary" iconLeft={<Eye size={14} />} onClick={() => setOpen(true)}>
        View as tenant
      </Button>
      <Modal open={open} onClose={() => setOpen(false)} title={`View ${tenantName} as support`} size="sm">
        <div className="adm__form">
          <p className="adm__svc-desc">
            Opens a read-only session as Onsumm Support. Every action is recorded against
            you, and writes are refused for the duration.
          </p>
          <Input
            label="Reason (recorded in the audit log)"
            placeholder="e.g. Ticket #4821 — bills missing after upload"
            value={reason}
            onChange={e => setReason(e.target.value)}
          />
          {reason.trim().length > 0 && reason.trim().length < 10 && (
            <span className="adm__problem">Please give at least 10 characters.</span>
          )}
          <div style={{ display: 'flex', gap: 'var(--sp-2)', justifyContent: 'flex-end' }}>
            <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
            <Button variant="danger" disabled={busy || reason.trim().length < 10} onClick={go}>
              {busy ? 'Starting…' : 'Start session'}
            </Button>
          </div>
        </div>
      </Modal>
    </>
  )
}

// ── Users / usage / audit ────────────────────────────────────────────────────
function UsersTab({ tenantId }: { tenantId: string }) {
  const [users, setUsers] = useState<Awaited<ReturnType<typeof fetchTenantUsers>> | null>(null)
  useEffect(() => { fetchTenantUsers(tenantId).then(setUsers).catch(() => setUsers([])) }, [tenantId])
  if (!users) return <SkeletonRows rows={4} />
  if (!users.length) return <EmptyState title="No users" message="This tenant has no login accounts yet." />
  return (
    <Card><CardBody><div className="adm__tablewrap">
      <table className="adm__table">
        <thead><tr><th>Name</th><th>Username</th><th>Role</th><th>Email</th><th>Department</th></tr></thead>
        <tbody>
          {users.map(u => (
            <tr key={u.username}>
              <td>{u.name}</td><td>{u.username}</td>
              <td><Badge tone="neutral">{u.role}</Badge></td>
              <td>{u.email}</td><td>{u.department ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div></CardBody></Card>
  )
}

function UsageTab({ tenantId }: { tenantId: string }) {
  const [usage, setUsage] = useState<Awaited<ReturnType<typeof fetchTenantUsage>> | null>(null)
  useEffect(() => { fetchTenantUsage(tenantId).then(setUsage).catch(() => setUsage([])) }, [tenantId])
  if (!usage) return <SkeletonRows rows={4} />
  if (!usage.length) {
    return (
      <EmptyState
        title="No usage recorded yet"
        message="Metering starts once the instrumented services are used."
      />
    )
  }
  return (
    <Card><CardBody><div className="adm__tablewrap">
      <table className="adm__table">
        <thead><tr><th>Service</th><th>Metric</th><th>Month</th><th style={{ textAlign: 'right' }}>Used</th><th style={{ textAlign: 'right' }}>Limit</th></tr></thead>
        <tbody>
          {usage.map((u, i) => (
            <tr key={i}>
              <td>{SERVICE_LABELS[u.service_key as ServiceKey] ?? u.service_key}</td>
              <td>{u.metric}</td>
              <td>{formatDate(u.period_month)}</td>
              <td style={{ textAlign: 'right' }}>{u.used}</td>
              <td style={{ textAlign: 'right' }}>{u.limits?.limit ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div></CardBody></Card>
  )
}

function AuditTab({ tenantId }: { tenantId: string }) {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null)
  useEffect(() => {
    fetchAudit({ tenant_id: tenantId, limit: 100 })
      .then(d => setEntries(d.entries)).catch(() => setEntries([]))
  }, [tenantId])
  if (!entries) return <SkeletonRows rows={4} />
  if (!entries.length) return <EmptyState title="Nothing recorded yet" message="Ops actions on this tenant appear here." />
  return <AuditTable entries={entries} showTenant={false} />
}

export function AuditTable({ entries, showTenant = true }: { entries: AuditEntry[]; showTenant?: boolean }) {
  return (
    <Card><CardBody><div className="adm__tablewrap">
      <table className="adm__table">
        <thead>
          <tr>
            <th>When</th><th>Who</th><th>Action</th>
            {showTenant && <th>Tenant</th>}
            <th>Target</th><th>Change</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(e => (
            <tr key={e.id}>
              <td>{formatDateTime(e.created_at)}</td>
              <td>{e.actor_email}</td>
              <td><Badge tone="neutral">{e.action}</Badge></td>
              {showTenant && <td>{e.company_name ?? '—'}</td>}
              <td>{e.target_id ?? '—'}</td>
              <td className="adm__json">{e.after ? JSON.stringify(e.after) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div></CardBody></Card>
  )
}
