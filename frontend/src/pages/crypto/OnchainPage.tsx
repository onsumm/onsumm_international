/**
 * On-chain ledger (05_USER_FLOWS.md, flow 6): what the chain watcher has seen
 * on the company's treasury and tax wallets, how each transfer matched (payout,
 * tax, funding, receivable — or not at all), the daily wallet statement against
 * the balance read from the chain, and the vouchers posted to the books.
 *
 * An outgoing transfer Onsumm did not propose shows as UNMATCHED: money left the
 * treasury outside the approval flow, which is exactly what this page exists to
 * surface.
 */

import { useCallback, useEffect, useState } from 'react'
import { Download, RefreshCw } from 'lucide-react'
import {
  apiError, fetchOnchainStatus, fetchOnchainTransfers, fetchPostings, fetchStatement, fetchVoucherXml, rescanOnchain,
  type ChainTransfer, type DailyStatement, type LedgerPosting, type OnchainStatus,
} from '../../api/crypto'
import { Badge, Button, Card, CardBody, CardHeader, EmptyState, ErrorState, Input, PageHeader, Skeleton, formatDateTime, useToast } from '../../ui'
import { AddressLink, Note, StatusBadge, TxLink } from '../../components/crypto/common'
import { downloadText } from '../../components/crypto/helpers'
import { explorerAddress, formatUsdc } from '../../web3/units'
import './crypto.css'

type Tab = 'transfers' | 'statement' | 'postings'

