/**
 * Audit proof verification, entirely in the browser.
 *
 * Mirrors docs/crypto-rail/08_API_CONTRACT.md §7 byte for byte:
 *
 *   row_hash = sha256(prev_hash || utf8(canonical))
 *   leaf     = sha256(0x00 || salt || row_hash)
 *   node     = sha256(0x01 || min(a, b) || max(a, b))      byte-wise order
 *   top_leaf = sha256(0x02 || tenant uuid (16 bytes) || tenant_root)
 *
 * Nothing here trusts the server's own hashes: every value is recomputed from
 * the canonical text and the sibling paths, and the result is compared with the
 * root written on-chain. A bundle that passes proves the record existed, in
 * exactly this form, when the anchor transaction was mined.
 */

export interface AnchorRef {
  anchor_id?: number
  merkle_root: string
  from_seq?: number
  to_seq?: number
  tenant_count?: number
  chain_id?: number
  mode?: 'calldata' | 'contract'
  contract?: string | null
  tx_hash: string | null
  explorer?: string | null
  status?: string
  anchored_at?: string | null
}

export interface ProofBundle {
  version: number
  canonical: string
  record?: Record<string, unknown>
  prev_hash: string
  row_hash: string
  salt: string
  leaf?: string
  tenant_id: string
  tenant_path: string[]
  tenant_root: string
  top_leaf?: string
  top_path: string[]
  top_root: string
  anchor: AnchorRef | null
  share_url?: string
}

// ── bytes ────────────────────────────────────────────────────────────────────

export function hexToBytes(hex: string): Uint8Array {
  const clean = hex.startsWith('0x') || hex.startsWith('0X') ? hex.slice(2) : hex
  if (clean.length % 2 !== 0 || /[^0-9a-fA-F]/.test(clean)) throw new Error(`Not a hex string: ${hex.slice(0, 20)}…`)
  const out = new Uint8Array(clean.length / 2)
  for (let i = 0; i < out.length; i++) out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16)
  return out
}

export function bytesToHex(bytes: Uint8Array): string {
  return '0x' + Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('')
}

export function concatBytes(...parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0))
  let o = 0
  for (const p of parts) {
    out.set(p, o)
    o += p.length
  }
  return out
}

/** Byte-wise comparison, as the backend's `sorted([a, b])` on bytes. */
export function compareBytes(a: Uint8Array, b: Uint8Array): number {
  const n = Math.min(a.length, b.length)
  for (let i = 0; i < n; i++) if (a[i] !== b[i]) return a[i] - b[i]
  return a.length - b.length
}

export function sameHex(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false
  return a.toLowerCase().replace(/^0x/, '') === b.toLowerCase().replace(/^0x/, '')
}

export async function sha256(data: Uint8Array): Promise<Uint8Array> {
  // Copy into a fresh ArrayBuffer: subtle.digest rejects views over shared or
  // offset buffers in some engines.
  const buf = new Uint8Array(data).buffer
  return new Uint8Array(await crypto.subtle.digest('SHA-256', buf))
}

export function uuidToBytes(uuid: string): Uint8Array {
  const hex = uuid.replace(/-/g, '')
  if (hex.length !== 32) throw new Error(`Not a UUID: ${uuid}`)
  return hexToBytes(hex)
}

// ── the four hash rules ──────────────────────────────────────────────────────

export async function rowHash(prevHash: Uint8Array, canonical: string): Promise<Uint8Array> {
  return sha256(concatBytes(prevHash, new TextEncoder().encode(canonical)))
}

export async function leafHash(salt: Uint8Array, row: Uint8Array): Promise<Uint8Array> {
  return sha256(concatBytes(new Uint8Array([0x00]), salt, row))
}

export async function nodeHash(a: Uint8Array, b: Uint8Array): Promise<Uint8Array> {
  const [lo, hi] = compareBytes(a, b) <= 0 ? [a, b] : [b, a]
  return sha256(concatBytes(new Uint8Array([0x01]), lo, hi))
}

export async function topLeafHash(tenantId: string, tenantRoot: Uint8Array): Promise<Uint8Array> {
  return sha256(concatBytes(new Uint8Array([0x02]), uuidToBytes(tenantId), tenantRoot))
}

/** Folds a leaf up a sorted-pair path. No left/right flags are needed. */
export async function foldPath(start: Uint8Array, path: string[]): Promise<Uint8Array> {
  let acc = start
  for (const sibling of path) acc = await nodeHash(acc, hexToBytes(sibling))
  return acc
}

// ── anchor transaction input ─────────────────────────────────────────────────

const ONSM_MAGIC = '4f4e534d'

export interface DecodedAnchorInput {
  mode: 'calldata' | 'contract'
  root: string
  fromSeq?: number
  toSeq?: number
}

/**
 * Reads the Merkle root out of an anchor transaction's input.
 *
 * calldata mode: 0x | "ONSM" | 0x01 | root (32) | from_seq (8) | to_seq (8).
 * contract mode: anchor(bytes32 root, uint64, uint64) — the root is the first
 * ABI word, straight after the 4-byte selector. The selector itself is not
 * checked here (computing keccak in the browser would need a dependency); the
 * contract address is checked against the bundle instead.
 */
export function decodeAnchorInput(input: string): DecodedAnchorInput | null {
  const hex = (input || '').toLowerCase().replace(/^0x/, '')
  if (hex.startsWith(ONSM_MAGIC) && hex.slice(8, 10) === '01' && hex.length >= 10 + 64 + 32) {
    const root = '0x' + hex.slice(10, 74)
    const fromSeq = parseInt(hex.slice(74, 90), 16)
    const toSeq = parseInt(hex.slice(90, 106), 16)
    return { mode: 'calldata', root, fromSeq, toSeq }
  }
  if (hex.length >= 8 + 64) {
    return { mode: 'contract', root: '0x' + hex.slice(8, 72) }
  }
  return null
}

