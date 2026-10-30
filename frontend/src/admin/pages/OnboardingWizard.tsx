import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Button, Card, CardBody, CardHeader, Input, PageHeader, Select, useToast,
} from '../../ui'
import {
  listPackages, provisionTenant, validateOnboarding,
  type OnboardPayload, type ServicePackage,
} from '../../api/admin'
import { tenantPath } from '../../routes/adminPaths'
import { SERVICE_LABELS, type ServiceKey } from '../../auth/services'

type Problem = { field: string; message: string; severity?: string }

/**
 * Creates a working tenant end-to-end: company row, default brand, approval chain,
 * first admin login, and the licensed services. Replaces doing all of that by hand
 * in psql.
 */
export default function OnboardingWizard() {
  const navigate = useNavigate()
  const toast = useToast()
  const [packages, setPackages] = useState<ServicePackage[]>([])
  const [problems, setProblems] = useState<Problem[]>([])
  const [busy, setBusy] = useState(false)

  const [form, setForm] = useState({
    company_name: '', legal_name: '', gstn: '', primary_contact_email: '',
    package_key: '', username: '', password: '', admin_name: '', admin_email: '',
  })
  const set = (k: keyof typeof form) => (v: string) => setForm(s => ({ ...s, [k]: v }))

  useEffect(() => { listPackages().then(setPackages).catch(() => setPackages([])) }, [])

  const payload = (): OnboardPayload => ({
    company_name: form.company_name.trim(),
    legal_name: form.legal_name.trim() || undefined,
    gstn: form.gstn.trim() || undefined,
    primary_contact_email: form.primary_contact_email.trim() || undefined,
    package_key: form.package_key || undefined,
    admin: {
      username: form.username.trim(),
      password: form.password,
      name: form.admin_name.trim(),
      email: form.admin_email.trim(),
    },
  })

  const ready = form.company_name && form.username && form.password.length >= 8 &&
    form.admin_name && form.admin_email

  /** Checked before provisioning because usernames are unique across ALL tenants —
   *  finding that out from a failed transaction is a poor experience. */
  const check = async () => {
    setBusy(true)
    try {
      const r = await validateOnboarding(payload())
      setProblems(r.problems)
      if (r.ok && !r.problems.length) toast.success('Looks good — ready to create.')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Validation failed')
    } finally { setBusy(false) }
  }

  const create = async () => {
    setBusy(true)
    try {
      const r = await provisionTenant(payload())
      toast.success(`${form.company_name} created.`)
      navigate(tenantPath(r.tenant_id, 'licensing'))
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Could not create the tenant'
      setProblems([{ field: 'admin.username', message: msg }])
      toast.error(msg)
    } finally { setBusy(false) }
  }

  const selected = packages.find(p => p.key === form.package_key)
  const blocking = problems.filter(p => p.severity !== 'warning')

  return (
    <>
      <PageHeader
        title="Onboard a client"
        sub="Creates the company, its default brand and approval chain, and its first admin login — in one transaction."
      />

      <Card style={{ marginBottom: 'var(--sp-3)' }}>
        <CardHeader title="Company" />
        <CardBody>
          <div className="adm__form">
            <Input label="Company name" required value={form.company_name}
                   onChange={e => set('company_name')(e.target.value)} />
            <div className="adm__form-row">
              <Input label="Legal name (used on invoices)" value={form.legal_name}
                     onChange={e => set('legal_name')(e.target.value)} />
              <Input label="GSTIN" value={form.gstn}
                     onChange={e => set('gstn')(e.target.value.toUpperCase())} />
            </div>
            <Input label="Primary contact email" type="email" value={form.primary_contact_email}
                   onChange={e => set('primary_contact_email')(e.target.value)} />
          </div>
        </CardBody>
      </Card>

      <Card style={{ marginBottom: 'var(--sp-3)' }}>
        <CardHeader
          title="Plan"
          sub="A package is a starting point — it copies services onto the tenant, which you can then change individually."
        />
        <CardBody>
          <div className="adm__form">
            <Select
              label="Package"
              value={form.package_key}
              onChange={e => set('package_key')(e.target.value)}
              options={[
                { value: '', label: 'Core services only' },
                ...packages.map(p => ({ value: p.key, label: p.name })),
              ]}
            />
            {selected?.services && (
              <p className="adm__svc-desc">
                Enables: {selected.services.map(s => SERVICE_LABELS[s as ServiceKey] ?? s).join(', ')}
              </p>
            )}
          </div>
        </CardBody>
      </Card>

      <Card style={{ marginBottom: 'var(--sp-3)' }}>
        <CardHeader
          title="First administrator"
          sub="Usernames are unique across every company on the platform, so a plain 'admin' is probably taken. An email address always works."
        />
        <CardBody>
          <div className="adm__form">
            <div className="adm__form-row">
              <Input label="Username" required value={form.username}
                     onChange={e => set('username')(e.target.value)} />
              <Input label="Temporary password" type="password" required value={form.password}
                     onChange={e => set('password')(e.target.value)} />
            </div>
            <div className="adm__form-row">
              <Input label="Full name" required value={form.admin_name}
                     onChange={e => set('admin_name')(e.target.value)} />
              <Input label="Email" type="email" required value={form.admin_email}
                     onChange={e => set('admin_email')(e.target.value)} />
            </div>
            {form.password.length > 0 && form.password.length < 8 && (
              <span className="adm__problem">Use at least 8 characters.</span>
            )}
          </div>
        </CardBody>
      </Card>

      {problems.length > 0 && (
        <Card style={{ marginBottom: 'var(--sp-3)' }}>
          <CardBody>
            {problems.map((p, i) => (
              <div key={i} className={p.severity === 'warning' ? 'adm__warn' : 'adm__problem'}>
                {p.field}: {p.message}
              </div>
            ))}
          </CardBody>
        </Card>
      )}

      <div style={{ display: 'flex', gap: 'var(--sp-2)' }}>
        <Button variant="secondary" disabled={!ready || busy} onClick={check}>Check for conflicts</Button>
        <Button disabled={!ready || busy || blocking.length > 0} onClick={create}>
          {busy ? 'Working…' : 'Create tenant'}
        </Button>
      </div>
    </>
  )
}
