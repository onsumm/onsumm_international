import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { UserPlus } from 'lucide-react'
import {
  Badge, Button, Card, CardBody, EmptyState, ErrorState, PageHeader, SkeletonRows,
  FilterStatus, FilterTh, useColumnFilters, formatDate, type FilterField,
} from '../../ui'
import { listServiceCatalog, listTenants, type TenantSummary } from '../../api/admin'
import { ADMIN_PATHS, tenantPath } from '../../routes/adminPaths'
import { SERVICE_CATEGORIES, SERVICE_LABELS } from '../../auth/services'

/** Reuses the same column-filter primitives as Bills and Vendors. */
export default function TenantsPage({ catalogOnly = false }: { catalogOnly?: boolean }) {
  const navigate = useNavigate()
  const [tenants, setTenants] = useState<TenantSummary[] | null>(null)
  const [error, setError] = useState('')
  const [catalog, setCatalog] = useState<Awaited<ReturnType<typeof listServiceCatalog>>>([])

  useEffect(() => {
    if (catalogOnly) {
      listServiceCatalog().then(setCatalog).catch(e => setError(String(e.message ?? e)))
      return
    }
    listTenants()
      .then(setTenants)
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load tenants'))
  }, [catalogOnly])

  const fields = useMemo<FilterField<TenantSummary>[]>(() => [
    { key: 'company_name', label: 'Company', kind: 'text', value: t => t.company_name },
    { key: 'plan_tier', label: 'Plan', kind: 'enum', value: t => t.plan_tier ?? '—' },
    {
      key: 'is_active', label: 'Status', kind: 'enum',
      value: t => (t.is_active ? 'Active' : 'Suspended'),
    },
    { key: 'user_count', label: 'Users', kind: 'number', value: t => t.user_count },
    { key: 'service_count', label: 'Services', kind: 'number', value: t => t.service_count },
  ], [])
  const filters = useColumnFilters(fields)

  if (catalogOnly) {
    return (
      <>
        <PageHeader
          title="Service catalog"
          sub="Every module that can be licensed. Defined in backend_ocr/app/services.py."
        />
        {error && <ErrorState title="Could not load the catalog" message={error} />}
        {SERVICE_CATEGORIES.map(cat => (
          <Card key={cat.label} style={{ marginBottom: 'var(--sp-3)' }}>
            <CardBody>
              <h3 style={{ margin: '0 0 var(--sp-2)', fontSize: 'var(--fs-2)' }}>{cat.label}</h3>
              <ul className="adm__svc">
                {cat.services.map(key => {
                  const meta = catalog.find(c => c.key === key)
                  return (
                    <li key={key} className="adm__svc-row">
                      <div className="adm__svc-meta">
                        <span className="adm__svc-name">{SERVICE_LABELS[key]}</span>
                        {meta?.is_core && <Badge tone="neutral"> core</Badge>}
                        <p className="adm__svc-desc">{meta?.description ?? ''}</p>
                      </div>
                      <code className="adm__json">{key}</code>
                    </li>
                  )
                })}
              </ul>
            </CardBody>
          </Card>
        ))}
      </>
    )
  }

  if (error) return <ErrorState title="Could not load tenants" message={error} />
  if (!tenants) return <SkeletonRows rows={6} />

  const rows = filters.apply(tenants)

  return (
    <>
      <PageHeader
        title="Tenants"
        sub={`${tenants.length} client${tenants.length === 1 ? '' : 's'} on the platform`}
        actions={
          <Button iconLeft={<UserPlus size={14} />} onClick={() => navigate(ADMIN_PATHS.onboarding)}>
            Onboard a client
          </Button>
        }
      />

      {tenants.length === 0 ? (
        <EmptyState
          title="No tenants yet"
          message="Onboard your first client to get started."
        />
      ) : (
        <Card>
          <CardBody>
            <FilterStatus state={filters} total={tenants.length} shown={rows.length} unit="tenants" />
            <div className="adm__tablewrap">
              <table className="adm__table">
                <thead>
                  <tr>
                    <FilterTh state={filters} fieldKey="company_name" rows={tenants}>Company</FilterTh>
                    <FilterTh state={filters} fieldKey="plan_tier" rows={tenants}>Plan</FilterTh>
                    <FilterTh state={filters} fieldKey="is_active" rows={tenants}>Status</FilterTh>
                    <FilterTh state={filters} fieldKey="user_count" rows={tenants} align="right">Users</FilterTh>
                    <FilterTh state={filters} fieldKey="service_count" rows={tenants} align="right">Services</FilterTh>
                    <th>Onboarded</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(t => (
                    <tr
                      key={t.tenant_id}
                      className="adm__row-link"
                      onClick={() => navigate(tenantPath(t.tenant_id))}
                    >
                      <td>
                        <strong>{t.company_name}</strong>
                        {t.gstn && <div className="adm__svc-desc">{t.gstn}</div>}
                      </td>
                      <td>{t.plan_tier ?? '—'}</td>
                      <td>
                        <Badge tone={t.is_active ? 'success' : 'danger'}>
                          {t.is_active ? 'Active' : 'Suspended'}
                        </Badge>
                      </td>
                      <td style={{ textAlign: 'right' }}>{t.user_count}</td>
                      <td style={{ textAlign: 'right' }}>{t.service_count}</td>
                      <td>{t.onboarded_at ? formatDate(t.onboarded_at) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardBody>
        </Card>
      )}
    </>
  )
}
