import { useEffect, useState } from 'react'
import { useAuth } from '../auth/AuthContext'
import { Eye, EyeOff, ArrowRight, FileSearch, GitBranch, Shield, CheckCircle, CheckCircle2 } from 'lucide-react'

const API_BASE = import.meta.env.VITE_API_URL || ''

interface Props { onBack?: () => void }

type ForgotStep = 'login' | 'forgot' | 'otp' | 'reset' | 'done'
type SsoProvider = 'google' | 'microsoft'

// Reason codes the SSO callback puts in the redirect. Each one tells the user
// what to do next rather than just that it failed.
const SSO_ERRORS: Record<string, string> = {
  no_account: "That email isn't registered as a team member. Ask an admin to add it under Settings \u2192 Team members, then try again.",
  ambiguous_account: 'More than one account uses that email address. Please sign in with your username and password.',
  email_unverified: "Google hasn't verified that email address, so it can't be used to sign in.",
  no_email: "That account didn't share an email address, so we couldn't match it to a team member.",
  cancelled: 'Sign-in was cancelled.',
  error: 'Sign-in failed. Please try again.',
}

// Hackathon demo logins, shown under the form. Clicking one fills the form.
const DEMO_ACCOUNTS = [
  { label: 'Main', username: 'vibhanshu', password: 'vibhanshu123' },
  { label: 'International', username: 'lumen.asha', password: 'Demo@2026!' },
]

// Provider marks. Inline SVG rather than an icon pack: lucide ships no brand
// logos, and both providers require their own colours on a light surface — a
// monochrome stand-in would be off-guidelines for each.
function GoogleMark() {
  return (
    <svg width="17" height="17" viewBox="0 0 18 18" aria-hidden="true" focusable="false">
      <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z" />
      <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z" />
      <path fill="#FBBC05" d="M3.97 10.72a5.41 5.41 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33z" />
      <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.46 3.44 1.35l2.58-2.58C13.46.9 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z" />
    </svg>
  )
}

function MicrosoftMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 18 18" aria-hidden="true" focusable="false">
      <rect width="8.2" height="8.2" x="0" y="0" fill="#F25022" />
      <rect width="8.2" height="8.2" x="9.8" y="0" fill="#7FBA00" />
      <rect width="8.2" height="8.2" x="0" y="9.8" fill="#00A4EF" />
      <rect width="8.2" height="8.2" x="9.8" y="9.8" fill="#FFB900" />
    </svg>
  )
}