function TransfersTable({ rows }: { rows: ChainTransfer[] }) {
  if (!rows.length) return <EmptyState title="No transfers seen yet" message="Transfers in and out of the watched wallets appear here within seconds of being mined." />
  return (
    <div className="cr-table-wrap">
      <table className="cr-table">
        <thead>
          <tr>
            <th>Block</th><th>Direction</th><th className="num">Amount</th><th>From</th><th>To</th>
            <th>Match</th><th>Status</th><th className="num">Conf.</th><th>Tx</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(t => (
            <tr key={`${t.tx_hash}-${t.log_index}`} style={t.removed ? { opacity: 0.5, textDecoration: 'line-through' } : undefined}>
              <td>{t.block_number}</td>
              <td><Badge tone={t.direction === 'OUT' ? 'info' : 'neutral'}>{t.direction === 'OUT' ? 'Out' : 'In'}</Badge></td>
              <td className="num">{formatUsdc(t.amount_units)}</td>
              <td><AddressLink address={t.from_address} href={explorerAddress(t.from_address)} /></td>
              <td><AddressLink address={t.to_address} href={explorerAddress(t.to_address)} /></td>
              <td>{t.match_type ? <Badge tone={t.match_type === 'UNKNOWN' ? 'danger' : 'neutral'}>{t.match_type}</Badge> : '—'}</td>
              <td><StatusBadge status={t.removed ? 'REVOKED' : t.status} title={t.removed ? 'Removed by a chain reorganisation' : undefined} /></td>
              <td className="num">{t.confirmations}</td>
              <td><TxLink hash={t.tx_hash} href={t.explorer} simulated={t.simulated} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function StatementView() {
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [st, setSt] = useState<DailyStatement | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    fetchStatement(date)
      .then(s => { setSt(s); setErr('') })
      .catch(e => setErr(apiError(e).message))
      .finally(() => setLoading(false))
  }, [date])
  useEffect(load, [load])

  return (
    <div className="cr-stack">
      <div className="cr-row" style={{ alignItems: 'end' }}>
        <div style={{ width: 200 }}><Input label="Day (UTC)" type="date" value={date} onChange={e => setDate(e.target.value)} /></div>
        <Button iconLeft={<RefreshCw size={13} />} loading={loading} onClick={load}>Load</Button>
      </div>
      {err && <ErrorState message={err} onRetry={load} />}
      {st && (
        <>
          <div className="cr-table-wrap">
            <table className="cr-table">
              <thead>
                <tr>
                  <th>Wallet</th><th>Role</th><th className="num">Opening</th><th className="num">In</th><th className="num">Out</th>
                  <th className="num">Closing (ledger)</th><th className="num">Closing (chain)</th><th>Reconciled</th>
                </tr>
              </thead>
              <tbody>
                {st.wallets.map(w => (
                  <tr key={w.address}>
                    <td><AddressLink address={w.address} href={explorerAddress(w.address)} /></td>
                    <td>{w.role}</td>
                    <td className="num">{formatUsdc(w.opening_units)}</td>
                    <td className="num">{formatUsdc(w.inflow_units)}</td>
                    <td className="num">{formatUsdc(w.outflow_units)}</td>
                    <td className="num">{formatUsdc(w.closing_units_ledger)}</td>
                    <td className="num">{w.closing_units_chain != null ? formatUsdc(w.closing_units_chain) : '—'}</td>
                    <td>{w.reconciled ? <StatusBadge status="MATCHED" /> : <StatusBadge status="MISMATCH" title="Ledger and chain balances differ" />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3 className="cr-section-title">Lines</h3>
          <TransfersTable rows={st.lines} />
        </>
      )}
    </div>
  )
}

function PostingsView() {
  const toast = useToast()
  const [rows, setRows] = useState<LedgerPosting[] | null>(null)
  const [err, setErr] = useState('')
  const load = useCallback(() => {
    fetchPostings().then(r => { setRows(r); setErr('') }).catch(e => setErr(apiError(e).message))
  }, [])
  useEffect(load, [load])

  const download = async (p: LedgerPosting) => {
    try {
      downloadText(`tally-voucher-${p.source_id.slice(0, 8)}.xml`, await fetchVoucherXml(p.id), 'application/xml')
    } catch (e) {
      toast.error('Could not download the voucher', apiError(e).message)
    }
  }

  if (err) return <ErrorState message={err} onRetry={load} />
  if (!rows) return <Skeleton height={80} />
  if (!rows.length) return <EmptyState title="No vouchers yet" message="A Tally payment voucher is generated for every confirmed USDC payout." />
  return (
    <div className="cr-table-wrap">
      <table className="cr-table">
        <thead><tr><th>Created</th><th>Source</th><th>Target</th><th>Reference</th><th>Status</th><th aria-label="Actions" /></tr></thead>
        <tbody>
          {rows.map(p => (
            <tr key={p.id}>
              <td>{formatDateTime(p.created_at)}</td>
              <td>{p.source_type} <span className="cr-mono">{p.source_id.slice(0, 8)}</span></td>
              <td>{p.target_system}</td>
              <td className="cr-mono">{p.external_ref ?? '—'}</td>
              <td><StatusBadge status={p.status} title={p.error ?? undefined} /></td>
              <td style={{ textAlign: 'right' }}>
                <Button size="sm" variant="ghost" iconLeft={<Download size={13} />} onClick={() => void download(p)}>Voucher XML</Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function OnchainPage() {
  const toast = useToast()
  const [tab, setTab] = useState<Tab>('transfers')
  const [status, setStatus] = useState<OnchainStatus | null>(null)
  const [rows, setRows] = useState<ChainTransfer[] | null>(null)
  const [err, setErr] = useState('')
  const [rescanning, setRescanning] = useState(false)

  const load = useCallback(() => {
    Promise.all([fetchOnchainStatus(), fetchOnchainTransfers(100)])
      .then(([s, t]) => { setStatus(s); setRows(t); setErr('') })
      .catch(e => setErr(apiError(e).message))
  }, [])
  useEffect(() => {
    load()
    const t = setInterval(load, 10_000)
    return () => clearInterval(t)
  }, [load])

  const rescan = async () => {
    setRescanning(true)
    try {
      const r = await rescanOnchain()
      toast.success('Rescanned', `${r.processed} transfer(s) processed; cursor at block ${r.cursor_block}.`)
      load()
    } catch (e) {
      toast.error('Rescan failed', apiError(e).message)
    } finally {
      setRescanning(false)
    }
  }

  const unmatchedOut = rows?.filter(t => t.direction === 'OUT' && t.status === 'UNMATCHED' && !t.removed) ?? []

  return (
    <div className="cr">
      <PageHeader
        title="On-chain ledger"
        sub="Every USDC movement on the company’s treasury and tax wallets, matched to bills and posted to the books."
        actions={<Button iconLeft={<RefreshCw size={14} />} loading={rescanning} onClick={() => void rescan()}>Rescan now</Button>}
      />

      {err && <ErrorState title="Could not load the on-chain ledger" message={err} onRetry={load} />}

      {status && (
        <Card>
          <CardHeader title="Chain watcher" actions={status.configured ? <Badge tone="success">Running</Badge> : <Badge tone="danger">Not configured</Badge>} />
          <CardBody>
            {!status.configured && <Note tone="warn">No RPC endpoint is configured on the server, so nothing is being watched.</Note>}
            <dl className="cr-kv">
              <dt>Network</dt><dd>{status.chain} (chain ID {status.chain_id})</dd>
              <dt>Chain head</dt><dd>{status.head_block ?? '—'}</dd>
              <dt>Processed to</dt><dd>{status.cursor_block ?? '—'}{status.lag_blocks != null && ` · ${status.lag_blocks} block(s) behind`}</dd>
              <dt>Watching</dt>
              <dd className="cr-stack" style={{ gap: 2 }}>
                {status.watched_addresses.length ? status.watched_addresses.map(a => <AddressLink key={a} address={a} href={explorerAddress(a)} />) : '—'}
              </dd>
            </dl>
          </CardBody>
        </Card>
      )}

      {unmatchedOut.length > 0 && (
        <Note tone="danger">
          <strong>{unmatchedOut.length} outgoing transfer(s) were not proposed by Onsumm.</strong> Money left the treasury
          outside the approval flow — check them in the Review Hub (<span className="cr-mono">UNMATCHED_OUTBOUND_TRANSFER</span>).
        </Note>
      )}

      <div className="cr-tabs" role="tablist" aria-label="On-chain views">
        {(['transfers', 'statement', 'postings'] as Tab[]).map(t => (
          <button key={t} type="button" role="tab" className="cr-tab" aria-selected={tab === t} onClick={() => setTab(t)}>
            {t === 'transfers' ? 'Transfers' : t === 'statement' ? 'Daily statement' : 'Vouchers'}
          </button>
        ))}
      </div>

      <Card>
        <CardBody>
          {tab === 'transfers' && (rows ? <TransfersTable rows={rows} /> : !err && <Skeleton height={120} />)}
          {tab === 'statement' && <StatementView />}
          {tab === 'postings' && <PostingsView />}
        </CardBody>
      </Card>
    </div>
  )
}