// ── full verification ────────────────────────────────────────────────────────

export type CheckKey = 'row_hash' | 'tenant_root' | 'top_root' | 'anchor'

export interface CheckResult {
  key: CheckKey
  label: string
  ok: boolean
  detail: string
}

export interface VerifyOutcome {
  ok: boolean
  checks: CheckResult[]
  computed: { rowHash: string; leaf: string; tenantRoot: string; topLeaf: string; topRoot: string }
}

/** Something that can fetch an anchor transaction's input by hash. */
export type AnchorFetcher = (txHash: string) => Promise<{ input: string; to: string | null; from: string } | null>

const LABELS: Record<CheckKey, string> = {
  row_hash: 'Record hash matches its text',
  tenant_root: 'Record is in the company’s daily tree',
  top_root: 'Company tree is in the anchored root',
  anchor: 'Root is written on-chain',
}

/**
 * Recomputes the bundle from its canonical text and compares each level.
 *
 * `canonicalOverride` lets the demo edit the record text before checking, to
 * show that a single changed character breaks the proof.
 */
export async function verifyBundle(
  bundle: ProofBundle,
  fetchAnchor?: AnchorFetcher,
  canonicalOverride?: string,
): Promise<VerifyOutcome> {
  const canonical = canonicalOverride ?? bundle.canonical
  const row = await rowHash(hexToBytes(bundle.prev_hash), canonical)
  const leaf = await leafHash(hexToBytes(bundle.salt), row)
  const tenantRoot = await foldPath(leaf, bundle.tenant_path)
  const topLeaf = await topLeafHash(bundle.tenant_id, tenantRoot)
  const topRoot = await foldPath(topLeaf, bundle.top_path)

  const rowHex = bytesToHex(row)
  const tenantHex = bytesToHex(tenantRoot)
  const topHex = bytesToHex(topRoot)
  const checks: CheckResult[] = []

  const rowOk = sameHex(rowHex, bundle.row_hash)
  checks.push({
    key: 'row_hash', label: LABELS.row_hash, ok: rowOk,
    detail: rowOk ? rowHex : `Recomputed ${short(rowHex)}, bundle says ${short(bundle.row_hash)}`,
  })
  const tenantOk = sameHex(tenantHex, bundle.tenant_root)
  checks.push({
    key: 'tenant_root', label: LABELS.tenant_root, ok: tenantOk,
    detail: tenantOk ? tenantHex : `Recomputed ${short(tenantHex)}, bundle says ${short(bundle.tenant_root)}`,
  })
  const topOk = sameHex(topHex, bundle.top_root)
  checks.push({
    key: 'top_root', label: LABELS.top_root, ok: topOk,
    detail: topOk ? topHex : `Recomputed ${short(topHex)}, bundle says ${short(bundle.top_root)}`,
  })

  if (!bundle.anchor?.tx_hash) {
    checks.push({ key: 'anchor', label: LABELS.anchor, ok: false, detail: 'This record has not been anchored yet.' })
  } else if (!fetchAnchor) {
    checks.push({ key: 'anchor', label: LABELS.anchor, ok: false, detail: 'No chain connection to check the anchor.' })
  } else {
    try {
      const tx = await fetchAnchor(bundle.anchor.tx_hash)
      if (!tx) {
        checks.push({ key: 'anchor', label: LABELS.anchor, ok: false, detail: `Transaction ${short(bundle.anchor.tx_hash)} not found on-chain.` })
      } else {
        const decoded = decodeAnchorInput(tx.input)
        const contractOk = decoded?.mode !== 'contract' || !bundle.anchor.contract || sameHex(tx.to, bundle.anchor.contract)
        const calldataOk = decoded?.mode !== 'calldata' || sameHex(tx.to, tx.from)
        const rootOk = !!decoded && sameHex(decoded.root, topHex)
        const ok = rootOk && contractOk && calldataOk
        checks.push({
          key: 'anchor', label: LABELS.anchor, ok,
          detail: ok
            ? `Root found in transaction ${short(bundle.anchor.tx_hash)} (${decoded?.mode} mode)`
            : !decoded
              ? 'The transaction does not carry an Onsumm anchor.'
              : !rootOk
                ? `On-chain root ${short(decoded.root)} differs from the recomputed ${short(topHex)}`
                : 'The anchor transaction was not sent by the expected account or contract.',
        })
      }
    } catch (e) {
      checks.push({ key: 'anchor', label: LABELS.anchor, ok: false, detail: `Could not read the chain: ${(e as Error).message}` })
    }
  }

  return {
    ok: checks.every(c => c.ok),
    checks,
    computed: { rowHash: rowHex, leaf: bytesToHex(leaf), tenantRoot: tenantHex, topLeaf: bytesToHex(topLeaf), topRoot: topHex },
  }
}

export function short(hex: string | null | undefined): string {
  if (!hex) return '—'
  return hex.length > 14 ? `${hex.slice(0, 8)}…${hex.slice(-4)}` : hex
}

/** Light shape check before verifying, so a wrong file gets a clear message. */
export function parseBundle(raw: unknown): ProofBundle {
  const b = raw as Partial<ProofBundle> | null
  const need: (keyof ProofBundle)[] = ['canonical', 'prev_hash', 'row_hash', 'salt', 'tenant_id', 'tenant_path', 'tenant_root', 'top_path', 'top_root']
  const missing = need.filter(k => b?.[k] === undefined || b?.[k] === null)
  if (!b || missing.length) throw new Error(`This is not an Onsumm proof bundle (missing ${missing.join(', ') || 'everything'}).`)
  return b as ProofBundle
}
