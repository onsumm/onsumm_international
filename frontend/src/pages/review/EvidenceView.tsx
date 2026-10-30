/**
 * Renders a finding's evidence by its declared *shape*, never by its problem type.
 *
 * This component is the whole open-ended-detection bet. Detection is agentic, so
 * a finding type that did not exist when this file was written has to render
 * well today. It therefore dispatches on `evidence.kind` — a small, closed set
 * of shapes any finding can pick — and falls back to RawEvidence, which is
 * written to never crash, never render blank, and never print [object Object].
 *
 * There is no switch on finding_type anywhere in this file, and there must not be.
 */
import { Component, type ReactNode } from 'react'
import { CheckCircle2, XCircle } from 'lucide-react'

import type { Finding } from '../../api/review'
import { formatDate, formatMoney, humanise } from '../../ui'
import { ProposalsPanel, type Proposal } from './ProposalsPanel'

interface Props {
  evidence: Record<string, unknown>
  /** Only the `proposals` kind uses these: it is the one evidence shape that
   *  is itself the decision, so it needs the finding it belongs to and a way
   *  to say the record changed. Everything else renders read-only. */
  finding?: Finding
  onApplied?: () => void
}

/** Evidence is arbitrary by design, so values stay `unknown` and every reader
 *  narrows them. That is the point: nothing here may assume a shape. */
type Loose = Record<string, unknown>

function asRecord(v: unknown): Loose {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Loose) : {}
}

function asArray(v: unknown): unknown[] {
  return Array.isArray(v) ? v : []
}

const MONEYISH =
  /amount|total|value|paid|exposure|variance|cost|price|outstanding|balance|payable|\btds\b|\bnet\b|\bfee(s)?\b/i
const ISO_DATE = /^\d{4}-\d{2}-\d{2}([T ]|$)/

