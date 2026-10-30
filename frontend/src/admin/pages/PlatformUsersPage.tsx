import { useEffect, useState } from 'react'
import { UserPlus } from 'lucide-react'
import {
  Badge, Button, Card, CardBody, ErrorState, Input, Modal, PageHeader, Select,
  SkeletonRows, Switch, formatDateTime, useToast,
} from '../../ui'
import {
  createPlatformUser, listPlatformUsers, patchPlatformUser,
  type AdminRole, type PlatformUser,
} from '../../api/admin'
import { useAdminAuth, useAdminCan } from '../AdminAuthContext'

const ROLES: { value: AdminRole; label: string; hint: string }[] = [
  { value: 'readonly', label: 'Read-only', hint: 'Can look at tenants and the audit log.' },
  { value: 'support', label: 'Support', hint: 'Can also open read-only support sessions.' },
  { value: 'admin', label: 'Admin', hint: 'Can also change licensing and onboard clients.' },
  { value: 'owner', label: 'Owner', hint: 'Can also manage platform users.' },
]

export default function PlatformUsersPage() {
  const { admin } = useAdminAuth()
  const isOwner = useAdminCan('owner')
  const toast = useToast()
  const [users, setUsers] = useState<PlatformUser[] | null>(null)
  const [error, setError] = useState('')
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ email: '', name: '', password: '', role: 'support' as AdminRole })

  const load = () => {
    listPlatformUsers().then(setUsers)
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load platform users'))
  }
  useEffect(load, [])

  const create = async () => {
    try {
      await createPlatformUser(form)
      toast.success(`${form.email} added`)
      setAdding(false)
      setForm({ email: '', name: '', password: '', role: 'support' })
      load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not add user')
    }
  }

  const toggleActive = async (u: PlatformUser, active: boolean) => {
    try {
      await patchPlatformUser(u.id, { is_active: active })
      load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not update')
    }
  }

  if (error) return <ErrorState title="Could not load platform users" message={error} />
  if (!users) return <SkeletonRows rows={5} />

  return (
    <>
      <PageHeader
        title="Platform users"
        sub="Who can sign into this panel. Separate from every customer's user list."
        actions={isOwner && (
          <Button iconLeft={<UserPlus size={14} />} onClick={() => setAdding(true)}>Add user</Button>
        )}
      />
      <Card><CardBody><div className="adm__tablewrap">
        <table className="adm__table">
          <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Last sign-in</th><th>Active</th></tr></thead>
          <tbody>
            {users.map(u => {
              const isSelf = u.email.toLowerCase() === admin?.email.toLowerCase()
              return (
                <tr key={u.id}>
                  <td>{u.name}{isSelf && <span className="adm__svc-desc">you</span>}</td>
                  <td>{u.email}</td>
                  <td><Badge tone="neutral">{u.role}</Badge></td>
                  <td>{u.last_login_at ? formatDateTime(u.last_login_at) : 'never'}</td>
                  <td>
                    <Switch
                      checked={u.is_active}
                      label={`${u.email} active`}
                      /* Owners cannot deactivate themselves — locking the last owner
                         out of the panel is only recoverable in psql. */
                      disabled={!isOwner || isSelf}
                      onChange={v => toggleActive(u, v)}
                    />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div></CardBody></Card>

      <Modal open={adding} onClose={() => setAdding(false)} title="Add a platform user" size="sm">
        <div className="adm__form">
          <Input label="Email" type="email" value={form.email}
                 onChange={e => setForm(s => ({ ...s, email: e.target.value }))} />
          <Input label="Full name" value={form.name}
                 onChange={e => setForm(s => ({ ...s, name: e.target.value }))} />
          <Input label="Temporary password" type="password" value={form.password}
                 onChange={e => setForm(s => ({ ...s, password: e.target.value }))} />
          {form.password.length > 0 && form.password.length < 12 && (
            <span className="adm__problem">Platform passwords must be at least 12 characters.</span>
          )}
          <Select
            label="Role"
            value={form.role}
            onChange={e => setForm(s => ({ ...s, role: e.target.value as AdminRole }))}
            options={ROLES.map(r => ({ value: r.value, label: r.label }))}
          />
          <p className="adm__svc-desc">{ROLES.find(r => r.value === form.role)?.hint}</p>
          <div style={{ display: 'flex', gap: 'var(--sp-2)', justifyContent: 'flex-end' }}>
            <Button variant="secondary" onClick={() => setAdding(false)}>Cancel</Button>
            <Button disabled={!form.email || !form.name || form.password.length < 12} onClick={create}>
              Add user
            </Button>
          </div>
        </div>
      </Modal>
    </>
  )
}
