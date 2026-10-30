/**
 * Small building blocks shared by every crypto rail screen: status badges with
 * a glyph (so state never rests on colour alone), a live countdown for cooling
 * periods, the list of policy refusals, and copy-to-clipboard.
 */

import { useState } from 'react'
import { AlertTriangle, Check, Clock, Copy, ExternalLink, Lock, ShieldAlert, XCircle } from 'lucide-react'
import { Badge, Button, type BadgeTone } from '../../ui'
import { codeCopy } from '../../api/crypto'
import { shortAddress } from '../../web3/units'
import { formatRemaining, humanStatus, useNow } from './helpers'

const STATUS_TONE: Record<string, BadgeTone> = {
  ACTIVE: 'success',
  CLEAR: 'success',
  APPROVED: 'success',
  CONFIRMED: 'success',
  SUCCESS: 'success',
  MATCHED: 'success',
  POSTED: 'success',
  ANCHORED: 'success',
  COOLING: 'warn',
  PENDING_PROOF: 'warn',
  PENDING: 'warn',
  SUBMITTED: 'info',
  NOT_STARTED: 'neutral',
  UNMATCHED: 'warn',
  FROZEN: 'danger',
  REVOKED: 'neutral',
  HIT: 'danger',
  ERROR: 'danger',
  REJECTED: 'danger',
  FAILED: 'danger',
  MISMATCH: 'danger',
}

const STATUS_ICON: Record<BadgeTone, JSX.Element | undefined> = {
  success: <Check size={11} />,
  warn: <Clock size={11} />,
  danger: <XCircle size={11} />,
  info: <Clock size={11} />,
  neutral: undefined,
  brand: undefined,
}

export function StatusBadge({ status, title }: { status: string | null | undefined; title?: string }) {
  const tone = STATUS_TONE[status ?? ''] ?? 'neutral'
  return (
    <Badge tone={tone} icon={STATUS_ICON[tone]} title={title}>
      {humanStatus(status)}
    </Badge>
  )
}

export function Countdown({ until, doneLabel = 'Cooling period over' }: { until: string | null | undefined; doneLabel?: string }) {
  const now = useNow()
  if (!until) return null
  const left = new Date(until).getTime() - now
  if (left <= 0) return <span className="cr-muted">{doneLabel}</span>
  return (
    <span className="cr-muted" aria-live="off">
      <Clock size={12} style={{ verticalAlign: '-2px' }} aria-hidden="true" /> {formatRemaining(left)} left
    </span>
  )
}

/** Every refusal at once, each with what it means — never just "403". */
export function RefusalList({ codes, messages, title = 'This payout cannot go ahead' }: { codes: string[]; messages?: string[]; title?: string }) {
  if (!codes.length && !messages?.length) return null
  return (
    <div className="cr-note cr-note--danger" role="alert">
      <ShieldAlert size={14} aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
          {codes.map((c, i) => (
            <li key={c}>
              <span className="cr-mono">{c}</span> — {messages?.[i] || codeCopy(c)}
            </li>
          ))}
          {!codes.length && messages?.map(m => <li key={m}>{m}</li>)}
        </ul>
      </div>
    </div>
  )
}

export function Note({ tone = 'info', children }: { tone?: 'info' | 'warn' | 'danger' | 'ok'; children: React.ReactNode }) {
  const icon = tone === 'danger' ? <Lock size={14} /> : tone === 'warn' ? <AlertTriangle size={14} /> : tone === 'ok' ? <Check size={14} /> : <AlertTriangle size={14} />
  return (
    <div className={`cr-note${tone === 'info' ? '' : ` cr-note--${tone}`}`} role={tone === 'danger' ? 'alert' : undefined}>
      <span aria-hidden="true" style={{ display: 'flex' }}>{icon}</span>
      <div>{children}</div>
    </div>
  )
}

export function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  return (
    <Button
      size="sm"
      variant="ghost"
      iconLeft={done ? <Check size={13} /> : <Copy size={13} />}
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setDone(true)
          setTimeout(() => setDone(false), 1500)
        })
      }}
    >
      {done ? 'Copied' : label}
    </Button>
  )
}

export function AddressLink({ address, href }: { address: string | null | undefined; href?: string | null }) {
  if (!address) return <span>—</span>
  return (
    <span className="cr-row" style={{ gap: 4 }}>
      <span className="cr-mono" title={address}>{shortAddress(address)}</span>
      {href && (
        <a href={href} target="_blank" rel="noreferrer" aria-label="Open in block explorer" title="Open in block explorer">
          <ExternalLink size={12} />
        </a>
      )}
    </span>
  )
}

export function TxLink({ hash, href, simulated }: { hash: string | null | undefined; href?: string | null; simulated?: boolean }) {
  if (!hash) return <span>—</span>
  // A demo settlement was validated against the Safe but never broadcast: there is
  // nothing to open on the explorer, so show the id and say so.
  if (simulated) {
    return (
      <span className="cr-mono" title={`${hash} — demo settlement, not broadcast`}>
        {shortAddress(hash)} <span className="cr-muted">(simulated)</span>
      </span>
    )
  }
  const url = href || `https://sepolia.basescan.org/tx/${hash}`
  return (
    <a className="cr-mono" href={url} target="_blank" rel="noreferrer" title={hash}>
      {shortAddress(hash)} <ExternalLink size={11} style={{ verticalAlign: '-1px' }} aria-hidden="true" />
    </a>
  )
}
