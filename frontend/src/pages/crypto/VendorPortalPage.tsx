/**
 * Vendor portal — public, no login (05_USER_FLOWS.md, flow 2).
 *
 * The vendor arrives from an emailed link carrying a signed, expiring token.
 * They connect a wallet, switch to Base Sepolia, and sign a SIWE (EIP-4361)
 * message; the server recovers the signer from the signature, so the address
 * cannot be typed in on someone else's behalf. Screening and the cooling
 * period follow, then the client's admin approves.
 */

import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { CheckCircle2, PenLine, Wallet } from 'lucide-react'
import {
  apiError, codeCopy, fetchPortal, fetchPortalChallenge, submitPortalTaxDoc, verifyPortalWallet,
  type PortalInfo, type PortalVerifyResult, type TaxDocType,
} from '../../api/crypto'
import { Button, Card, CardBody, CardHeader, ErrorState, Input, LoadingMark, Select, useToast } from '../../ui'
import { AddressLink, Countdown, Note, StatusBadge } from '../../components/crypto/common'
import {
  BASE_SEPOLIA, connectWallet, currentChainId, ensureBaseSepolia, hasWallet, personalSign, walletErrorMessage,
} from '../../web3/eip1193'
import { explorerAddress } from '../../web3/units'
import { useDocumentTitle } from '../../hooks/useDocumentTitle'
import './crypto.css'

type Step = 'connect' | 'sign' | 'done'

const TAX_DOCS: { value: TaxDocType; label: string }[] = [
  { value: 'W-8BEN-E', label: 'W-8BEN-E (US entity)' },
  { value: 'TRC', label: 'Tax residency certificate' },
  { value: 'FORM_10F', label: 'Form 10F' },
  { value: 'NO_PE', label: 'No permanent establishment declaration' },
]

