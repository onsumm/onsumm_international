import { useEffect, useState } from 'react'
import { Button, EmptyState, ErrorState, Input, PageHeader, SkeletonRows } from '../../ui'
import { fetchAudit, type AuditEntry } from '../../api/admin'
import { AuditTable } from './TenantDetailPage'

/** Every platform action, newest first. Keyset paging so it stays fast as it grows. */
export default function AuditLogPage() {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null)
  const [cursor, setCursor] = useState<number | null>(null)
  const [actor, setActor] = useState('')
  const [error, setError] = useState('')

  const load = (before?: number) => {
    fetchAudit({ actor: actor || undefined, before_id: before, limit: 100 })
      .then(d => {
        setEntries(prev => (before && prev ? [...prev, ...d.entries] : d.entries))
        setCursor(d.next_before_id)
      })
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load the audit log'))
  }

  useEffect(() => { setEntries(null); load() /* eslint-disable-next-line */ }, [actor])

  return (
    <>
      <PageHeader title="Audit log" sub="Every action taken from this panel." />
      <div style={{ maxWidth: 320, marginBottom: 'var(--sp-3)' }}>
        <Input label="Filter by ops user" placeholder="email contains…" value={actor}
               onChange={e => setActor(e.target.value)} />
      </div>

      {error && <ErrorState title="Could not load the audit log" message={error} />}
      {!entries && !error && <SkeletonRows rows={8} />}
      {entries?.length === 0 && <EmptyState title="Nothing recorded yet" message="Ops actions appear here as they happen." />}
      {entries && entries.length > 0 && (
        <>
          <AuditTable entries={entries} />
          {cursor && (
            <div style={{ marginTop: 'var(--sp-3)' }}>
              <Button variant="secondary" onClick={() => load(cursor)}>Load more</Button>
            </div>
          )}
        </>
      )}
    </>
  )
}
