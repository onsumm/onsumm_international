/**
 * The browser wallet, through the EIP-1193 provider directly.
 *
 * No wagmi or viem: the crypto rail needs only five wallet calls (connect, chain
 * switch, personal_sign for the vendor's SIWE proof, eth_signTypedData_v4 for a
 * Safe transaction, and one read-only RPC lookup on /verify), so a dependency
 * would add weight and an upgrade surface for very little. See
 * docs/crypto-rail/00_DECISIONS.md.
 */

export interface Eip1193Provider {
  request(args: { method: string; params?: unknown[] | object }): Promise<unknown>
  on?(event: string, handler: (...args: unknown[]) => void): void
  removeListener?(event: string, handler: (...args: unknown[]) => void): void
}

declare global {
  interface Window {
    ethereum?: Eip1193Provider
  }
}

/** Base Sepolia — the hackathon network (chain ID 84532). */
export const BASE_SEPOLIA = {
  chainId: 84532,
  hexChainId: '0x14a34',
  name: 'Base Sepolia',
  rpcUrl: 'https://sepolia.base.org',
  explorer: 'https://sepolia.basescan.org',
} as const

/** Read-only JSON-RPC endpoint used by the public verify page. */
export const READ_RPC_URL: string = (import.meta.env.VITE_RPC_URL as string | undefined) || BASE_SEPOLIA.rpcUrl

export class WalletError extends Error {
  code?: number
  constructor(message: string, code?: number) {
    super(message)
    this.code = code
  }
}

export function getProvider(): Eip1193Provider | null {
  return typeof window !== 'undefined' && window.ethereum ? window.ethereum : null
}

export function hasWallet(): boolean {
  return getProvider() !== null
}

function provider(): Eip1193Provider {
  const p = getProvider()
  if (!p) throw new WalletError('No browser wallet found. Install MetaMask or Rabby, then reload this page.')
  return p
}

/** Turns a provider rejection into a sentence a person can act on. */
export function walletErrorMessage(e: unknown): string {
  const err = e as { code?: number; message?: string } | undefined
  if (err?.code === 4001) return 'You cancelled the request in your wallet.'
  if (err?.code === -32002) return 'Your wallet already has a request open. Open the wallet to finish it.'
  return err?.message || 'The wallet request failed.'
}

export async function connectWallet(): Promise<string> {
  const accounts = (await provider().request({ method: 'eth_requestAccounts' })) as string[]
  if (!accounts?.length) throw new WalletError('The wallet returned no account.')
  return accounts[0]
}

/**
 * Ask the wallet to let the user pick which account(s) this site may use.
 *
 * `eth_requestAccounts` only returns accounts already connected to the site, so
 * once one Safe owner is connected it keeps returning that owner. Signing as the
 * next owner needs the wallet's own account picker, which `wallet_requestPermissions`
 * opens. Wallets without it fall back to `eth_requestAccounts`.
 */
export async function chooseAccount(): Promise<string> {
  const p = provider()
  try {
    await p.request({ method: 'wallet_requestPermissions', params: [{ eth_accounts: {} }] })
  } catch (e) {
    if ((e as { code?: number })?.code === 4001) throw e // the user closed the picker
  }
  const accounts = (await p.request({ method: 'eth_requestAccounts' })) as string[]
  if (!accounts?.length) throw new WalletError('The wallet returned no account.')
  return accounts[0]
}

/** Every account the user has connected to this site (the wallet's selected one first). */
export async function connectedAccounts(): Promise<string[]> {
  const p = getProvider()
  if (!p) return []
  return ((await p.request({ method: 'eth_accounts' })) as string[]) ?? []
}

export async function currentAccount(): Promise<string | null> {
  const p = getProvider()
  if (!p) return null
  const accounts = (await p.request({ method: 'eth_accounts' })) as string[]
  return accounts?.[0] ?? null
}

export async function currentChainId(): Promise<number> {
  const hex = (await provider().request({ method: 'eth_chainId' })) as string
  return parseInt(hex, 16)
}

/** Switches to Base Sepolia, adding it to the wallet first if it is unknown there. */
export async function ensureBaseSepolia(): Promise<void> {
  const p = provider()
  if ((await currentChainId()) === BASE_SEPOLIA.chainId) return
  try {
    await p.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: BASE_SEPOLIA.hexChainId }] })
  } catch (e) {
    // 4902: the wallet does not know this chain yet.
    if ((e as { code?: number }).code !== 4902) throw e
    await p.request({
      method: 'wallet_addEthereumChain',
      params: [{
        chainId: BASE_SEPOLIA.hexChainId,
        chainName: BASE_SEPOLIA.name,
        nativeCurrency: { name: 'Sepolia Ether', symbol: 'ETH', decimals: 18 },
        rpcUrls: [BASE_SEPOLIA.rpcUrl],
        blockExplorerUrls: [BASE_SEPOLIA.explorer],
      }],
    })
  }
}

function utf8ToHex(text: string): string {
  return '0x' + Array.from(new TextEncoder().encode(text), b => b.toString(16).padStart(2, '0')).join('')
}

/** EIP-191 personal_sign — used for the vendor's SIWE (EIP-4361) ownership proof. */
export async function personalSign(message: string, address: string): Promise<string> {
  return (await provider().request({ method: 'personal_sign', params: [utf8ToHex(message), address] })) as string
}

export interface TypedDataField {
  name: string
  type: string
}

export interface TypedData {
  domain: Record<string, unknown>
  primaryType: string
  types: Record<string, TypedDataField[]>
  message: Record<string, unknown>
}

const DOMAIN_FIELD_TYPES: Record<string, string> = {
  name: 'string',
  version: 'string',
  chainId: 'uint256',
  verifyingContract: 'address',
  salt: 'bytes32',
}

/**
 * EIP712Domain built from the keys the domain actually has, in the canonical
 * order. A Safe signs over {chainId, verifyingContract} only; declaring fields
 * that are not present would change the domain separator and the signature
 * would not match the Safe's own hash.
 */
export function domainType(domain: Record<string, unknown>): TypedDataField[] {
  return Object.keys(DOMAIN_FIELD_TYPES)
    .filter(k => domain[k] !== undefined && domain[k] !== null)
    .map(k => ({ name: k, type: DOMAIN_FIELD_TYPES[k] }))
}

/** eth_signTypedData_v4 over the Safe transaction the backend built. */
export async function signTypedData(address: string, typed: TypedData): Promise<string> {
  const payload = {
    types: { EIP712Domain: domainType(typed.domain), ...typed.types },
    domain: typed.domain,
    primaryType: typed.primaryType,
    message: typed.message,
  }
  return (await provider().request({
    method: 'eth_signTypedData_v4',
    params: [address, JSON.stringify(payload)],
  })) as string
}

// ── Read-only RPC (no wallet needed) ─────────────────────────────────────────

export interface RpcTransaction {
  hash: string
  from: string
  to: string | null
  input: string
  blockNumber: string | null
}

let rpcId = 0

export async function rpcCall<T>(method: string, params: unknown[], url: string = READ_RPC_URL): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: ++rpcId, method, params }),
  })
  if (!res.ok) throw new Error(`RPC ${method} failed with HTTP ${res.status}`)
  const body = (await res.json()) as { result?: T; error?: { message?: string } }
  if (body.error) throw new Error(body.error.message || `RPC ${method} failed`)
  return body.result as T
}

export function getTransactionByHash(hash: string): Promise<RpcTransaction | null> {
  return rpcCall<RpcTransaction | null>('eth_getTransactionByHash', [hash])
}
