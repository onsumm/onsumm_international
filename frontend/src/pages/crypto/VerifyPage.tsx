/**
 * /verify — public proof checker (05_USER_FLOWS.md, flow 8; README step 8).
 *
 * An auditor drops a proof bundle (or opens a share link) and the browser
 * recomputes every hash itself, then reads the anchor transaction straight from
 * a public Base Sepolia RPC. Onsumm's server is only the courier for the bundle;
 * it is not trusted for the answer. Bill IDs are never typed in here, so records
 * cannot be enumerated.
 *
 * "Tamper one field" edits the record text before checking — the demo's way of
 * showing that a single changed character fails the proof.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CheckCircle2, FileJson, ShieldAlert, ShieldCheck, XCircle } from 'lucide-react'
import { fetchSharedProof } from '../../api/audit'
import { apiError } from '../../api/crypto'
import { Button, Card, CardBody, CardHeader, Checkbox, LoadingMark } from '../../ui'
import { Note, TxLink } from '../../components/crypto/common'
import { getTransactionByHash } from '../../web3/eip1193'
import { parseBundle, short, verifyBundle, type ProofBundle, type VerifyOutcome } from '../../web3/merkle'
import { useDocumentTitle } from '../../hooks/useDocumentTitle'
import './crypto.css'

/** Flips one character inside the record text: the first digit, else the first letter. */
function tamper(canonical: string): string {
  const i = canonical.search(/[0-9]/)
  if (i >= 0) return canonical.slice(0, i) + (canonical[i] === '9' ? '8' : String(Number(canonical[i]) + 1)) + canonical.slice(i + 1)
  const j = canonical.search(/[a-z]/i)
  return j >= 0 ? canonical.slice(0, j) + (canonical[j] === 'x' ? 'y' : 'x') + canonical.slice(j + 1) : canonical + ' '
}

const fetchAnchorTx = async (hash: string) => {
  const tx = await getTransactionByHash(hash)
  return tx ? { input: tx.input, to: tx.to, from: tx.from } : null
}

