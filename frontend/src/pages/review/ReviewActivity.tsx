/**
 * Activity: every change made from the Review Hub, newest first.
 *
 * Who did it, when, from which finding, what they did — and, for a vendor or a
 * bill, the before → after of each field that changed. Read from the audit log
 * the server keeps (backend_ocr/app/review/audit.py); nothing here is derived
 * on the client.
 */
import { useEffect, useState } from 'react'
import { ArrowRight } from 'lucide-react'
import { fetchAudit, type AuditEntry } from '../../api/review'
import { Button, LoadingMark, formatDateTime } from '../../ui'

const PAGE = 50
const UUID = '[0-9a-fA-F-]{36}'

/** What the request did, in words. */
export function describe(e: AuditEntry): string {
  const p = e.path
  const b = e.body as Record<string, unknown>
  const m = e.method
  if (new RegExp(`^/api/review/findings/${UUID}/status$`).test(p)) {
    const st = String(b.status ?? '').toLowerCase()
    return st === 'dismissed' ? `Dismissed${b.reason ? ` (${String(b.reason).replace(/_/g, ' ')})` : ''}`
      : st === 'snoozed' ? `Snoozed${b.snooze_days ? ` for ${b.snooze_days} days` : ''}`
      : st ? `Marked ${st}` : 'Changed status'
  }
  if (p === '/api/review/findings/bulk') {
    const n = Array.isArray(b.finding_ids) ? b.finding_ids.length : 0
    return `${String(b.status ?? 'Changed').toLowerCase()} ${n} item${n === 1 ? '' : 's'} at once`
  }
  if (new RegExp(`^/api/review/findings/${UUID}/undo$`).test(p)) return 'Undid the last decision'
  if (p === '/api/review/bill-coding/apply') {
    const n = Array.isArray(b.accept) ? b.accept.length : 0
    return `Applied ${n} suggested correction${n === 1 ? '' : 's'} to the bill`
  }
  if (p === '/api/review/act/approve-bill') return 'Approved the bill'
  if (p === '/api/review/act/notify') return 'Sent a notification'
  if (p === '/api/review/act/merge-misread-vendor') return 'Merged a misread vendor'
  if (p === '/api/review/rescan') return 'Asked for a re-scan'
  if (new RegExp(`^/api/vendors/${UUID}/names$`).test(p)) return 'Set the brand and legal names'
  if (new RegExp(`^/api/vendors/${UUID}/contacts`).test(p)) return m === 'DELETE' ? 'Removed a contact' : 'Added a contact'
  if (new RegExp(`^/api/vendors/${UUID}/bank`).test(p)) return m === 'DELETE' ? 'Removed a bank account' : 'Changed bank details'
  if (new RegExp(`^/api/vendors/${UUID}/(verify-bank|reverify-bank)`).test(p)) return 'Ran a bank verification'
  if (new RegExp(`^/api/vendors/${UUID}/gstins`).test(p)) return m === 'DELETE' ? 'Removed a GSTIN' : 'Changed GSTINs'
  if (new RegExp(`^/api/vendors/${UUID}/documents/.+/discard$`).test(p)) return 'Kept what is on file (discarded the contract’s value)'
  if (new RegExp(`^/api/vendors/${UUID}$`).test(p) && m === 'PATCH') return 'Edited the vendor'
  if (new RegExp(`^/api/bills/${UUID}/action$`).test(p)) {
    const a = String(b.action ?? '')
    return a === 'approve' ? 'Approved the bill' : a === 'send_back' ? 'Sent the bill back' : a === 'submit' ? 'Submitted the bill' : 'Acted on the bill'
  }
  if (new RegExp(`^/api/bills/${UUID}`).test(p)) return m === 'PUT' || m === 'PATCH' ? 'Edited the bill' : 'Changed the bill'
  return `${m} ${p}`
}

/** A field name as a person would say it. */
function fieldLabel(k: string): string {
  if (k === 'payment_terms_days') return 'Payment terms'
  return k.replace(/_id$/, '').replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())
}

function show(v: unknown, k?: string): string {
  if (v === null || v === undefined || v === '') return 'not set'
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (k === 'payment_terms_days') {
    const n = Number(v)
    return n === 0 ? 'due on receipt' : `${n} day${n === 1 ? '' : 's'}`
  }
  return String(v)
}

/** Terms first: it is what the user decided; the due date follows from it. */
function ordered(changes: Record<string, [unknown, unknown]>) {
  return Object.entries(changes || {}).sort(([a], [b]) =>
    (a === 'payment_terms_days' ? -1 : 0) - (b === 'payment_terms_days' ? -1 : 0))
}

export function ReviewActivity() {
  const [items, setItems] = useState<AuditEntry[] | null>(null)
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    setItems(null); setError('')
    fetchAudit({ limit: PAGE, offset })
      .then(r => { if (live) { setItems(r.items); setTotal(r.total) } })
      .catch(e => live && setError(e instanceof Error ? e.message : 'Could not load the activity'))
    return () => { live = false }
  }, [offset])

  if (error) return <p className="rh__row-error" role="alert">{error}</p>
  if (!items) return <div className="rh__act-loading"><LoadingMark size={22} /></div>
  if (items.length === 0) {
    return <p className="rh__act-empty">No changes made from Review yet. Everything done here will be listed, with who did it and what changed.</p>
  }

  return (
    <div className="rh__act">
      <table className="rh__act-table">
        <thead>
          <tr>
            <th>When</th>
            <th>Who</th>
            <th>What</th>
            <th>On</th>
          </tr>
        </thead>
        <tbody>
          {items.map(e => {
            const failed = e.status_code != null && e.status_code >= 400
            const changes = ordered(e.changes)
            return (
              <tr key={e.audit_id} className={failed ? 'rh__act-row--failed' : undefined}>
                <td className="rh__act-when">{formatDateTime(e.at)}</td>
                <td>
                  <span className="rh__act-who">{e.actor || 'Unknown'}</span>
                  {e.actor_role && <span className="rh__act-role">{e.actor_role.replace(/_/g, ' ')}</span>}
                </td>
                <td>
                  <span className="rh__act-what">{describe(e)}</span>
                  {failed && <span className="rh__act-failed">Failed ({e.status_code}) — nothing changed</span>}
                  {changes.length > 0 && (
                    <ul className="rh__act-changes">
                      {changes.map(([k, [before, after]]) => (
                        <li key={k}>
                          <span className="rh__act-field">{fieldLabel(k)}</span>
                          <span className="rh__act-before">{show(before, k)}</span>
                          <ArrowRight size={11} aria-hidden="true" />
                          <span className="rh__act-after">{show(after, k)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
                <td>
                  {e.entity_label && <span className="rh__act-entity">{e.entity_label}</span>}
                  {e.finding_title && <span className="rh__act-finding">{e.finding_title}</span>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {total > PAGE && (
        <div className="rh__act-pager">
          <span className="tnum">{offset + 1}–{offset + items.length} of {total}</span>
          <Button size="sm" variant="ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(offset - PAGE, 0))}>Newer</Button>
          <Button size="sm" variant="ghost" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Older</Button>
        </div>
      )}
    </div>
  )
}
