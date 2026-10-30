import { describe, it, expect } from 'vitest'
import {
  bytesToHex, decodeAnchorInput, hexToBytes, leafHash, nodeHash, rowHash, sha256, topLeafHash,
  verifyBundle, compareBytes, type ProofBundle,
} from './merkle'

const enc = (s: string) => new TextEncoder().encode(s)
const cat = (...p: Uint8Array[]) => {
  const out = new Uint8Array(p.reduce((n, x) => n + x.length, 0))
  let o = 0
  for (const x of p) { out.set(x, o); o += x.length }
  return out
}
const digest = async (b: Uint8Array) => new Uint8Array(await crypto.subtle.digest('SHA-256', new Uint8Array(b).buffer))

const TENANT = '11111111-2222-3333-4444-555555555555'
const PREV = new Uint8Array(32) // first row of a chain: 32 zero bytes
const SALT = hexToBytes('0x' + 'ab'.repeat(32))

/**
 * A two-record tenant tree and a two-tenant top tree, built here with WebCrypto
 * directly (not through the module under test), so the test pins the rules
 * rather than echoing the implementation.
 */
async function buildVector() {
  const canonA = '{"action":"INSERT","seq":1,"table_name":"fact_disbursements"}'
  const canonB = '{"action":"UPDATE","seq":2,"table_name":"fact_disbursements"}'
  const rowA = await digest(cat(PREV, enc(canonA)))
  const rowB = await digest(cat(rowA, enc(canonB)))
  const leafA = await digest(cat(new Uint8Array([0]), SALT, rowA))
  const leafB = await digest(cat(new Uint8Array([0]), SALT, rowB))
  const [lo, hi] = compareBytes(leafA, leafB) <= 0 ? [leafA, leafB] : [leafB, leafA]
  const tenantRoot = await digest(cat(new Uint8Array([1]), lo, hi))
  const topLeaf = await digest(cat(new Uint8Array([2]), hexToBytes(TENANT.replace(/-/g, '')), tenantRoot))
  const otherTop = await digest(enc('another tenant'))
  const [tlo, thi] = compareBytes(topLeaf, otherTop) <= 0 ? [topLeaf, otherTop] : [otherTop, topLeaf]
  const topRoot = await digest(cat(new Uint8Array([1]), tlo, thi))

  const bundle: ProofBundle = {
    version: 1,
    canonical: canonA,
    prev_hash: bytesToHex(PREV),
    row_hash: bytesToHex(rowA),
    salt: bytesToHex(SALT),
    tenant_id: TENANT,
    tenant_path: [bytesToHex(leafB)],
    tenant_root: bytesToHex(tenantRoot),
    top_path: [bytesToHex(otherTop)],
    top_root: bytesToHex(topRoot),
    anchor: { merkle_root: bytesToHex(topRoot), tx_hash: '0xfeed', mode: 'calldata' },
  }
  const input = '0x4f4e534d01' + bytesToHex(topRoot).slice(2) + '0000000000000001' + '0000000000000002'
  return { bundle, input, topRoot }
}

describe('hash rules', () => {
  it('matches a known SHA-256 vector', async () => {
    expect(bytesToHex(await sha256(enc('abc')))).toBe(
      '0xba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    )
  })

  it('orders node children byte-wise, so the pair hashes the same either way', async () => {
    const a = hexToBytes('0x01' + '00'.repeat(31))
    const b = hexToBytes('0xff' + '00'.repeat(31))
    expect(bytesToHex(await nodeHash(a, b))).toBe(bytesToHex(await nodeHash(b, a)))
  })

  it('separates leaves, nodes and top leaves by their prefix byte', async () => {
    const x = new Uint8Array(32)
    const leaf = await leafHash(new Uint8Array(0), x)
    const top = await topLeafHash(TENANT, x)
    const row = await rowHash(new Uint8Array(0), '')
    expect(new Set([bytesToHex(leaf), bytesToHex(top), bytesToHex(row)]).size).toBe(3)
  })
})

describe('anchor input', () => {
  it('decodes calldata mode', () => {
    const root = '0x' + '12'.repeat(32)
    const d = decodeAnchorInput('0x4f4e534d01' + root.slice(2) + '0000000000000005' + '0000000000000009')
    expect(d).toEqual({ mode: 'calldata', root, fromSeq: 5, toSeq: 9 })
  })

  it('reads the root from the first ABI word in contract mode', () => {
    const root = '0x' + '34'.repeat(32)
    const d = decodeAnchorInput('0xabcdef01' + root.slice(2) + '00'.repeat(64))
    expect(d?.mode).toBe('contract')
    expect(d?.root).toBe(root)
  })
})

describe('verifyBundle', () => {
  it('verifies an untouched bundle against its anchor transaction', async () => {
    const { bundle, input } = await buildVector()
    const out = await verifyBundle(bundle, async () => ({ input, to: '0xaaa', from: '0xAAA' }))
    expect(out.checks.map(c => [c.key, c.ok])).toEqual([
      ['row_hash', true], ['tenant_root', true], ['top_root', true], ['anchor', true],
    ])
    expect(out.ok).toBe(true)
  })

  it('fails from the first level when one character of the record changes', async () => {
    const { bundle, input } = await buildVector()
    const tampered = bundle.canonical.replace('INSERT', 'INSERX')
    const out = await verifyBundle(bundle, async () => ({ input, to: '0xaaa', from: '0xaaa' }), tampered)
    expect(out.ok).toBe(false)
    expect(out.checks[0]).toMatchObject({ key: 'row_hash', ok: false })
    expect(out.checks[3]).toMatchObject({ key: 'anchor', ok: false })
  })

  it('fails the anchor check when the on-chain root differs', async () => {
    const { bundle } = await buildVector()
    const wrong = '0x4f4e534d01' + 'ee'.repeat(32) + '00'.repeat(16)
    const out = await verifyBundle(bundle, async () => ({ input: wrong, to: '0xa', from: '0xa' }))
    expect(out.checks.slice(0, 3).every(c => c.ok)).toBe(true)
    expect(out.checks[3].ok).toBe(false)
  })

  it('reports an unanchored record instead of passing it', async () => {
    const { bundle } = await buildVector()
    const out = await verifyBundle({ ...bundle, anchor: null })
    expect(out.ok).toBe(false)
    expect(out.checks[3].detail).toMatch(/not been anchored/)
  })
})