export default function VerifyPage() {
  useDocumentTitle('Verify an audit proof · Onsumm')
  const [params] = useSearchParams()
  const share = params.get('share')
  const [bundle, setBundle] = useState<ProofBundle | null>(null)
  const [pasted, setPasted] = useState('')
  const [loadErr, setLoadErr] = useState('')
  const [over, setOver] = useState(false)
  const [tampered, setTampered] = useState(false)
  const [outcome, setOutcome] = useState<VerifyOutcome | null>(null)
  const [running, setRunning] = useState(false)

  const accept = useCallback((raw: unknown) => {
    try {
      setBundle(parseBundle(raw))
      setLoadErr('')
      setTampered(false)
    } catch (e) {
      setBundle(null)
      setLoadErr((e as Error).message)
    }
  }, [])

  useEffect(() => {
    if (!share) return
    fetchSharedProof(share)
      .then(accept)
      .catch(e => setLoadErr(`Could not open the shared proof: ${apiError(e).message}`))
  }, [share, accept])

  const readFile = (file: File) => {
    file.text()
      .then(t => accept(JSON.parse(t)))
      .catch(() => setLoadErr('That file is not valid JSON.'))
  }

  const canonical = useMemo(() => (bundle ? (tampered ? tamper(bundle.canonical) : bundle.canonical) : ''), [bundle, tampered])

  useEffect(() => {
    if (!bundle) { setOutcome(null); return }
    let alive = true
    setRunning(true)
    verifyBundle(bundle, fetchAnchorTx, canonical)
      .then(o => { if (alive) setOutcome(o) })
      .catch(e => { if (alive) setLoadErr((e as Error).message) })
      .finally(() => { if (alive) setRunning(false) })
    return () => { alive = false }
  }, [bundle, canonical])

  const record = useMemo(() => {
    try {
      return JSON.parse(canonical) as Record<string, unknown>
    } catch {
      return null
    }
  }, [canonical])

  return (
    <div className="cr-public">
      <div className="cr-public__inner">
        <div className="cr-public__brand">
          <img src="/brand/onsumm-mark.svg" alt="" />
          Onsumm · Verify an audit proof
        </div>

        <Card>
          <CardHeader
            title="Check a record yourself"
            sub="Your browser recomputes every hash and reads the anchored root from Base Sepolia. Nothing here trusts Onsumm’s servers."
          />
          <CardBody>
            <div className="cr-stack">
              {/* Drag-and-drop is a pointer convenience only; the "Choose file" button
                  and the paste box below are the keyboard paths. */}
              {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions -- drop target only; file input and paste box are the accessible paths */}
              <div
                className={`cr-drop${over ? ' cr-drop--over' : ''}`}
                onDragOver={e => { e.preventDefault(); setOver(true) }}
                onDragLeave={() => setOver(false)}
                onDrop={e => {
                  e.preventDefault()
                  setOver(false)
                  const f = e.dataTransfer.files?.[0]
                  if (f) readFile(f)
                }}
              >
                <FileJson size={20} aria-hidden="true" />
                <div>Drop a proof bundle (.json) here, or</div>
                <label className="u-btn u-btn--secondary u-btn--sm" style={{ marginTop: 8, cursor: 'pointer' }}>
                  Choose file
                  <input type="file" accept="application/json,.json" style={{ display: 'none' }}
                    onChange={e => { const f = e.target.files?.[0]; if (f) readFile(f) }} />
                </label>
              </div>
              <label className="u-field">
                <span className="u-field__label">…or paste the bundle JSON</span>
                <textarea className="cr-textarea" value={pasted} onChange={e => setPasted(e.target.value)} placeholder='{"version":1,"canonical":"…"}' />
              </label>
              <div className="cr-row">
                <Button disabled={!pasted.trim()} onClick={() => {
                  try { accept(JSON.parse(pasted)) } catch { setLoadErr('That is not valid JSON.') }
                }}>
                  Verify pasted bundle
                </Button>
                {share && !bundle && !loadErr && <span className="cr-muted"><LoadingMark size={14} /> Opening shared proof…</span>}
              </div>
              {loadErr && <Note tone="danger">{loadErr}</Note>}
            </div>
          </CardBody>
        </Card>

        {bundle && (
          <>
            {running && !outcome && <LoadingMark size={22} />}
            {outcome && (
              <div className={`cr-verdict ${outcome.ok ? 'cr-verdict--ok' : 'cr-verdict--bad'}`} role="status">
                {outcome.ok ? <ShieldCheck size={26} aria-hidden="true" /> : <ShieldAlert size={26} aria-hidden="true" />}
                {outcome.ok ? 'VERIFIED — this record is unchanged since it was anchored' : 'TAMPERED or unproven — see which check failed'}
              </div>
            )}

            <Card>
              <CardHeader title="Checks" actions={
                <Checkbox label="Tamper one field (demo)" checked={tampered} onChange={e => setTampered(e.target.checked)} />
              } />
              <CardBody>
                {outcome && (
                  <ul className="cr-checks">
                    {outcome.checks.map(c => (
                      <li key={c.key}>
                        {c.ok ? <CheckCircle2 size={16} className="ok" aria-label="passed" /> : <XCircle size={16} className="bad" aria-label="failed" />}
                        <div>
                          <div><strong>{c.label}</strong></div>
                          <div className="cr-mono cr-muted">{c.detail}</div>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="The record" sub={tampered ? 'One character below has been changed for the demo.' : 'Exactly the text that was hashed.'} />
              <CardBody>
                <div className="cr-stack">
                  {record && (
                    <dl className="cr-kv">
                      {Object.entries(record).map(([k, v]) => (
                        <div key={k} style={{ display: 'contents' }}>
                          <dt>{k}</dt>
                          <dd className="cr-mono">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd>
                        </div>
                      ))}
                    </dl>
                  )}
                  {!record && <pre className="cr-mono" style={{ whiteSpace: 'pre-wrap' }}>{canonical}</pre>}
                  <dl className="cr-kv">
                    <dt>Company</dt><dd className="cr-mono">{bundle.tenant_id}</dd>
                    <dt>Row hash</dt><dd className="cr-mono">{bundle.row_hash}</dd>
                    <dt>Anchored root</dt><dd className="cr-mono">{bundle.top_root}</dd>
                    <dt>Anchor tx</dt><dd>{bundle.anchor?.tx_hash ? <TxLink hash={bundle.anchor.tx_hash} href={bundle.anchor.explorer} /> : 'Not anchored yet'}</dd>
                    {bundle.anchor?.anchored_at && (<><dt>Anchored at</dt><dd>{new Date(bundle.anchor.anchored_at).toLocaleString()}</dd></>)}
                    {outcome && <><dt>Recomputed root</dt><dd className="cr-mono" title={outcome.computed.topRoot}>{short(outcome.computed.topRoot)}</dd></>}
                  </dl>
                </div>
              </CardBody>
            </Card>
          </>
        )}
      </div>
    </div>
  )
}
