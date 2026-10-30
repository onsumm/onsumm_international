/**
 * Audit anchor (05_USER_FLOWS.md, flow 8; README demo step 8).
 *
 * The audit log is hash-chained by the database, so editing or deleting any row
 * breaks every hash after it. Each anchor commits one Merkle root over all
 * unanchored rows to Base Sepolia. From here a controller checks the chain,
 * anchors on demand, and hands an auditor a proof bundle or a share link that
 * /verify checks in the browser without a login.
 */

import { useCallback, useEffect, useState } from 'react'
import { Anchor as AnchorIcon, Download, Link2, ShieldCheck } from 'lucide-react'
import {
  anchorNow, fetchAnchors, fetchAuditLog, fetchProof, verifyAuditChain,
  type Anchor, type AuditRow, type ChainCheck,
} from '../../api/audit'
import { apiError } from '../../api/crypto'
import { Badge, Button, Card, CardBody, CardHeader, EmptyState, ErrorState, PageHeader, Skeleton, formatDateTime, useToast } from '../../ui'
import { CopyButton, Note, StatusBadge, TxLink } from '../../components/crypto/common'
import { downloadText } from '../../components/crypto/helpers'
import { short } from '../../web3/merkle'
import { VERIFY_PATH } from '../../routes/paths'
import './crypto.css'

