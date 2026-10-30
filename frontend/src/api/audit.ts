/**
 * Audit anchor API — docs/crypto-rail/08_API_CONTRACT.md §7. The two
 * /api/public routes are read without a login (the /verify page), so they use a
 * bare axios instance rather than the signed-in one.
 */

import axios from 'axios'
import type { AnchorRef, ProofBundle } from '../web3/merkle'

export type Anchor = Required<Pick<AnchorRef, 'anchor_id' | 'merkle_root'>> & AnchorRef

export interface AuditRow {
  log_id: string
  seq: number | null
  table_name: string
  entity_id: string | null
  action: string
  created_at: string
  anchor_id: number | null
}

export interface ChainCheck {
  ok: boolean
  checked: number
  first_broken_seq: number | null
}

export async function fetchAuditLog(limit = 50): Promise<AuditRow[]> {
  return (await axios.get<{ rows: AuditRow[] }>('/api/audit/log', { params: { limit } })).data.rows
}
export async function verifyAuditChain(): Promise<ChainCheck> {
  return (await axios.get<ChainCheck>('/api/audit/verify-chain')).data
}
export async function anchorNow(): Promise<Anchor> {
  return (await axios.post<Anchor>('/api/audit/anchor')).data
}
export async function fetchAnchors(): Promise<Anchor[]> {
  return (await axios.get<{ anchors: Anchor[] }>('/api/audit/anchors')).data.anchors
}
export async function fetchProof(logId: string): Promise<ProofBundle> {
  return (await axios.get<ProofBundle>(`/api/audit/proof/${logId}`)).data
}

const publicApi = axios.create({ baseURL: (import.meta.env.VITE_API_URL as string | undefined) || undefined })

export async function fetchSharedProof(shareToken: string): Promise<ProofBundle> {
  return (await publicApi.get<ProofBundle>(`/api/public/proofs/${encodeURIComponent(shareToken)}`)).data
}
export async function fetchPublicAnchor(anchorId: number | string): Promise<Anchor> {
  return (await publicApi.get<Anchor>(`/api/public/anchors/${anchorId}`)).data
}
