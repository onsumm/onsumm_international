/**
 * The payment sidebar for the USDC rail — the counterpart of PaymentSidebar.
 *
 * What is true of the whole payment rather than of a step: the Safe the money
 * leaves, how much of it is free to pay with, who has to sign, and the limits
 * the company set. "Available" is the backend's book figure (treasury_book.py):
 * the on-chain balance less demo-settled payouts and payouts still waiting on
 * the Safe, which is also what a new payout is checked against.
 */

import { Landmark, RefreshCw, Users } from 'lucide-react'
import { Badge, IconButton } from '../../ui'
import type { RailPolicy, TreasuryWallet } from '../../api/crypto'
import { AddressLink } from '../../components/crypto/common'
import { explorerAddress, formatUnits, shortAddress, unitsToBigInt } from '../../web3/units'
import { chainLabel } from './usdcPay'

function usd(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === '') return 'no limit'
  const n = Number(v)
  return n >= 1000 ? `$${(n / 1000).toLocaleString('en-US', { maximumFractionDigits: 1 })}k` : `$${n.toLocaleString('en-US')}`
}

export function UsdcTreasurySidebar({ wallet, policy, loading, onRefresh }: {
  wallet: TreasuryWallet | null
  policy: RailPolicy | null
  loading: boolean
  onRefresh: () => void
}) {
  const demo = wallet?.settlement_mode === 'demo'
  const available = wallet?.available_units ?? wallet?.usdc_balance_units ?? null
  const settled = unitsToBigInt(wallet?.settled_offchain_units)
  const reserved = unitsToBigInt(wallet?.reserved_units)
  // USDC is a dollar token, so today's outflow in units reads directly against the USD daily limit.
  const usedToday = Number(formatUnits(wallet?.paid_today_units, 2).replace(/,/g, ''))
  const daily = policy?.daily_limit_usd != null ? Number(policy.daily_limit_usd) : null
  const usedPct = daily ? Math.min(100, (usedToday / daily) * 100) : 0

  return (
    <aside className="pay__side" aria-label="Treasury">
      <section className="pay-side__sec">
        <h2 className="pay-side__head">
          Paying from
          {demo && <Badge tone="warn" title="Checked against the real Safe, recorded without broadcasting. No funds move.">DEMO</Badge>}
          <span className="up-side__refresh">
            <IconButton size="sm" label="Refresh balance" onClick={onRefresh} disabled={loading}
              icon={<RefreshCw size={13} className={loading ? 'up-spin' : undefined} />} />
          </span>
        </h2>

        {!wallet ? (
          <p className="cr-muted" style={{ margin: 0 }}>
            {loading ? 'Reading the treasury…' : 'No treasury Safe is connected. Add one under Crypto setup.'}
          </p>
        ) : (
          <div className="up-safe">
            <div className="up-safe__top">
              <span className="up-safe__icon" aria-hidden="true"><Landmark size={15} /></span>
              <div style={{ minWidth: 0 }}>
                <div className="up-safe__name">Treasury Safe</div>
                <div className="up-safe__sub">
                  <AddressLink address={wallet.address} href={wallet.explorer ?? explorerAddress(wallet.address)} />
                  <span>· {chainLabel(wallet.chain)}</span>
                </div>
              </div>
            </div>
            <div>
              <div className="up-safe__label">Available</div>
              <div className="up-safe__bal">
                {available === null ? '—' : formatUnits(available)} <span>USDC</span>
              </div>
            </div>
            <div className="up-safe__math" title="Available = on-chain balance − settled in demo − awaiting signatures">
              {wallet.usdc_balance_units === null ? 'On-chain balance unavailable' : `${formatUnits(wallet.usdc_balance_units)} on-chain`}
              {demo && settled > 0n && <> · −{formatUnits(settled.toString())} settled</>}
              {reserved > 0n && <> · −{formatUnits(reserved.toString())} pending ({wallet.reserved_count ?? 0})</>}
            </div>
          </div>
        )}
      </section>

      {wallet && wallet.safe_threshold != null && (
        <section className="pay-side__sec">
          <h2 className="pay-side__head"><Users size={12} aria-hidden="true" /> Signers · {wallet.safe_threshold} of {wallet.safe_owners.length}</h2>
          <div className="up-owners">
            {wallet.safe_owners.map(o => <span key={o} className="up-owner cr-mono" title={o}>{shortAddress(o)}</span>)}
          </div>
        </section>
      )}

      {policy && (
        <section className="pay-side__sec">
          <h2 className="pay-side__head">Limits</h2>
          <div className="up-limits">
            <span><b>{usd(policy.per_tx_limit_usd)}</b> per payment</span>
            <span><b>{usd(policy.daily_limit_usd)}</b> per day</span>
          </div>
          {daily ? (
            <div className="up-meter" aria-label={`Used today: ${usedPct.toFixed(0)}% of the daily limit`}>
              <div className="up-meter__bar"><span style={{ width: `${Math.max(usedPct, usedToday > 0 ? 2 : 0)}%` }} /></div>
              <div className="up-meter__txt">
                ${usedToday.toLocaleString('en-US', { maximumFractionDigits: 2 })} used today
                {wallet?.paid_today_count ? ` · ${wallet.paid_today_count} payment${wallet.paid_today_count === 1 ? '' : 's'}` : ''}
              </div>
            </div>
          ) : null}
        </section>
      )}
    </aside>
  )
}