export default function LoginPage({ onBack }: Props) {
  const { login, loginWithSso } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow]         = useState(false)
  const [error, setError]       = useState('')
  const [loading, setLoading]   = useState(false)
  const [ssoBusy, setSsoBusy]   = useState<SsoProvider | null>(null)

  // Forgot password flow
  const [step, setStep]           = useState<ForgotStep>('login')
  const [fpUser, setFpUser]       = useState('')
  const [otp, setOtp]             = useState('')
  const [newPw, setNewPw]         = useState('')
  const [showPw, setShowPw]       = useState(false)
  const [fpMsg, setFpMsg]         = useState('')
  const [fpErr, setFpErr]         = useState('')
  const [fpLoading, setFpLoading] = useState(false)

  // The SSO callback redirects back here with either a single-use code or a
  // reason it failed. Redeem the code immediately and strip it from the URL so
  // it can't survive in history or be replayed by a refresh.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const code = params.get('sso')
    const failed = params.get('sso_error')
    if (!code && !failed) return
    window.history.replaceState({}, '', window.location.pathname)
    if (failed) { setError(SSO_ERRORS[failed] || SSO_ERRORS.error); return }
    if (code) {
      setLoading(true)
      loginWithSso(code)
        .then(ok => { if (!ok) setError('Sign-in could not be completed. Please try again.') })
        .finally(() => setLoading(false))
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const startSso = async (provider: SsoProvider) => {
    setError(''); setSsoBusy(provider)
    try {
      const res = await fetch(`${API_BASE}/api/auth/sso/authorize?provider=${provider}`)
      if (!res.ok) throw new Error('authorize failed')
      const { auth_url } = await res.json()
      window.location.href = auth_url
    } catch {
      setError(`Could not start ${provider === 'google' ? 'Google' : 'Microsoft'} sign-in.`)
      setSsoBusy(null)
    }
  }

  const handleLogin = async (u = username, p = password) => {
    setError(''); setLoading(true)
    const ok = await login(u, p)
    if (!ok) setError('Invalid username or password.')
    setLoading(false)
  }

  const handleForgot = async () => {
    setFpErr(''); setFpLoading(true)
    try {
      const res = await fetch(`${API_BASE}/api/auth/forgot-password`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: fpUser })
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Failed')
      setFpMsg(data.message)
      setStep('otp')
    } catch (e: unknown) { setFpErr(e instanceof Error ? e.message : 'Error') }
    setFpLoading(false)
  }

  const handleVerifyOtp = async () => {
    setFpErr(''); setFpLoading(true)
    try {
      const res = await fetch(`${API_BASE}/api/auth/verify-otp`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: fpUser, otp })
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Invalid OTP')
      setStep('reset')
    } catch (e: unknown) { setFpErr(e instanceof Error ? e.message : 'Error') }
    setFpLoading(false)
  }

  const handleReset = async () => {
    setFpErr(''); setFpLoading(true)
    try {
      const res = await fetch(`${API_BASE}/api/auth/reset-password`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: fpUser, otp, new_password: newPw })
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Failed')
      setStep('done')
    } catch (e: unknown) { setFpErr(e instanceof Error ? e.message : 'Error') }
    setFpLoading(false)
  }



  return (
    <>
      {/* ── Top nav ── */}
      <nav className="lp__nav">
        <button className="lp__nav-logo" onClick={onBack}>
          <img src="/brand/onsumm-wordmark-dark.svg" alt="Onsumm" />
        </button>

        <div style={{ marginLeft: 'auto' }}>
          <button className="lp__nav-back" onClick={onBack}>← Back to site</button>
        </div>
      </nav>

      <div className="lp">

        {/* ── Left panel ── */}
        <div className="lp__left">
          {/* Subtle grid pattern */}
          <div className="lp__grid-bg" />

          <div className="lp__left-inner">
            {/* Brand */}
            <div className="lp__brand">
              <div className="lp__brand-logo"><img src="/brand/onsumm-wordmark-light.svg" alt="Onsumm" /></div>
              <div className="lp__brand-badge">AP Automation Platform</div>
            </div>

            <h2 className="lp__left-headline">
              Automate your entire<br />
              <span className="lp__teal-text">Accounts Payable</span> workflow
            </h2>
            <p className="lp__left-sub">
              From intelligent invoice capture to 3-way matching and zero-touch vendor payments — built for Indian finance teams.
            </p>

            {/* Feature list */}
            <div className="lp__feat-list">
              {[
                { icon: <FileSearch size={15} strokeWidth={1.5} />, label: 'AI Invoice Capture & OCR', sub: '99.9% accuracy on Indian formats' },
                { icon: <GitBranch size={15} strokeWidth={1.5} />, label: 'Multi-level Approval Workflows', sub: 'Configurable per team & threshold' },
                { icon: <Shield size={15} strokeWidth={1.5} />, label: 'GST & TDS Compliance', sub: 'Auto-validate GSTIN, calculate TDS' },
              ].map((f, i) => (
                <div key={i} className="lp__feat">
                  <div className="lp__feat-icon">{f.icon}</div>
                  <div>
                    <div className="lp__feat-label">{f.label}</div>
                    <div className="lp__feat-sub">{f.sub}</div>
                  </div>
                </div>
              ))}
            </div>

            {/* Stats row */}
            <div className="lp__stats">
              {[
                { val: '95%', label: 'Less manual entry' },
                { val: '3×', label: 'Faster approvals' },
                { val: '100%', label: 'Audit-ready' },
              ].map((s, i) => (
                <div key={i} className="lp__stat">
                  <div className="lp__stat-val">{s.val}</div>
                  <div className="lp__stat-lbl">{s.label}</div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* ── Right panel ── */}
        <div className="lp__right">
          <div className="lp__form-wrap">

            {/* Mobile-only logo */}
            <div className="lp__mobile-logo"><img src="/brand/onsumm-wordmark-dark.svg" alt="Onsumm" /></div>

            {/* Header */}
            <div className="lp__form-header">
              <div className="lp__form-icon">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4M10 17l5-5-5-5M15 12H3"/>
                </svg>
              </div>
              <div>
                <h1 className="lp__title">Welcome back</h1>
                <p className="lp__subtitle">Sign in to your onsumm.ai account</p>
              </div>
            </div>

            {/* Login Form */}
            {step === 'login' && (
              <>
              <form className="lp__form" onSubmit={e => { e.preventDefault(); handleLogin() }}>
                <div className="lp__field">
                  <label className="lp__lbl">Username</label>
                  <input className="lp__inp" placeholder="e.g. admin" value={username}
                    onChange={e => setUsername(e.target.value)} autoFocus autoComplete="username" />
                </div>
                <div className="lp__field">
                  <label className="lp__lbl">Password</label>
                  <div className="lp__pw-wrap">
                    <input className="lp__inp lp__inp--pw" type={show ? 'text' : 'password'}
                      placeholder="Enter your password" value={password}
                      onChange={e => setPassword(e.target.value)} autoComplete="current-password" />
                    <button
                      className="lp__eye"
                      type="button"
                      onClick={() => setShow(s => !s)}
                      aria-label={show ? 'Hide password' : 'Show password'}
                      aria-pressed={show}
                    >
                      {show ? <EyeOff size={15} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}
                    </button>
                  </div>
                </div>
                <div style={{ textAlign: 'right', marginTop: '-8px' }}>
                  <button type="button" className="lp__link" onClick={() => { setStep('forgot'); setFpUser(username); setFpErr('') }}>
                    Forgot password?
                  </button>
                </div>
                {error && (
                  <div className="lp__err">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                    {error}
                  </div>
                )}
                <button className="lp__btn" type="submit" disabled={loading || !username || !password}>
                  {loading ? <><span className="lp__spin" /> Signing in…</> : <><ArrowRight size={15} /> Sign In</>}
                </button>
              </form>

              <div className="lp__divider"><span>or</span></div>

              <div className="lp__sso-row">
                <button type="button" className="lp__btn lp__btn--sso"
                  disabled={loading || ssoBusy !== null} onClick={() => startSso('google')}>
                  <GoogleMark />
                  {ssoBusy === 'google' ? 'Redirecting…' : 'Continue with Google'}
                </button>
                <button type="button" className="lp__btn lp__btn--sso"
                  disabled={loading || ssoBusy !== null} onClick={() => startSso('microsoft')}>
                  <MicrosoftMark />
                  {ssoBusy === 'microsoft' ? 'Redirecting…' : 'Continue with Microsoft'}
                </button>
              </div>

              <div className="lp__demo">
                <div className="lp__demo-title">Demo logins</div>
                {DEMO_ACCOUNTS.map(a => (
                  <button key={a.username} type="button" className="lp__demo-row"
                    onClick={() => { setUsername(a.username); setPassword(a.password); setError('') }}>
                    <span className="lp__demo-tag">{a.label}</span>
                    <span className="lp__demo-cred"><code>{a.username}</code> / <code>{a.password}</code></span>
                  </button>
                ))}
              </div>
              </>
            )}

            {/* Forgot — enter username */}
            {step === 'forgot' && (
              <div className="lp__form">
                <p style={{ fontSize: 13, color: '#737781', marginBottom: 4 }}>Enter your username and we'll send an OTP to your registered email.</p>
                <div className="lp__field">
                  <label className="lp__lbl">Username</label>
                  <input className="lp__inp" placeholder="e.g. admin" value={fpUser} onChange={e => setFpUser(e.target.value)} autoFocus />
                </div>
                {fpErr && <div className="lp__err"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>{fpErr}</div>}
                <button className="lp__btn" onClick={handleForgot} disabled={fpLoading || !fpUser}>
                  {fpLoading ? <><span className="lp__spin" /> Sending OTP…</> : <>Send OTP</>}
                </button>
                <button type="button" className="lp__link" onClick={() => setStep('login')}>← Back to login</button>
              </div>
            )}

            {/* OTP verification */}
            {step === 'otp' && (
              <div className="lp__form">
                {fpMsg && <div className="lp__ok">{fpMsg}</div>}
                <div className="lp__field">
                  <label className="lp__lbl">Enter OTP</label>
                  <input
                    className="lp__inp"
                    placeholder="6-digit code"
                    value={otp}
                    onChange={e => setOtp(e.target.value.replace(/\D/g, '').slice(0, 6))}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    pattern="[0-9]*"
                    maxLength={6}
                  />
                </div>
                {fpErr && <div className="lp__err"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>{fpErr}</div>}
                <button className="lp__btn" onClick={handleVerifyOtp} disabled={fpLoading || otp.length !== 6}>
                  {fpLoading ? <><span className="lp__spin" /> Verifying…</> : <>Verify OTP</>}
                </button>
                <button type="button" className="lp__link" onClick={() => setStep('forgot')}>← Resend OTP</button>
              </div>
            )}

            {/* New password */}
            {step === 'reset' && (
              <div className="lp__form">
                <p style={{ fontSize: 13, color: '#737781' }}>OTP verified. Set your new password.</p>
                <div className="lp__field">
                  <label className="lp__lbl">New Password</label>
                  <div className="lp__pw-wrap">
                    <input className="lp__inp lp__inp--pw" type={showPw ? 'text' : 'password'}
                      placeholder="Min 6 characters" value={newPw} onChange={e => setNewPw(e.target.value)} autoFocus />
                    <button
                      className="lp__eye"
                      type="button"
                      onClick={() => setShowPw(s => !s)}
                      aria-label={showPw ? 'Hide password' : 'Show password'}
                      aria-pressed={showPw}
                    >
                      {showPw ? <EyeOff size={15} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}
                    </button>
                  </div>
                </div>
                {fpErr && <div className="lp__err"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>{fpErr}</div>}
                <button className="lp__btn" onClick={handleReset} disabled={fpLoading || newPw.length < 6}>
                  {fpLoading ? <><span className="lp__spin" /> Updating…</> : <>Update Password</>}
                </button>
              </div>
            )}

            {/* Done */}
            {step === 'done' && (
              <div className="lp__form" style={{ textAlign: 'center', gap: 16 }}>
                <CheckCircle2 size={40} color="#047857" aria-hidden="true" style={{ margin: '0 auto' }} />
                <p style={{ fontSize: 14, color: '#0b1c30', fontWeight: 600 }}>Password updated successfully!</p>
                <button className="lp__btn" onClick={() => { setStep('login'); setOtp(''); setNewPw('') }}>
                  Back to Login
                </button>
              </div>
            )}

            {/* Trust line */}
            <div className="lp__trust">
              {['Encrypted', 'India hosted'].map(t => (
                <span key={t} className="lp__trust-item">
                  <CheckCircle size={11} strokeWidth={2} />
                  {t}
                </span>
              ))}
            </div>

          </div>
        </div>
      </div>

      <style>{`
        /* Fonts are loaded once in index.html — see the note there. */
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

        /* ── Navbar ── */
        .lp__nav {
          height: 56px;
          background: #fff;
          border-bottom: 1px solid #e2e8f0;
          display: flex;
          align-items: center;
          padding: 0 28px;
          gap: 8px;
          position: sticky;
          top: 0;
          z-index: 20;
        }
        .lp__nav-logo {
          font-family: "Public Sans", sans-serif;
          font-size: 17px;
          font-weight: 700;
          color: #002a58;
          letter-spacing: -0.02em;
          background: none;
          border: none;
          cursor: pointer;
          padding: 0;
          flex-shrink: 0;
          margin-right: 8px;
        }
        .lp__nav-logo img { display:block; width:112px; height:24px; }
        .lp__teal { color: #00696b; }
        .lp__nav-links { display: flex; gap: 2px; }
        .lp__nav-link {
          background: none; border: none;
          font-family: "Inter", sans-serif;
          font-size: 13px; font-weight: 500;
          color: #424750; cursor: pointer;
          padding: 5px 12px; border-radius: 4px;
          transition: color 0.15s, background 0.15s;
        }
        .lp__nav-link:hover { color: #004080; background: #eff4ff; }
        .lp__nav-back {
          background: none; border: 1px solid #e2e8f0;
          font-family: "Inter", sans-serif;
          font-size: 12px; font-weight: 500;
          color: #424750; cursor: pointer;
          padding: 5px 14px; border-radius: 4px;
          transition: all 0.15s;
        }
        .lp__nav-back:hover { border-color: #004080; color: #004080; background: #eff4ff; }

        /* ── Main layout ── */
        .lp {
          display: grid;
          grid-template-columns: 55% 45%;
          min-height: calc(100vh - 56px);
          font-family: "Inter", -apple-system, sans-serif;
        }

        /* ── Left panel ── */
        .lp__left {
          background: #002a58;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 56px 64px;
          position: relative;
          overflow: hidden;
        }
        .lp__grid-bg {
          position: absolute; inset: 0;
          background-image:
            linear-gradient(rgba(255,255,255,0.04) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255,255,255,0.04) 1px, transparent 1px);
          background-size: 40px 40px;
          pointer-events: none;
        }
        .lp__left-inner {
          max-width: 460px;
          width: 100%;
          position: relative;
          z-index: 1;
        }
        .lp__brand { display: flex; align-items: center; gap: 12px; margin-bottom: 40px; }
        .lp__brand-logo {
          font-family: "Public Sans", sans-serif;
          font-size: 22px; font-weight: 700;
          color: #fff; letter-spacing: -0.02em;
        }
        .lp__brand-logo img { display:block; width:144px; height:31px; }
        .lp__brand-badge {
          font-size: 10px; font-weight: 600;
          color: rgba(255,255,255,0.45);
          text-transform: uppercase; letter-spacing: 0.07em;
          padding: 3px 8px;
          border: 1px solid rgba(255,255,255,0.12);
          border-radius: 4px;
        }
        .lp__left-headline {
          font-family: "Public Sans", sans-serif;
          font-size: clamp(24px, 2.5vw, 32px);
          font-weight: 700; color: #fff;
          letter-spacing: -0.02em;
          line-height: 1.25;
          margin-bottom: 16px;
        }
        .lp__teal-text { color: #5cd8da; }
        .lp__left-sub {
          font-size: 14px;
          color: rgba(255,255,255,0.75);
          line-height: 1.7;
          margin-bottom: 40px;
        }

        .lp__feat-list { display: flex; flex-direction: column; gap: 12px; margin-bottom: 40px; }
        .lp__feat {
          display: flex; align-items: flex-start; gap: 14px;
          padding: 14px 16px;
          background: rgba(255,255,255,0.05);
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 6px;
          transition: background 0.15s;
        }
        .lp__feat:hover { background: rgba(255,255,255,0.08); }
        .lp__feat-icon {
          width: 32px; height: 32px; border-radius: 4px; flex-shrink: 0;
          background: rgba(92,216,218,0.12);
          border: 1px solid rgba(92,216,218,0.2);
          display: flex; align-items: center; justify-content: center;
          color: #5cd8da;
          margin-top: 1px;
        }
        .lp__feat-label { font-size: 13px; font-weight: 600; color: rgba(255,255,255,0.9); margin-bottom: 2px; }
        .lp__feat-sub   { font-size: 11px; color: rgba(255,255,255,0.72); }

        .lp__stats {
          display: grid; grid-template-columns: repeat(3, 1fr);
          gap: 1px; background: rgba(255,255,255,0.08);
          border-radius: 6px; overflow: hidden;
          border: 1px solid rgba(255,255,255,0.08);
        }
        .lp__stat {
          background: rgba(0,42,88,0.6);
          padding: 16px; text-align: center;
        }
        .lp__stat-val {
          font-family: "Public Sans", sans-serif;
          font-size: 20px; font-weight: 700;
          color: #fff; margin-bottom: 3px;
        }
        .lp__stat-lbl { font-size: 11px; color: rgba(255,255,255,0.72); }

        /* ── Right panel ── */
        .lp__right {
          background: #f8f9ff;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 56px 48px;
          border-left: 1px solid #e2e8f0;
        }
        .lp__form-wrap { max-width: 380px; width: 100%; }

        .lp__mobile-logo {
          display: none;
          font-family: "Public Sans", sans-serif;
          font-size: 20px; font-weight: 700;
          color: #002a58; letter-spacing: -0.02em;
          margin-bottom: 28px;
        }
        .lp__mobile-logo img { display:block; width:130px; height:28px; }

        .lp__form-header {
          display: flex; align-items: flex-start; gap: 14px;
          margin-bottom: 32px;
        }
        .lp__form-icon {
          width: 44px; height: 44px; flex-shrink: 0;
          background: #eff4ff;
          border: 1px solid #c3c6d2;
          border-radius: 8px;
          display: flex; align-items: center; justify-content: center;
          color: #004080;
          margin-top: 2px;
        }
        .lp__title {
          font-family: "Public Sans", sans-serif;
          font-size: 22px; font-weight: 700;
          color: #0b1c30; letter-spacing: -0.02em;
          margin-bottom: 4px;
        }
        .lp__subtitle { font-size: 13px; color: #737781; line-height: 1.5; }

        .lp__form { display: flex; flex-direction: column; gap: 16px; }
        .lp__field { display: flex; flex-direction: column; gap: 6px; }
        .lp__lbl {
          font-size: 12px; font-weight: 600;
          color: #424750; letter-spacing: 0.02em;
        }
        .lp__inp {
          padding: 10px 14px;
          border: 1px solid #e2e8f0; border-radius: 4px;
          font-size: 14px; color: #0b1c30; background: #fff;
          outline: none; font-family: inherit;
          transition: border-color 0.12s, box-shadow 0.12s;
          width: 100%;
        }
        .lp__inp:hover { border-color: #c3c6d2; }
        .lp__inp:focus {
          border-color: #004080;
          box-shadow: 0 0 0 3px rgba(0,64,128,0.1);
        }
        .lp__inp--pw { padding-right: 42px; }
        .lp__pw-wrap { position: relative; }
        .lp__eye {
          position: absolute; right: 12px; top: 50%; transform: translateY(-50%);
          background: none; border: none; color: #737781; cursor: pointer;
          display: flex; align-items: center; padding: 0;
        }
        .lp__eye:hover { color: #0b1c30; }

        .lp__err {
          display: flex; align-items: center; gap: 8px;
          font-size: 13px; color: #991b1b;
          background: #fff5f5; border: 1px solid #fecaca;
          border-radius: 4px; padding: 10px 12px;
        }

        .lp__btn {
          display: flex; align-items: center; justify-content: center; gap: 8px;
          padding: 12px 20px; background: #004080; color: #fff;
          border: none; border-radius: 4px;
          font-size: 14px; font-weight: 600; cursor: pointer;
          font-family: inherit; transition: background 0.15s, transform 0.1s;
          margin-top: 4px; width: 100%;
        }
        .lp__btn:hover:not(:disabled) { background: #002a58; transform: translateY(-1px); }
        .lp__btn:active:not(:disabled) { transform: translateY(0); }
        .lp__btn:disabled { opacity: 0.5; cursor: not-allowed; }

        /* ── SSO block ──
           Google and Microsoft both require their logo in its own colours on a
           light surface, so these cannot wear the navy primary fill — and being
           secondary is right anyway, since it keeps the password path the
           obvious default. The two brands' specs differ only in their exact
           greys; one shared border and text colour is used so the pair doesn't
           read as two unrelated buttons. */
        .lp__divider {
          display: flex; align-items: center; gap: 10px;
          margin: 18px 0 14px;
          font-size: 12px; color: #9096a1;
        }
        .lp__divider::before, .lp__divider::after {
          content: ''; flex: 1; height: 1px; background: #e2e8f0;
        }
        .lp__sso-row { display: flex; flex-direction: column; gap: 8px; }
        .lp__btn--sso {
          background: #fff; color: #1f1f1f;
          border: 1px solid #dadce0; font-weight: 500;
          margin-top: 0;
          transition: background 0.15s, border-color 0.15s, box-shadow 0.15s;
        }
        .lp__btn--sso:hover:not(:disabled) {
          background: #f7f9fc; border-color: #c3c9d2;
          box-shadow: 0 1px 2px rgba(11, 28, 48, 0.06);
          transform: none;
        }
        .lp__btn--sso:disabled { opacity: 0.55; }
        .lp__btn--sso svg { flex-shrink: 0; }

        @keyframes lpSpin { to { transform: rotate(360deg); } }
        .lp__spin {
          width: 14px; height: 14px; border-radius: 50%;
          border: 2px solid rgba(255,255,255,0.3); border-top-color: #fff;
          animation: lpSpin 0.7s linear infinite;
        }

        .lp__link {
          background: none; border: none; color: #004080;
          font-size: 12px; font-weight: 500; cursor: pointer;
          padding: 0; text-align: center; font-family: inherit;
        }
        .lp__link:hover { text-decoration: underline; }
        .lp__ok {
          font-size: 13px; color: #166534;
          background: #f0fdf4; border: 1px solid #bbf7d0;
          border-radius: 4px; padding: 10px 12px;
        }

        .lp__demo {
          margin-top: 16px; padding: 12px;
          background: #eff4ff; border: 1px solid #c3d4f0; border-radius: 6px;
          display: flex; flex-direction: column; gap: 6px;
        }
        .lp__demo-title {
          font-size: 11px; font-weight: 600; color: #424750;
          text-transform: uppercase; letter-spacing: 0.06em;
        }
        .lp__demo-row {
          display: flex; align-items: center; gap: 10px;
          background: #fff; border: 1px solid #dbe3f0; border-radius: 4px;
          padding: 8px 10px; cursor: pointer; text-align: left;
          font-family: inherit; font-size: 12px; color: #0b1c30;
          transition: border-color 0.15s, background 0.15s;
        }
        .lp__demo-row:hover { border-color: #004080; background: #f7f9ff; }
        .lp__demo-tag {
          flex-shrink: 0; min-width: 92px;
          font-size: 11px; font-weight: 600; color: #00696b;
        }
        .lp__demo-cred code {
          font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          font-size: 12px; color: #0b1c30;
        }

        .lp__trust {
          display: flex; align-items: center; gap: 16px;
          margin-top: 16px; padding-top: 16px;
          border-top: 1px solid #e2e8f0;
        }
        .lp__trust-item {
          display: flex; align-items: center; gap: 5px;
          font-size: 11px; color: #737781; font-family: "Inter", sans-serif;
        }
        .lp__trust-item svg { color: #10B981; flex-shrink: 0; }


        @media (max-width: 820px) {
          .lp { grid-template-columns: 1fr; }
          .lp__left { display: none; }
          .lp__right { padding: 40px 24px; border-left: none; }
          .lp__mobile-logo { display: block; }
          .lp__nav-links { display: none; }
        }
      `}</style>
    </>
  )
}
