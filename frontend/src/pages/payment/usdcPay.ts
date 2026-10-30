/**
 * Plain helpers for the USDC pay screen (UsdcPayParts, UsdcTreasurySidebar),
 * kept apart from the components so fast refresh keeps working.
 */

import type { RailPolicy, SupplierWallet, TreasuryWallet, WithholdingPreview } from '../../api/crypto'
import { formatUnits, formatUsdc, unitsToBigInt } from '../../web3/units'

/** "base-sepolia" → "Base Sepolia". */
export function chainLabel(chain: string | null | undefined): string {
  return (chain || '').split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ') || '—'
}

export interface Check {
  ok: boolean
  label: string
  /** Pill text when the check passes. */
  short?: string
  detail?: string
  warn?: boolean
  key?: 'wallet' | 'screening' | 'balance' | 'per_tx' | 'daily' | 'policy' | 'signoff'
}

/** Every condition the backend will also check, evaluated on what the page already knows. */
export function preflight({ preview, treasury, policy, wallet, amountUsd }: {
  preview: WithholdingPreview | null
  treasury: TreasuryWallet | null
  policy: RailPolicy | null
  wallet: SupplierWallet | null
  amountUsd: number | null
}): Check[] {
  const out: Check[] = []
  out.push(wallet?.status === 'ACTIVE'
    ? { key: 'wallet', ok: true, short: 'Wallet active', label: 'Vendor wallet verified and active' }
    : { key: 'wallet', ok: false, label: 'Vendor wallet not active', detail: 'Approve the vendor’s wallet first.' })
  if (wallet?.screening_status) {
    const clear = ['CLEAR', 'PASSED', 'OK'].includes(wallet.screening_status.toUpperCase())
    out.push({ key: 'screening', ok: clear, short: 'Sanctions clear', label: clear ? 'Wallet passed sanctions screening' : `Screening: ${wallet.screening_status.toLowerCase()}` })
  }
  const available = treasury?.available_units ?? treasury?.usdc_balance_units
  if (preview?.gross_units && available != null) {
    const gross = unitsToBigInt(preview.gross_units)
    const avail = unitsToBigInt(available)
    out.push(gross <= avail
      ? { key: 'balance', ok: true, short: 'Funds available', label: 'Treasury has enough USDC', detail: `${formatUsdc((avail - gross).toString())} left after this payment` }
      : { key: 'balance', ok: false, label: 'Not enough USDC in the treasury', detail: `needs ${formatUnits(preview.gross_units)}, only ${formatUnits(available)} available` })
  }
  if (policy && amountUsd != null) {
    const per = policy.per_tx_limit_usd != null ? Number(policy.per_tx_limit_usd) : null
    if (per != null) out.push(amountUsd <= per
      ? { key: 'per_tx', ok: true, short: 'Within payment limit', label: `Within the $${per.toLocaleString('en-US')} per-payment limit` }
      : { key: 'per_tx', ok: false, label: `Over the $${per.toLocaleString('en-US')} per-payment limit` })
    const daily = policy.daily_limit_usd != null ? Number(policy.daily_limit_usd) : null
    if (daily != null) {
      const used = Number(formatUnits(treasury?.paid_today_units, 2).replace(/,/g, ''))
      out.push(used + amountUsd <= daily
        ? { key: 'daily', ok: true, short: 'Within daily limit', label: `Within the $${daily.toLocaleString('en-US')} daily limit` }
        : { key: 'daily', ok: false, label: `Over the $${daily.toLocaleString('en-US')} daily limit`, detail: `$${used.toLocaleString('en-US')} already paid today` })
    }
  }
  if (preview) {
    out.push(preview.policy.allowed
      ? { key: 'policy', ok: true, short: 'Policy OK', label: 'Company payment policy passes' }
      : { key: 'policy', ok: false, label: 'Blocked by company payment policy' })
    if (preview.requires_signoff) out.push({ key: 'signoff', ok: true, warn: true, short: 'Adviser sign-off needed', label: 'Withholding treatment needs adviser sign-off' })
  }
  // Problems first: a blocked payment should say why before what passed.
  return out.sort((a, b) => Number(a.ok) - Number(b.ok) || Number(!a.warn) - Number(!b.warn))
}