export default function VendorPortalPage() {
  useDocumentTitle('Register your payout wallet · Onsumm')
  const { token = '' } = useParams<{ token: string }>()
  const toast = useToast()
  const [info, setInfo] = useState<PortalInfo | null>(null)
  const [linkError, setLinkError] = useState('')
  const [loadError, setLoadError] = useState('')
  const [account, setAccount] = useState<string | null>(null)
  const [chainOk, setChainOk] = useState(false)
  const [step, setStep] = useState<Step>('connect')
  const [busy, setBusy] = useState(false)
  const [walletErr, setWalletErr] = useState('')
  const [result, setResult] = useState<PortalVerifyResult | null>(null)
  const [docType, setDocType] = useState<TaxDocType>('W-8BEN-E')
  const [docRef, setDocRef] = useState('')
  const [docSaved, setDocSaved] = useState(false)

  const load = useCallback(() => {
    fetchPortal(token)
      .then(i => { setInfo(i); setLoadError(''); setLinkError('') })
      .catch(e => {
        const err = apiError(e)
        if (err.status === 401 || err.error === 'PORTAL_LINK_INVALID') setLinkError(codeCopy('PORTAL_LINK_INVALID'))
        else setLoadError(err.message)
      })
  }, [token])
  useEffect(load, [load])

  const connect = async () => {
    setBusy(true)
    setWalletErr('')
    try {
      const addr = await connectWallet()
      setAccount(addr)
      await ensureBaseSepolia()
      setChainOk((await currentChainId()) === BASE_SEPOLIA.chainId)
      setStep('sign')
    } catch (e) {
      setWalletErr(walletErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const sign = async () => {
    if (!account || !info) return
    setBusy(true)
    setWalletErr('')
    try {
      if ((await currentChainId()) !== BASE_SEPOLIA.chainId) await ensureBaseSepolia()
      const challenge = await fetchPortalChallenge(token, info.chain || 'base-sepolia', account)
      const signature = await personalSign(challenge.message, account)
      const res = await verifyPortalWallet(token, challenge.message, signature)
      setResult(res)
      setStep('done')
      if (res.screening === 'HIT') toast.error('Wallet refused', 'This address failed sanctions screening.')
    } catch (e) {
      const err = apiError(e)
      setWalletErr(err.status ? err.message : walletErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const saveDoc = async () => {
    try {
      await submitPortalTaxDoc(token, docType, docRef.trim())
      setDocSaved(true)
      toast.success('Tax document recorded')
    } catch (e) {
      toast.error('Could not save the document', apiError(e).message)
    }
  }

  return (
    <div className="cr-public">
      <div className="cr-public__inner">
        <div className="cr-public__brand">
          <img src="/brand/onsumm-mark.svg" alt="" />
          Onsumm · Vendor payout wallet
        </div>

        {linkError && <Note tone="danger"><strong>This link can’t be used.</strong> {linkError}</Note>}
        {loadError && <ErrorState message={loadError} onRetry={load} />}
        {!info && !linkError && !loadError && <LoadingMark size={22} />}

        {info && (
          <>
            <Card>
              <CardHeader
                title={`Get paid in USDC by ${info.tenant_name}`}
                sub={`Registering a payout wallet for ${info.supplier_name} on ${BASE_SEPOLIA.name}. Testnet — no real funds.`}
              />
              <CardBody>
                <div className="cr-stack">
                  <Note>
                    You will sign a short message with your wallet. Signing proves you control the address; it does not
                    move any funds and costs no gas. Link valid until {new Date(info.expires_at).toLocaleString()}.
                  </Note>

                  {info.wallets.length > 0 && (
                    <div className="cr-stack" style={{ gap: 'var(--sp-2)' }}>
                      <span className="cr-muted">Wallets already on file:</span>
                      {info.wallets.map(w => (
                        <div key={w.id} className="cr-row">
                          <AddressLink address={w.address} href={explorerAddress(w.address)} />
                          <StatusBadge status={w.status} />
                        </div>
                      ))}
                      <span className="cr-muted">Registering a new wallet freezes the old one until your client approves the change.</span>
                    </div>
                  )}

                  {!hasWallet() && (
                    <Note tone="warn">No browser wallet found. Install MetaMask or Rabby, then reload this page.</Note>
                  )}

                  {step === 'connect' && (
                    <div>
                      <Button variant="primary" size="lg" iconLeft={<Wallet size={15} />} loading={busy} disabled={!hasWallet()} onClick={() => void connect()}>
                        Connect wallet
                      </Button>
                    </div>
                  )}

                  {step === 'sign' && account && (
                    <div className="cr-stack">
                      <dl className="cr-kv">
                        <dt>Wallet</dt><dd className="cr-mono">{account}</dd>
                        <dt>Network</dt><dd>{chainOk ? BASE_SEPOLIA.name : 'Wrong network — switch to Base Sepolia'}</dd>
                      </dl>
                      <div>
                        <Button variant="primary" size="lg" iconLeft={<PenLine size={15} />} loading={busy} onClick={() => void sign()}>
                          Sign to prove ownership
                        </Button>
                      </div>
                    </div>
                  )}

                  {walletErr && <Note tone="danger">{walletErr}</Note>}

                  {step === 'done' && result && (
                    <div className="cr-stack">
                      {result.screening === 'HIT' ? (
                        <Note tone="danger">This wallet did not pass screening and cannot receive payments. Contact {info.tenant_name}.</Note>
                      ) : (
                        <Note tone="ok">
                          <CheckCircle2 size={14} style={{ verticalAlign: '-2px' }} aria-hidden="true" /> Wallet verified.{' '}
                          {result.needs_admin_approval ? `${info.tenant_name} will approve it after the cooling period.` : 'It is ready to receive payouts.'}
                        </Note>
                      )}
                      <dl className="cr-kv">
                        <dt>Status</dt><dd><StatusBadge status={result.status} /></dd>
                        <dt>Screening</dt><dd><StatusBadge status={result.screening} /></dd>
                        {result.cooling_until && (<><dt>Cooling period</dt><dd><Countdown until={result.cooling_until} doneLabel="Over — awaiting approval" /></dd></>)}
                      </dl>
                    </div>
                  )}
                </div>
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Tax documents" sub="For a US company paying from abroad, a W-8BEN-E is usually enough. TRC and Form 10F matter only when the payer is in India." />
              <CardBody>
                <div className="cr-form" style={{ alignItems: 'end' }}>
                  <Select label="Document" value={docType} options={TAX_DOCS} onChange={e => setDocType(e.target.value as TaxDocType)} />
                  <Input label="Reference or file name" value={docRef} onChange={e => { setDocRef(e.target.value); setDocSaved(false) }} />
                  <div>
                    <Button disabled={!docRef.trim() || docSaved} onClick={() => void saveDoc()}>{docSaved ? 'Saved' : 'Save'}</Button>
                  </div>
                </div>
              </CardBody>
            </Card>
          </>
        )}
      </div>
    </div>
  )
}