export default function AuditPage() {
  const toast = useToast()
  const [rows, setRows] = useState<AuditRow[] | null>(null)
  const [anchors, setAnchors] = useState<Anchor[] | null>(null)
  const [chain, setChain] = useState<ChainCheck | null>(null)
  const [err, setErr] = useState('')
  const [anchoring, setAnchoring] = useState(false)
  const [checking, setChecking] = useState(false)
  const [proofBusy, setProofBusy] = useState('')
  const [shareUrl, setShareUrl] = useState<{ logId: string; url: string } | null>(null)

  const load = useCallback(() => {
    Promise.all([fetchAuditLog(50), fetchAnchors()])
      .then(([r, a]) => { setRows(r); setAnchors(a); setErr('') })
      .catch(e => setErr(apiError(e).message))
  }, [])
  useEffect(load, [load])

  const check = async () => {
    setChecking(true)
    try {
      setChain(await verifyAuditChain())
    } catch (e) {
      toast.error('Could not check the chain', apiError(e).message)
    } finally {
      setChecking(false)
    }
  }

  const anchor = async () => {
    setAnchoring(true)
    try {
      const a = await anchorNow()
      toast.success('Anchored', `Root ${short(a.merkle_root)} written to the chain (rows ${a.from_seq ?? '—'} to ${a.to_seq ?? '—'}).`)
      load()
    } catch (e) {
      toast.error('Anchoring failed', apiError(e).message)
    } finally {
      setAnchoring(false)
    }
  }

  const proof = async (row: AuditRow, mode: 'download' | 'share') => {
    setProofBusy(`${row.log_id}:${mode}`)
    try {
      const b = await fetchProof(row.log_id)
      if (mode === 'download') {
        downloadText(`onsumm-proof-${row.seq ?? row.log_id.slice(0, 8)}.json`, JSON.stringify(b, null, 2))
      } else if (b.share_url) {
        setShareUrl({ logId: row.log_id, url: b.share_url })
      } else {
        toast.warn('No share link', 'The server did not return a share link for this record.')
      }
    } catch (e) {
      toast.error('Could not build the proof', apiError(e).message)
    } finally {
      setProofBusy('')
    }
  }

  return (
    <div className="cr">
      <PageHeader
        title="Audit proof"
        sub="Every approval and payment record is hash-chained and anchored on Base Sepolia, so anyone can check it was not changed."
        actions={
          <div className="cr-row">
            <a className="u-btn u-btn--ghost u-btn--md" href={VERIFY_PATH} target="_blank" rel="noreferrer">Open /verify</a>
            <Button variant="primary" iconLeft={<AnchorIcon size={14} />} loading={anchoring} onClick={() => void anchor()}>Anchor now</Button>
          </div>
        }
      />

      {err && <ErrorState title="Could not load the audit log" message={err} onRetry={load} />}

      <div className="cr-grid">
        <Card>
          <CardHeader
            title="Hash chain"
            sub="Recomputes every row hash for this company."
            actions={<Button size="sm" iconLeft={<ShieldCheck size={13} />} loading={checking} onClick={() => void check()}>Verify chain</Button>}
          />
          <CardBody>
            {!chain && <span className="cr-muted">Not checked yet in this session.</span>}
            {chain && (chain.ok
              ? <Note tone="ok">Intact — {chain.checked.toLocaleString()} rows checked, no break.</Note>
              : <Note tone="danger">Broken at sequence <strong>{chain.first_broken_seq}</strong> ({chain.checked.toLocaleString()} rows checked). A row was edited or deleted after it was written.</Note>)}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Anchors" sub="Merkle roots written on-chain." />
          <CardBody>
            {!anchors && !err && <Skeleton height={60} />}
            {anchors && anchors.length === 0 && <span className="cr-muted">Nothing anchored yet. Press “Anchor now”.</span>}
            {anchors && anchors.length > 0 && (
              <table className="cr-table">
                <thead><tr><th>#</th><th>Rows</th><th>Root</th><th>Tx</th><th>When</th></tr></thead>
                <tbody>
                  {anchors.slice(0, 8).map(a => (
                    <tr key={a.anchor_id}>
                      <td>{a.anchor_id}</td>
                      <td>{a.from_seq ?? '—'}–{a.to_seq ?? '—'}</td>
                      <td className="cr-mono" title={a.merkle_root}>{short(a.merkle_root)}</td>
                      <td>{a.tx_hash ? <TxLink hash={a.tx_hash} href={a.explorer} /> : <StatusBadge status={a.status ?? 'PENDING'} />}</td>
                      <td>{formatDateTime(a.anchored_at ?? null)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardBody>
        </Card>
      </div>

      {shareUrl && (
        <Note tone="ok">
          <div className="cr-row">
            <span className="cr-grow">Share link for the auditor (no login needed):</span>
            <CopyButton text={shareUrl.url} label="Copy link" />
          </div>
          <span className="cr-mono">{shareUrl.url}</span>
        </Note>
      )}

      <Card>
        <CardHeader title="Recent audit records" sub="Download a proof bundle, or copy a share link for /verify." />
        <CardBody>
          {!rows && !err && <Skeleton height={120} />}
          {rows && rows.length === 0 && <EmptyState title="No audit records yet" />}
          {rows && rows.length > 0 && (
            <div className="cr-table-wrap">
              <table className="cr-table">
                <thead><tr><th className="num">Seq</th><th>Table</th><th>Action</th><th>Entity</th><th>When</th><th>Anchored</th><th aria-label="Actions" /></tr></thead>
                <tbody>
                  {rows.map(r => (
                    <tr key={r.log_id}>
                      <td className="num">{r.seq ?? '—'}</td>
                      <td>{r.table_name}</td>
                      <td>{r.action}</td>
                      <td className="cr-mono">{r.entity_id ? r.entity_id.slice(0, 8) : '—'}</td>
                      <td>{formatDateTime(r.created_at)}</td>
                      <td>{r.anchor_id ? <Badge tone="success">#{r.anchor_id}</Badge> : <Badge tone="neutral">Not yet</Badge>}</td>
                      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                        <Button size="sm" variant="ghost" iconLeft={<Download size={13} />} loading={proofBusy === `${r.log_id}:download`} onClick={() => void proof(r, 'download')}>Bundle</Button>
                        <Button size="sm" variant="ghost" iconLeft={<Link2 size={13} />} loading={proofBusy === `${r.log_id}:share`} onClick={() => void proof(r, 'share')}>Share link</Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  )
}