/** Best-effort scalar rendering, used by every shape below. */
function scalar(value: unknown, key?: string): string {
  if (value == null || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  if (typeof value === 'number') {
    return key && MONEYISH.test(key) ? formatMoney(value) : String(value)
  }
  if (typeof value === 'string') {
    if (ISO_DATE.test(value)) {
      const d = formatDate(value)
      if (d && d !== '—') return d
    }
    if (key && MONEYISH.test(key) && value !== '' && !Number.isNaN(Number(value))) {
      return formatMoney(Number(value))
    }
    return value
  }
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

/** Two labels that say the same thing, give or take case and spacing. */
function sameText(a: unknown, b: unknown): boolean {
  return typeof a === 'string' && typeof b === 'string'
    && a.trim().toLowerCase() === b.trim().toLowerCase()
}

/** GSTINs, account numbers and codes read far better monospaced. */
function looksLikeCode(v: string): boolean {
  return /^[A-Z0-9]{8,20}$/.test(v.replace(/\s/g, ''))
}

function Value({ value, k }: { value: unknown; k?: string }) {
  const text = scalar(value, k)
  return typeof value === 'string' && looksLikeCode(value)
    ? <span className="rh__mono">{text}</span>
    : <>{text}</>
}

// ── Shapes ────────────────────────────────────────────────────────────────────

/**
 * Two codes, compared character by character.
 *
 * For a misread identifier the useful thing is not a sentence saying four
 * characters differ — it is *seeing* which four. The differing positions are
 * computed here from the two values, so the highlighting can never drift out of
 * sync with what it is highlighting.
 *
 * Red on what was read, green on what is correct. Colour is never the only
 * signal: the differing characters are also underlined and bolded, each side
 * carries an icon-and-word badge, and the count is stated in text — so this
 * survives greyscale and colour-blindness.
 */
function CodeDiff({ e }: { e: Loose }) {
  const left = asRecord(e.left)
  const right = asRecord(e.right)
  const a = String(left.value ?? '')
  const b = String(right.value ?? '')
  const comparable = a.length === b.length && a.length > 0
  const differs = (i: number) => comparable && a[i] !== b[i]
  const count = comparable ? [...a].filter((_, i) => differs(i)).length : 0
  const facts = asArray(e.facts).map(asRecord)

  const side = (value: string, rec: Loose, tone: 'bad' | 'good') => (
    <div className={`rh__cd-side rh__cd-side--${tone}`}>
      <div className="rh__cd-label">{scalar(rec.label)}</div>
      <div className={`rh__cd-value rh__cd-value--${tone}`}>
        {[...value].map((ch, i) => (
          <span key={i} className={differs(i) ? `rh__cd-ch rh__cd-ch--${tone}` : undefined}>
            {ch}
          </span>
        ))}
      </div>
      {rec.status === 'verified' && (
        <span className="rh__cd-badge rh__cd-badge--good">
          <CheckCircle2 size={13} aria-hidden="true" /> Verified
        </span>
      )}
      {rec.status === 'failed' && (
        <span className="rh__cd-badge rh__cd-badge--bad">
          <XCircle size={13} aria-hidden="true" /> Failed verification
        </span>
      )}
    </div>
  )

  return (
    <div className="rh__cd">
      <div className="rh__cd-pair">
        {side(a, left, 'bad')}
        {side(b, right, 'good')}
      </div>

      {count > 0 && (
        <p className="rh__cd-count">
          <strong>{count}</strong> of {a.length} characters differ
        </p>
      )}

      {facts.length > 0 && (
        <dl className="rh__cd-facts">
          {facts.map((f, i) => (
            <div key={i} style={{ display: 'contents' }}>
              <dt>{scalar(f.label)}</dt>
              <dd>{scalar(f.left)}</dd>
              <dd>{scalar(f.right)}</dd>
            </div>
          ))}
        </dl>
      )}

      {e.note ? <p className="rh__note">{scalar(e.note)}</p> : null}
    </div>
  )
}

function DiffEvidence({ e }: { e: Loose }) {
  // Three columns, with the label LEADING its pair of values. The label used to
  // trail on a full-width row underneath, which read as a caption for the row
  // below it — so "GSTIN" appeared to label the character positions.
  const rows = asArray(e.rows).map(asRecord)
  return (
    <div className="rh__diff">
      <div className="rh__diff-h" />
      <div className="rh__diff-h">{scalar(e.left_label) || 'Before'}</div>
      <div className="rh__diff-h">{scalar(e.right_label) || 'After'}</div>
      {rows.map((r, i) => (
        <div key={i} style={{ display: 'contents' }}>
          <div className="rh__diff-l">{scalar(r.label)}</div>
          <div className="rh__diff-c"><Value value={r.left} /></div>
          <div className="rh__diff-c"><Value value={r.right} /></div>
        </div>
      ))}
    </div>
  )
}

function TableEvidence({ e }: { e: Loose }) {
  const cols = asArray(e.columns).map(c => scalar(c))
  const rows = asArray(e.rows)
  if (!rows.length) return <p className="rh__note">Nothing to compare.</p>
  return (
    <>
      <div className="rh__tablewrap">
        <table className="rh__table">
          {cols.length > 0 && (
            <thead>
              <tr>{cols.map((c, i) => <th key={i} scope="col">{c}</th>)}</tr>
            </thead>
          )}
          <tbody>
            {rows.slice(0, 25).map((r, i) => (
              <tr key={i}>
                {(Array.isArray(r) ? r : [r]).map((cell, j) => (
                  <td key={j}><Value value={cell} k={cols[j]} /></td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > 25 && <p className="rh__note">…and {rows.length - 25} more rows.</p>}
      {e.note ? <p className="rh__note">{scalar(e.note)}</p> : null}
    </>
  )
}

const STEP_MARK: Record<string, string> = {
  done: '✓', current: '▸', blocked: '✕', pending: '·',
}

function TimelineEvidence({ e }: { e: Loose }) {
  const steps = asArray(e.steps).map(asRecord)
  return (
    <>
      <ol className="rh__steps">
        {steps.map((s, i) => {
          const state = typeof s.state === 'string' ? s.state : 'pending'
          return (
            <li key={i} className={`rh__step rh__step--${state}`}>
              <span className="rh__step-mark" aria-hidden="true">{STEP_MARK[state] ?? '·'}</span>
              <span className="rh__step-label">{scalar(s.label)}</span>
              {s.actor && !sameText(s.actor, s.label)
                ? <span className="rh__step-actor">{scalar(s.actor)}</span>
                : null}
              {/* An audit trail is the same shape as a plan plus two facts:
                  when it happened and what the person said. Optional, so a
                  timeline that has neither renders exactly as it did before. */}
              {s.at ? <span className="rh__step-at">{scalar(s.at)}</span> : null}
              {s.note ? <span className="rh__step-note">{scalar(s.note)}</span> : null}
              <span className="sr-only">{state}</span>
            </li>
          )
        })}
      </ol>
      {e.note ? <p className="rh__note">{scalar(e.note)}</p> : null}
    </>
  )
}

function FieldsEvidence({ e }: { e: Loose }) {
  const fields = asArray(e.fields).map(asRecord)
  return (
    <>
      <dl className="rh__fields">
        {fields.map((f, i) => (
          <div key={i} style={{ display: 'contents' }}>
            <dt>{scalar(f.label)}</dt>
            <dd><Value value={f.value} k={String(f.label ?? '')} /></dd>
          </div>
        ))}
      </dl>
      {e.note ? <p className="rh__note">{scalar(e.note)}</p> : null}
    </>
  )
}

function TextEvidence({ e }: { e: Loose }) {
  const bullets = asArray(e.bullets)
  return (
    <>
      {e.body ? <p className="rh__prose">{scalar(e.body)}</p> : null}
      {bullets.length > 0 && (
        <ul style={{ margin: 'var(--sp-2) 0 0', paddingLeft: 'var(--sp-5)' }}>
          {bullets.map((b, i) => (
            <li key={i} style={{ fontSize: 'var(--fs-sm)', color: 'var(--ink-700)' }}>
              {scalar(b)}
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

/**
 * The safety net. Given arbitrary JSON it produces something a human can read:
 * one level flattened into a field list, keys humanised, money and dates
 * formatted, arrays listed, and anything deeper folded into a <details>.
 *
 * A finding invented at runtime lands here, so this is the component to keep
 * hostile-payload tests pointed at.
 */
export function RawEvidence({ data }: { data: unknown }) {
  if (data == null) return <p className="rh__note">No further detail was recorded.</p>

  if (typeof data !== 'object') {
    return <p className="rh__prose">{scalar(data)}</p>
  }

  if (Array.isArray(data)) {
    if (data.length === 0) return <p className="rh__note">No further detail was recorded.</p>
    return (
      <ul style={{ margin: 0, paddingLeft: 'var(--sp-5)' }}>
        {data.slice(0, 20).map((v, i) => (
          <li key={i} style={{ fontSize: 'var(--fs-sm)', color: 'var(--ink-700)' }}>
            {typeof v === 'object' && v !== null ? <RawEvidence data={v} /> : scalar(v)}
          </li>
        ))}
      </ul>
    )
  }

  const entries = Object.entries(data as Record<string, unknown>).filter(([k]) => k !== 'kind')
  if (entries.length === 0) return <p className="rh__note">No further detail was recorded.</p>

  const flat = entries.filter(([, v]) => typeof v !== 'object' || v === null)
  const nested = entries.filter(([, v]) => typeof v === 'object' && v !== null)

  return (
    <>
      {flat.length > 0 && (
        <dl className="rh__fields">
          {flat.map(([k, v]) => (
            <div key={k} style={{ display: 'contents' }}>
              <dt>{humanise(k)}</dt>
              <dd><Value value={v} k={k} /></dd>
            </div>
          ))}
        </dl>
      )}
      {nested.map(([k, v]) => (
        <details key={k} style={{ marginTop: 'var(--sp-3)' }}>
          <summary style={{ fontSize: 'var(--fs-sm)', color: 'var(--ink-600)', cursor: 'pointer' }}>
            {humanise(k)}
          </summary>
          <div style={{ marginTop: 'var(--sp-2)' }}><RawEvidence data={v} /></div>
        </details>
      ))}
    </>
  )
}

/**
 * A malformed payload from a novel detector must degrade one panel, not take
 * down the drawer around it.
 */
class EvidenceBoundary extends Component<
  { children: ReactNode; fallback: ReactNode },
  { failed: boolean }
> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}

export function EvidenceView({ evidence, finding, onApplied }: Props) {
  if (!evidence || typeof evidence !== 'object' || Object.keys(evidence).length === 0) {
    return null
  }

  const kind = typeof evidence.kind === 'string' ? evidence.kind : null
  const e: Loose = evidence

  let body: ReactNode
  switch (kind) {
    case 'diff':      body = <DiffEvidence e={e} />; break
    case 'code_diff': body = <CodeDiff e={e} />; break
    case 'table':    body = <TableEvidence e={e} />; break
    case 'timeline': body = <TimelineEvidence e={e} />; break
    case 'fields':   body = <FieldsEvidence e={e} />; break
    case 'text':     body = <TextEvidence e={e} />; break
    case 'proposals':
      // Without a finding there is nowhere to send an acceptance, so it falls
      // back to the table renderer rather than offering a button that cannot
      // do anything.
      body = finding
        ? <ProposalsPanel
            proposals={(Array.isArray(e.proposals) ? e.proposals : []) as Proposal[]}
            finding={finding}
            onApplied={onApplied}
          />
        : <RawEvidence data={evidence} />
      break
    default:         body = <RawEvidence data={evidence} />
  }

  return (
    <EvidenceBoundary fallback={<RawEvidence data={evidence} />}>
      {body}
    </EvidenceBoundary>
  )
}
