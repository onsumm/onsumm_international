/**
 * "Run crypto checks" on a bill (demo steps 3 and 4): compares any wallet
 * printed on the invoice with the vendor's verified wallet (WALLET_MISMATCH)
 * and the invoice lines with the agreed contract rates
 * (OVERBILLED_VS_CONTRACT). Findings are also filed in the Review Hub.
 */

import { useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import { apiError, codeCopy, runCryptoChecks, type CryptoFinding } from '../../api/crypto'
import { Badge, Button } from '../../ui'
import { Note } from './common'
import '../../pages/crypto/crypto.css'

function detailText(d: CryptoFinding['details']): string {
  if (!d) return ''
  if (typeof d === 'string') return d
  return Object.entries(d)
    .filter(([, v]) => v !== null && v !== undefined && typeof v !== 'object')
    .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${String(v)}`)
    .join(' · ')
}

export default function BillCryptoChecks({ invoiceId }: { invoiceId: string }) {
  const [findings, setFindings] = useState<CryptoFinding[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const run = async () => {
    setBusy(true)
    setErr('')
    try {
      setFindings((await runCryptoChecks(invoiceId)).findings)
    } catch (e) {
      setErr(apiError(e).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ border: '1px solid var(--line-200)', borderRadius: 10, padding: '12px 16px', display: 'grid', gap: 8 }}>
      <div className="cr-row cr-row--between">
        <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--ink-600)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Crypto checks
        </span>
        <Button size="sm" iconLeft={<ShieldCheck size={13} />} loading={busy} onClick={() => void run()}>
          {findings ? 'Run again' : 'Run crypto checks'}
        </Button>
      </div>
      {err && <Note tone="danger">{err}</Note>}
      {findings && findings.length === 0 && <Note tone="ok">No wallet mismatch and no charge above the contract rate.</Note>}
      {findings?.map((f, i) => (
        <div key={`${f.code}-${i}`} className={`cr-note${f.severity === 'critical' || f.severity === 'high' ? ' cr-note--danger' : ' cr-note--warn'}`}>
          <div className="cr-stack" style={{ gap: 4 }}>
            <div className="cr-row">
              <Badge tone={f.severity === 'critical' || f.severity === 'high' ? 'danger' : 'warn'}>{f.code}</Badge>
              <strong>{f.title}</strong>
            </div>
            <span>{detailText(f.details) || codeCopy(f.code)}</span>
          </div>
        </div>
      ))}
    </div>
  )
}
