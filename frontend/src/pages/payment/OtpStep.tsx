/**
 * Step 3 — the confirmation code.
 *
 * One input, not six boxes: segmented digit fields lose OS autofill, paste,
 * undo and screen-reader support, which is why web.dev's SMS-OTP guidance and
 * Twilio's both specify a single `autocomplete="one-time-code"` field. The
 * backend expires the code after 120 seconds; the old page never showed that,
 * so the first sign of expiry was a red error after clicking Verify. Here it
 * counts down, and Resend unlocks when it actually becomes useful.
 */

import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, Clock, Mail, RotateCcw, ShieldAlert, XCircle } from 'lucide-react'
import { Button, Input } from '../../ui'
import { RESEND_COOLDOWN_MS, classifyOtpError, type OtpErrorKind } from './usePaymentFlow'

const OTP_FAILURE_COPY: Record<OtpErrorKind, { title: string; hint: string; fieldError: string; icon: typeof Clock }> = {
  expired: {
    title: 'This code has expired',
    hint: 'Codes expire 2 minutes after they’re sent. Request a new one below.',
    fieldError: 'Expired',
    icon: Clock,
  },
  too_many: {
    title: 'Too many incorrect attempts',
    hint: 'For your account’s safety this code is locked. Request a new one to keep trying.',
    fieldError: 'Locked',
    icon: ShieldAlert,
  },
  wrong: {
    title: 'That code didn’t match',
    hint: 'Check the 6-digit code in the email and enter it again.',
    fieldError: 'Incorrect',
    icon: AlertTriangle,
  },
  other: {
    title: 'Payment failed',
    hint: 'Something went wrong verifying this code.',
    fieldError: 'Failed',
    icon: XCircle,
  },
}

export interface OtpStepProps {
  sentTo: string
  expiresAt: number
  demoOtp: string | null
  /** More than one bill shares a single code, which is why Resend is unavailable. */
  isBulk: boolean
  otp: string
  setOtp: (v: string) => void
  error: string
  submitting: boolean
  onVerify: (code: string) => void
  onResend: () => void
  onRestart: () => void
}

export function OtpStep({
  sentTo, expiresAt, demoOtp, isBulk, otp, setOtp, error, submitting, onVerify, onResend, onRestart,
}: OtpStepProps) {
  const [now, setNow] = useState(() => Date.now())
  const [sentAt] = useState(() => Date.now())
  const fieldRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  // Focusing the code field is the whole purpose of this step, and the user
  // arrived by pressing a button — so this is expected, not a focus steal. Done
  // on mount rather than with autoFocus, which fires before the step is painted
  // and which jsx-a11y flags because it is usually the wrong instinct.
  useEffect(() => {
    fieldRef.current?.querySelector('input')?.focus()
  }, [])

  const msLeft = Math.max(0, expiresAt - now)
  const secsLeft = Math.ceil(msLeft / 1000)
  const expired = msLeft === 0
  const mm = Math.floor(secsLeft / 60)
  const ss = String(secsLeft % 60).padStart(2, '0')
  // Resending before the code is even in the inbox just confuses; after it has
  // expired it is the only way forward.
  const canResend = !isBulk && (expired || now - sentAt > RESEND_COOLDOWN_MS)
  const errorKind = error ? classifyOtpError(error) : null
  const FailIcon = errorKind ? OTP_FAILURE_COPY[errorKind].icon : null

  return (
    <div className="pay-card">
      <h2 className="pay-card__title">Enter the confirmation code</h2>
      <p className="pay__sub" style={{ margin: '0 0 var(--sp-3)', display: 'flex', alignItems: 'center', gap: 'var(--sp-2)' }}>
        <Mail size={13} aria-hidden="true" />
        Sent to <strong>{sentTo}</strong>
      </p>

      {demoOtp && (
        <div className="pay-demo">
          <span>
            <strong>DEMO</strong> — this environment also accepts{' '}
            <span className="pay-demo__code">{demoOtp}</span>, with no expiry.
          </span>
          <Button variant="secondary" size="sm" onClick={() => setOtp(demoOtp)}>
            Use demo code
          </Button>
        </div>
      )}

      <div className="pay-otp">
        {/* A structured failed-state, not the field's own one-line error: this
            names what went wrong (expired vs. wrong code vs. locked out) and
            what to do about it, since "Verification failed" alone left the
            only path forward as guesswork. */}
        {errorKind && FailIcon && (
          <div className={`pay-otp__fail pay-otp__fail--${errorKind}`}>
            <FailIcon size={16} aria-hidden="true" />
            <div>
              <div className="pay-otp__fail-title">{OTP_FAILURE_COPY[errorKind].title}</div>
              <div className="pay-otp__fail-hint">
                {OTP_FAILURE_COPY[errorKind].hint}
                {errorKind === 'other' && error ? ` (${error})` : ''}
              </div>
            </div>
          </div>
        )}

        <div className="pay-otp__input" ref={fieldRef}>
          <Input
            label="6-digit code"
            // type=text (not number) keeps leading zeros; one-time-code lets the
            // OS offer the code straight from the email/SMS.
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="\d{6}"
            maxLength={6}
            value={otp}
            // Full explanation is the panel above; the field itself only needs
            // to look invalid, not repeat the same sentence a second time.
            error={error ? OTP_FAILURE_COPY[errorKind ?? 'other'].fieldError : null}
            disabled={submitting}
            onChange={e => setOtp(e.target.value.replace(/\D/g, '').slice(0, 6))}
            onKeyDown={e => {
              if (e.key === 'Enter' && otp.length === 6) onVerify(otp)
            }}
          />
        </div>

        <div
          className={`pay-otp__timer${expired ? ' pay-otp__timer--out' : secsLeft <= 30 ? ' pay-otp__timer--soon' : ''}`}
          // A ticking number announced every second is unusable with a screen
          // reader; the state change is announced instead, below.
          aria-hidden="true"
        >
          <Clock size={13} />
          {expired ? 'Code expired' : `Expires in ${mm}:${ss}`}
        </div>
        <span className="sr-only" role="status">
          {expired ? 'The confirmation code has expired.' : ''}
        </span>

        <div style={{ display: 'flex', gap: 'var(--sp-2)', flexWrap: 'wrap' }}>
          <Button
            variant="success"
            disabled={otp.length !== 6 || expired}
            loading={submitting}
            title={expired ? 'This code has expired — request a new one' : undefined}
            onClick={() => onVerify(otp)}
          >
            Verify and pay
          </Button>
          {!isBulk && (
            <Button
              variant="secondary"
              iconLeft={<RotateCcw size={13} />}
              disabled={!canResend || submitting}
              title={canResend ? undefined : 'Available shortly, or as soon as the code expires'}
              onClick={onResend}
            >
              Resend code
            </Button>
          )}
          <Button variant="ghost" onClick={onRestart} disabled={submitting}>
            Cancel
          </Button>
        </div>

        {isBulk && (
          <p className="pay__sub" style={{ margin: 0 }}>
            All {' '}bills in this payment share one code, so it cannot be resent individually. If it has expired,
            cancel and start the payment again.
          </p>
        )}
      </div>
    </div>
  )
}
