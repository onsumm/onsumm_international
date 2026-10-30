/**
 * Crypto onboarding — the foreign client's setup, in the order the demo runs it
 * (docs/crypto-rail/05_USER_FLOWS.md, flow 1): entity profile and KYB, UBOs
 * with screening, the treasury Safe (owners and threshold read from the chain,
 * never typed in), and the rail policy.
 *
 * An India-resident tenant sees the same page with the crypto rail locked off
 * and the reason spelled out: that block is a feature, not an error.
 */

import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, Trash2, Wallet } from 'lucide-react'
import { useAuth } from '../../auth/AuthContext'
import {
  addUbo, apiError, codeCopy, connectTreasuryWallet, decideKyb, deleteUbo, fetchProfile, fetchRailPolicy,
  fetchTreasuryWallets, fetchUbos, refreshTreasuryWallet, saveRailPolicy, submitKyb,
  type KybInput, type Profile, type RailPolicy, type TreasuryWallet, type Ubo,
} from '../../api/crypto'
import {
  Badge, Button, Card, CardBody, CardHeader, Checkbox, ErrorState, Input, PageHeader, Select, Skeleton, useToast,
} from '../../ui'
import { AddressLink, Note, RefusalList, StatusBadge } from '../../components/crypto/common'
import { formatUsdc, isAddress } from '../../web3/units'
import { BASE_SEPOLIA } from '../../web3/eip1193'
import './crypto.css'

const CURRENCIES = ['USD', 'SGD', 'AED', 'EUR', 'GBP', 'INR'].map(c => ({ value: c, label: c }))

function ProfileCard({ profile, isAdmin, onSaved }: { profile: Profile; isAdmin: boolean; onSaved: (p: Profile) => void }) {
  const toast = useToast()
  const [form, setForm] = useState<KybInput>({
    legal_name: profile.legal_name ?? profile.company_name ?? '',
    country_code: profile.country_code ?? '',
    registration_number: profile.registration_number ?? '',
    tax_residency: profile.tax_residency ?? profile.country_code ?? '',
    base_currency: profile.base_currency ?? 'USD',
    documents: [],
  })
  const [docName, setDocName] = useState('')
  const [saving, setSaving] = useState(false)
  const [deciding, setDeciding] = useState<'' | 'APPROVED' | 'REJECTED'>('')

  const set = <K extends keyof KybInput>(k: K, v: KybInput[K]) => setForm(f => ({ ...f, [k]: v }))
  const valid = form.legal_name.trim() && /^[A-Za-z]{2}$/.test(form.country_code) && form.registration_number.trim()

  const submit = async () => {
    setSaving(true)
    try {
      const docs = docName.trim() ? [{ name: docName.trim() }] : []
      const p = await submitKyb({ ...form, country_code: form.country_code.toUpperCase(), tax_residency: (form.tax_residency || form.country_code).toUpperCase(), documents: docs })
      onSaved(p)
      setDocName('')
      toast.success('KYB submitted', 'An admin can now review and approve it.')
    } catch (e) {
      toast.error('Could not submit KYB', apiError(e).message)
    } finally {
      setSaving(false)
    }
  }

  const decide = async (d: 'APPROVED' | 'REJECTED') => {
    setDeciding(d)
    try {
      onSaved(await decideKyb(d))
      toast.success(d === 'APPROVED' ? 'KYB approved' : 'KYB rejected')
    } catch (e) {
      toast.error('Could not record the decision', apiError(e).message)
    } finally {
      setDeciding('')
    }
  }

  return (
    <Card>
      <CardHeader
        title="Entity profile and KYB"
        sub="Who the company is, where it is resident, and the currency it keeps its books in."
        actions={<StatusBadge status={profile.kyb_status} />}
      />
      <CardBody>
        <div className="cr-stack">
          <div className="cr-form">
            <Input label="Legal name" value={form.legal_name} onChange={e => set('legal_name', e.target.value)} required />
            <Input label="Country (ISO code)" value={form.country_code} maxLength={2} placeholder="SG" onChange={e => set('country_code', e.target.value.toUpperCase())} required />
            <Input label="Registration number" value={form.registration_number} onChange={e => set('registration_number', e.target.value)} required />
            <Input label="Tax residency" value={form.tax_residency} maxLength={2} placeholder="SG" onChange={e => set('tax_residency', e.target.value.toUpperCase())} />
            <Select label="Books kept in" value={form.base_currency} options={CURRENCIES} onChange={e => set('base_currency', e.target.value)} />
            <Input label="KYB document (name)" value={docName} placeholder="Certificate of incorporation.pdf" onChange={e => setDocName(e.target.value)} hint="Recorded for manual review in the hackathon build." />
          </div>
          <div className="cr-row">
            <Button variant="primary" loading={saving} disabled={!valid} onClick={() => void submit()}>
              Save and submit for review
            </Button>
            {isAdmin && profile.kyb_status === 'SUBMITTED' && (
              <>
                <Button variant="success" loading={deciding === 'APPROVED'} onClick={() => void decide('APPROVED')}>Approve KYB</Button>
                <Button variant="danger" loading={deciding === 'REJECTED'} onClick={() => void decide('REJECTED')}>Reject</Button>
              </>
            )}
            <span className="cr-grow" />
            <span className="cr-muted">
              Jurisdiction profile: <strong>{profile.jurisdiction_profile ?? '—'}</strong>
              {' · '}
              {profile.is_resident_india ? 'India-resident' : 'Not resident in India'}
            </span>
          </div>
        </div>
      </CardBody>
    </Card>
  )
}

