/**
 * The vendor card's "Crypto wallets" section (05_USER_FLOWS.md, flows 2 and 3).
 *
 * Wallets are never typed in by the client: the vendor proves control from the
 * portal link, the address is screened, a cooling period runs, and only then
 * can an admin approve it. A changed wallet goes round the same loop, which is
 * what blocks the classic "please pay our new account" fraud. The contract-rate
 * editor sits here too, because it is what the overbilling check compares
 * invoices against.
 */

import { useCallback, useEffect, useState } from 'react'
import { Link2, Plus, Snowflake, Trash2, Wallet } from 'lucide-react'
import {
  apiError, approveSupplierWallet, createWalletInvite, fetchContractRates, fetchSupplierWallets, freezeSupplierWallet,
  saveContractRates, type ContractRate, type SupplierWallet, type WalletInvite,
} from '../../api/crypto'
import { Button, Input, useToast } from '../../ui'
import { AddressLink, CopyButton, Countdown, Note, StatusBadge } from './common'
import { useNow } from './helpers'
import '../../pages/crypto/crypto.css'

function WalletRow({ w, canEdit, onChange }: { w: SupplierWallet; canEdit: boolean; onChange: (w: SupplierWallet) => void }) {
  const toast = useToast()
  const now = useNow()
  const [busy, setBusy] = useState<'' | 'approve' | 'freeze'>('')
  const [freezing, setFreezing] = useState(false)
  const [reason, setReason] = useState('')
  const cooling = !!w.cooling_until && new Date(w.cooling_until).getTime() > now
  const screeningHit = w.screening_status === 'HIT'
  const canApprove = canEdit && (w.status === 'COOLING' || w.status === 'PENDING_PROOF') && !cooling && !screeningHit && !w.approved_by

  const approve = async () => {
    setBusy('approve')
    try {
      onChange(await approveSupplierWallet(w.supplier_id, w.id))
      toast.success('Wallet approved', 'It can now receive USDC payouts.')
    } catch (e) {
      toast.error('Could not approve', apiError(e).message)
    } finally {
      setBusy('')
    }
  }

  const freeze = async () => {
    if (!reason.trim()) return
    setBusy('freeze')
    try {
      onChange(await freezeSupplierWallet(w.supplier_id, w.id, reason.trim()))
      setFreezing(false)
      setReason('')
      toast.warn('Wallet frozen', 'No payout can go to it until it is re-verified.')
    } catch (e) {
      toast.error('Could not freeze', apiError(e).message)
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="u-card" style={{ padding: 'var(--sp-3) var(--sp-4)' }}>
      <div className="cr-row cr-row--between">
        <div className="cr-row">
          <Wallet size={14} aria-hidden="true" />
          <AddressLink address={w.address} href={w.explorer} />
          <span className="cr-muted">{w.asset} · {w.chain}</span>
          {w.is_primary && <span className="cr-muted">· primary</span>}
        </div>
        <div className="cr-row">
          <StatusBadge status={w.status} />
          <StatusBadge status={w.screening_status ?? 'PENDING'} title="Sanctions screening" />
        </div>
      </div>
      <div className="cr-row cr-row--between" style={{ marginTop: 'var(--sp-2)' }}>
        <div className="cr-row">
          {cooling && <Countdown until={w.cooling_until} />}
          {!cooling && w.status === 'COOLING' && <span className="cr-muted">Cooling over — awaiting admin approval</span>}
          {w.approved_by && <span className="cr-muted">Approved by {w.approved_by}</span>}
          {screeningHit && <span className="cr-muted" style={{ color: 'var(--danger-600)' }}>Screening hit — cannot be approved</span>}
        </div>
        {canEdit && (
          <div className="cr-row">
            {(w.status === 'COOLING' || w.status === 'PENDING_PROOF') && (
              <Button size="sm" variant="success" disabled={!canApprove} loading={busy === 'approve'}
                title={cooling ? 'Available when the cooling period ends' : undefined} onClick={() => void approve()}>
                Approve
              </Button>
            )}
            {w.status !== 'FROZEN' && w.status !== 'REVOKED' && (
              <Button size="sm" variant="ghost" iconLeft={<Snowflake size={13} />} onClick={() => setFreezing(f => !f)}>
                {freezing ? 'Keep active' : 'Freeze'}
              </Button>
            )}
          </div>
        )}
      </div>
      {freezing && (
        <div className="cr-form" style={{ alignItems: 'end', marginTop: 'var(--sp-2)' }}>
          <Input label="Why freeze this wallet?" hint="Recorded in the audit log" value={reason} onChange={e => setReason(e.target.value)} />
          <div>
            <Button size="sm" variant="danger" disabled={!reason.trim()} loading={busy === 'freeze'} onClick={() => void freeze()}>
              Freeze wallet
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

function ContractRates({ supplierId, canEdit }: { supplierId: string; canEdit: boolean }) {
  const toast = useToast()
  const [rates, setRates] = useState<ContractRate[] | null>(null)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    fetchContractRates(supplierId).then(setRates).catch(() => setRates([]))
  }, [supplierId])

  const update = (i: number, patch: Partial<ContractRate>) => {
    setRates(r => (r ?? []).map((x, j) => (j === i ? { ...x, ...patch } : x)))
    setDirty(true)
  }

  const save = async () => {
    if (!rates) return
    setSaving(true)
    try {
      setRates(await saveContractRates(supplierId, rates.filter(r => r.item_key.trim())))
      setDirty(false)
      toast.success('Contract rates saved', 'New bills from this vendor are checked against them.')
    } catch (e) {
      toast.error('Could not save rates', apiError(e).message)
    } finally {
      setSaving(false)
    }
  }

  if (!rates) return null
  return (
    <div className="cr-stack">
      <div className="cr-row cr-row--between">
        <h4 className="cr-section-title">Contract rates</h4>
        {canEdit && (
          <Button size="sm" variant="ghost" iconLeft={<Plus size={13} />}
            onClick={() => { setRates(r => [...(r ?? []), { item_key: '', unit_price: '', currency_code: 'USD', valid_from: null, valid_to: null }]); setDirty(true) }}>
            Add rate
          </Button>
        )}
      </div>
      {rates.length === 0 && <span className="cr-muted">No agreed rates. The overbilling check needs at least one.</span>}
      {rates.map((r, i) => (
        <div key={r.id ?? `new-${i}`} className="cr-form" style={{ alignItems: 'end' }}>
          <Input label="Item" value={r.item_key} disabled={!canEdit} placeholder="Brand design retainer" onChange={e => update(i, { item_key: e.target.value })} />
          <Input label="Unit price" type="number" value={r.unit_price} disabled={!canEdit} onChange={e => update(i, { unit_price: e.target.value })} />
          <Input label="Currency" value={r.currency_code} maxLength={4} disabled={!canEdit} onChange={e => update(i, { currency_code: e.target.value.toUpperCase() })} />
          {canEdit && (
            <div>
              <Button size="sm" variant="ghost" iconLeft={<Trash2 size={13} />} aria-label="Remove rate"
                onClick={() => { setRates(x => (x ?? []).filter((_, j) => j !== i)); setDirty(true) }}>
                Remove
              </Button>
            </div>
          )}
        </div>
      ))}
      {canEdit && dirty && (
        <div><Button size="sm" variant="primary" loading={saving} onClick={() => void save()}>Save rates</Button></div>
      )}
    </div>
  )
}

export default function VendorCryptoWallets({ supplierId, canEdit, onWalletsChanged }: {
  supplierId: string
  canEdit: boolean
  /** Called after a wallet is approved or frozen, so the card can re-read the vendor's verification badge. */
  onWalletsChanged?: () => void
}) {
  const toast = useToast()
  const [wallets, setWallets] = useState<SupplierWallet[] | null>(null)
  const [err, setErr] = useState('')
  const [invite, setInvite] = useState<WalletInvite | null>(null)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviting, setInviting] = useState(false)

  const load = useCallback(() => {
    fetchSupplierWallets(supplierId).then(w => { setWallets(w); setErr('') }).catch(e => setErr(apiError(e).message))
  }, [supplierId])
  useEffect(load, [load])

  // Cooling ends on its own clock — re-read every 20s while a wallet is cooling,
  // so Approve unlocks without a manual refresh.
  const anyCooling = wallets?.some(w => w.status === 'COOLING' || w.status === 'PENDING_PROOF')
  useEffect(() => {
    if (!anyCooling) return
    const t = setInterval(load, 20_000)
    return () => clearInterval(t)
  }, [anyCooling, load])

  const sendInvite = async () => {
    setInviting(true)
    try {
      const inv = await createWalletInvite(supplierId, inviteEmail.trim() || undefined)
      setInvite(inv)
      toast.success('Wallet invite ready', inv.emailed_to ? `Emailed to ${inv.emailed_to}.` : 'Copy the link and send it to the vendor.')
    } catch (e) {
      toast.error('Could not create the invite', apiError(e).message)
    } finally {
      setInviting(false)
    }
  }

  const active = wallets?.find(w => w.status === 'ACTIVE')

  return (
    <div className="cr-stack">
      <div className="cr-row cr-row--between">
        <div className="cr-row">
          <Wallet size={16} aria-hidden="true" />
          <strong>Crypto wallets</strong>
        </div>
        {active ? <StatusBadge status="ACTIVE" title="This vendor can be paid in USDC" /> : <span className="cr-muted">No active wallet</span>}
      </div>

      {err && <Note tone="danger">{err}</Note>}
      {wallets && wallets.length === 0 && (
        <span className="cr-muted">The vendor has not registered a wallet. Send them an invite: they connect their wallet and sign a message to prove they own it.</span>
      )}
      {wallets?.map(w => (
        <WalletRow key={w.id} w={w} canEdit={canEdit} onChange={nw => { setWallets(list => (list ?? []).map(x => (x.id === nw.id ? nw : x))); onWalletsChanged?.() }} />
      ))}

      {canEdit && (
        <div className="cr-stack" style={{ gap: 'var(--sp-2)' }}>
          <div className="cr-form" style={{ alignItems: 'end' }}>
            <Input label="Vendor email (optional)" type="email" value={inviteEmail} placeholder="billing@vendor.com" onChange={e => setInviteEmail(e.target.value)} />
            <div>
              <Button iconLeft={<Link2 size={13} />} loading={inviting} onClick={() => void sendInvite()}>
                {wallets && wallets.length > 0 ? 'Send a new wallet invite' : 'Send wallet invite'}
              </Button>
            </div>
          </div>
          {invite && (
            <Note tone="ok">
              <div className="cr-row">
                <span className="cr-mono cr-grow">{invite.invite_url}</span>
                <CopyButton text={invite.invite_url} label="Copy link" />
              </div>
              <span className="cr-muted">Valid until {new Date(invite.expires_at).toLocaleString()}.</span>
            </Note>
          )}
        </div>
      )}

      <ContractRates supplierId={supplierId} canEdit={canEdit} />
    </div>
  )
}