function UboCard() {
  const toast = useToast()
  const [ubos, setUbos] = useState<Ubo[] | null>(null)
  const [err, setErr] = useState('')
  const [form, setForm] = useState({ name: '', country_code: '', ownership_pct: '' })
  const [adding, setAdding] = useState(false)

  const load = useCallback(() => {
    fetchUbos().then(u => { setUbos(u); setErr('') }).catch(e => setErr(apiError(e).message))
  }, [])
  useEffect(load, [load])

  const pct = Number(form.ownership_pct)
  const valid = form.name.trim() && /^[A-Za-z]{2}$/.test(form.country_code) && pct >= 25 && pct <= 100

  const add = async () => {
    setAdding(true)
    try {
      const u = await addUbo({ name: form.name.trim(), country_code: form.country_code.toUpperCase(), ownership_pct: pct })
      setUbos(list => [...(list ?? []), u])
      setForm({ name: '', country_code: '', ownership_pct: '' })
      if (u.screening_status === 'HIT') toast.error('Screening hit', `${u.name} matched the sanctions list.`)
    } catch (e) {
      toast.error('Could not add UBO', apiError(e).message)
    } finally {
      setAdding(false)
    }
  }

  return (
    <Card>
      <CardHeader title="Beneficial owners" sub="Everyone holding 25% or more. Each is screened against the sanctions list when saved." />
      <CardBody>
        <div className="cr-stack">
          {err && <ErrorState message={err} onRetry={load} />}
          {!ubos && !err && <Skeleton height={40} />}
          {ubos && ubos.length > 0 && (
            <table className="cr-table">
              <thead>
                <tr><th>Name</th><th>Country</th><th className="num">Ownership</th><th>Screening</th><th aria-label="Actions" /></tr>
              </thead>
              <tbody>
                {ubos.map(u => (
                  <tr key={u.id}>
                    <td>{u.name}</td>
                    <td>{u.country_code}</td>
                    <td className="num">{Number(u.ownership_pct)}%</td>
                    <td><StatusBadge status={u.screening_status} /></td>
                    <td style={{ textAlign: 'right' }}>
                      <Button size="sm" variant="ghost" iconLeft={<Trash2 size={13} />} aria-label={`Remove ${u.name}`}
                        onClick={() => void deleteUbo(u.id).then(() => setUbos(l => (l ?? []).filter(x => x.id !== u.id))).catch(e => toast.error('Could not remove', apiError(e).message))}>
                        Remove
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {ubos && ubos.length === 0 && <span className="cr-muted">No beneficial owners recorded yet.</span>}
          <div className="cr-form">
            <Input label="Name" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
            <Input label="Country" value={form.country_code} maxLength={2} placeholder="SG" onChange={e => setForm(f => ({ ...f, country_code: e.target.value.toUpperCase() }))} />
            <Input label="Ownership %" type="number" min={25} max={100} value={form.ownership_pct} onChange={e => setForm(f => ({ ...f, ownership_pct: e.target.value }))} hint="25 to 100" />
          </div>
          <div>
            <Button loading={adding} disabled={!valid} onClick={() => void add()}>Add and screen</Button>
          </div>
        </div>
      </CardBody>
    </Card>
  )
}

function TreasuryCard({ disabled }: { disabled: boolean }) {
  const toast = useToast()
  const [wallets, setWallets] = useState<TreasuryWallet[] | null>(null)
  const [err, setErr] = useState('')
  const [role, setRole] = useState<'TREASURY' | 'TAX'>('TREASURY')
  const [address, setAddress] = useState('')
  const [saving, setSaving] = useState(false)
  const [refreshing, setRefreshing] = useState('')

  const load = useCallback(() => {
    fetchTreasuryWallets().then(w => { setWallets(w); setErr('') }).catch(e => setErr(apiError(e).message))
  }, [])
  useEffect(load, [load])

  const connect = async () => {
    setSaving(true)
    try {
      const w = await connectTreasuryWallet({ role, chain: 'base-sepolia', address: address.trim() })
      setWallets(list => [...(list ?? []).filter(x => x.id !== w.id), w])
      setAddress('')
      toast.success(role === 'TREASURY' ? 'Treasury Safe connected' : 'Tax wallet connected',
        w.custody === 'SAFE' ? `${w.safe_owners.length} owners, threshold ${w.safe_threshold} — read from the chain.` : undefined)
    } catch (e) {
      const info = apiError(e)
      toast.error('Could not connect the wallet', info.codes.length ? info.codes.map(codeCopy).join(' ') : info.message)
    } finally {
      setSaving(false)
    }
  }

  const refresh = async (id: string) => {
    setRefreshing(id)
    try {
      const w = await refreshTreasuryWallet(id)
      setWallets(list => (list ?? []).map(x => (x.id === id ? w : x)))
    } catch (e) {
      const info = apiError(e)
      toast.error(info.codes.includes('SAFE_CONFIG_CHANGED') ? 'Safe changed outside Onsumm' : 'Could not refresh', info.codes.map(codeCopy).join(' ') || info.message)
    } finally {
      setRefreshing('')
    }
  }

  return (
    <Card>
      <CardHeader
        title="Treasury Safe"
        sub="The company’s own Safe on Base Sepolia. Owners and threshold are read from the chain; Onsumm never holds a key that can move funds."
      />
      <CardBody>
        <div className="cr-stack">
          {err && <ErrorState message={err} onRetry={load} />}
          {!wallets && !err && <Skeleton height={60} />}
          {wallets?.map(w => (
            <div key={w.id} className="u-card" style={{ padding: 'var(--sp-3) var(--sp-4)' }}>
              <div className="cr-row cr-row--between">
                <div className="cr-row">
                  <Wallet size={14} aria-hidden="true" />
                  <strong>{w.role === 'TREASURY' ? 'Treasury' : 'Tax wallet'}</strong>
                  <Badge tone="neutral">{w.custody === 'SAFE' ? 'Safe' : 'Wallet'}</Badge>
                  <StatusBadge status={w.status} />
                </div>
                <Button size="sm" variant="ghost" iconLeft={<RefreshCw size={13} />} loading={refreshing === w.id} onClick={() => void refresh(w.id)}>
                  Re-read on-chain
                </Button>
              </div>
              <dl className="cr-kv" style={{ marginTop: 'var(--sp-2)' }}>
                <dt>Address</dt><dd><AddressLink address={w.address} href={w.explorer} /></dd>
                <dt>Chain</dt><dd>{w.chain}</dd>
                {w.custody === 'SAFE' && (
                  <>
                    <dt>Threshold</dt><dd>{w.safe_threshold} of {w.safe_owners.length} owners</dd>
                    <dt>Owners</dt>
                    <dd className="cr-stack" style={{ gap: 2 }}>
                      {w.safe_owners.map(o => <span key={o} className="cr-mono">{o}</span>)}
                    </dd>
                  </>
                )}
                <dt>USDC balance</dt><dd>{w.usdc_balance_units != null ? formatUsdc(w.usdc_balance_units) : '—'}</dd>
              </dl>
            </div>
          ))}
          <div className="cr-form">
            <Select label="Role" value={role} disabled={disabled}
              options={[{ value: 'TREASURY', label: 'Treasury Safe (pays vendors)' }, { value: 'TAX', label: 'Tax wallet (holds withholding)' }]}
              onChange={e => setRole(e.target.value as 'TREASURY' | 'TAX')} />
            <Input label="Address on Base Sepolia" mono value={address} placeholder="0x…" disabled={disabled}
              error={address && !isAddress(address) ? 'Not a valid address' : null} onChange={e => setAddress(e.target.value)} />
          </div>
          <div>
            <Button variant="primary" loading={saving} disabled={disabled || !isAddress(address)} onClick={() => void connect()}>
              Connect
            </Button>
          </div>
        </div>
      </CardBody>
    </Card>
  )
}

function PolicyCard({ profile, onChanged }: { profile: Profile; onChanged: () => void }) {
  const toast = useToast()
  const [policy, setPolicy] = useState<RailPolicy | null>(null)
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)
  const [refusal, setRefusal] = useState<{ codes: string[]; messages: string[] } | null>(null)

  const load = useCallback(() => {
    fetchRailPolicy().then(p => { setPolicy(p); setErr('') }).catch(e => setErr(apiError(e).message))
  }, [])
  useEffect(load, [load])

  const set = <K extends keyof RailPolicy>(k: K, v: RailPolicy[K]) => setPolicy(p => (p ? { ...p, [k]: v } : p))

  const save = async () => {
    if (!policy) return
    setSaving(true)
    setRefusal(null)
    try {
      setPolicy(await saveRailPolicy(policy))
      toast.success('Rail policy saved')
      onChanged()
    } catch (e) {
      const info = apiError(e)
      if (info.codes.length) setRefusal({ codes: info.codes, messages: [] })
      else toast.error('Could not save the policy', info.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader title="Rail policy" sub="Checked by the policy engine before any payout is signed." />
      <CardBody>
        {err && <ErrorState message={err} onRetry={load} />}
        {!policy && !err && <Skeleton height={80} />}
        {policy && (
          <div className="cr-stack">
            <Checkbox
              label="Crypto rail enabled"
              checked={policy.crypto_enabled}
              disabled={profile.is_resident_india}
              onChange={e => set('crypto_enabled', e.target.checked)}
            />
            <div className="cr-form">
              <Input label="Allowed chains" value={policy.allowed_chains.join(', ')} onChange={e => set('allowed_chains', e.target.value.split(',').map(s => s.trim()).filter(Boolean))} hint="base-sepolia for the hackathon" />
              <Input label="Allowed tokens" value={policy.allowed_tokens.join(', ')} onChange={e => set('allowed_tokens', e.target.value.split(',').map(s => s.trim().toUpperCase()).filter(Boolean))} hint="USDC only" />
              <Input label="Per-payment limit (USD)" type="number" value={policy.per_tx_limit_usd ?? ''} onChange={e => set('per_tx_limit_usd', e.target.value)} />
              <Input label="Daily limit (USD)" type="number" value={policy.daily_limit_usd ?? ''} onChange={e => set('daily_limit_usd', e.target.value)} />
              <Input label="Dual control above (USD)" type="number" value={policy.dual_control_above_usd ?? ''} onChange={e => set('dual_control_above_usd', e.target.value)} />
              <Input label="Wallet cooling period (minutes)" type="number" min={0} value={policy.cooling_minutes} onChange={e => set('cooling_minutes', Number(e.target.value))} hint="1440 in production; 2 for the demo" />
            </div>
            <Checkbox
              label="Send a 1 USDC test transfer before the first payout to a new wallet"
              checked={policy.require_test_transfer}
              onChange={e => set('require_test_transfer', e.target.checked)}
            />
            {refusal && <RefusalList codes={refusal.codes} messages={refusal.messages} title="The policy was not saved" />}
            <div>
              <Button variant="primary" loading={saving} onClick={() => void save()}>Save policy</Button>
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  )
}

export default function CryptoOnboardingPage() {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const [profile, setProfile] = useState<Profile | null>(null)
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    fetchProfile().then(p => { setProfile(p); setErr('') }).catch(e => setErr(apiError(e).message))
  }, [])
  useEffect(load, [load])

  return (
    <div className="cr">
      <PageHeader
        title="Crypto rail setup"
        sub={`Onboard a client outside India to pay vendors in USDC on ${BASE_SEPOLIA.name}. Testnet only — no real funds.`}
        actions={profile && (
          profile.crypto_enabled
            ? <Badge tone="success">Crypto rail on</Badge>
            : <Badge tone="neutral">Crypto rail off</Badge>
        )}
      />

      {err && <ErrorState title="Could not load the company profile" message={err} onRetry={load} />}
      {!profile && !err && <Skeleton height={160} radius={12} />}

      {profile && profile.is_resident_india && (
        <Note tone="danger">
          <strong>Crypto payouts are disabled for this company.</strong> It is resident in India
          (<span className="cr-mono">CRYPTO_DISABLED_RESIDENT_INDIA</span>). {codeCopy('CRYPTO_DISABLED_RESIDENT_INDIA')} Indian
          withholding (Section 195) applies on the bank rail instead.
        </Note>
      )}

      {profile && (
        <>
          <ProfileCard profile={profile} isAdmin={isAdmin} onSaved={setProfile} />
          <div className="cr-grid">
            <UboCard />
            <PolicyCard profile={profile} onChanged={load} />
          </div>
          <TreasuryCard disabled={profile.is_resident_india} />
        </>
      )}
    </div>
  )
}
