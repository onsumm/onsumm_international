import { FileSearch, GitBranch, Shield, BarChart3, Zap, Clock, CheckCircle, ArrowRight, Landmark, Copy } from 'lucide-react'
import { FAQS } from '../seo/faq'

interface Props { onNavigate: (p: string) => void; onLogin: () => void }

const C = {
  bg: '#f8f9ff',
  surface: '#ffffff',
  primary: '#002a58',
  primaryCont: '#004080',
  secondary: '#00696b',
  onSurface: '#0b1c30',
  onSurfaceVar: '#424750',
  outline: '#737781',
  border: '#e2e8f0',
  surfaceLow: '#eff4ff',
  success: '#10B981',
  warning: '#F59E0B',
  error: '#DC2626',
}

// Each card owns one of the search clusters in src/seo/keywords.ts, and says
// what the product does in the words a finance lead would search for. Vendor
// bank verification and duplicate payment detection are cards in their own
// right rather than a clause inside "compliance" — they are the two things
// buyers arrive looking for, and they were previously unsayable on this page.
const FEATURES = [
  {
    icon: <FileSearch size={20} strokeWidth={1.5} />,
    title: 'AI Invoice OCR',
    desc: 'Invoice data extraction from any format — PDF, scan, image. The text layer is read first, so GSTINs and invoice numbers come through exact, not approximate.',
  },
  {
    icon: <GitBranch size={20} strokeWidth={1.5} />,
    title: '3-Way Matching',
    desc: 'Every purchase invoice cross-referenced against its PO and goods receipt note before approval. Zero manual effort.',
  },
  {
    icon: <Zap size={20} strokeWidth={1.5} />,
    title: 'Multi-Level Approval Workflows',
    desc: 'An invoice approval workflow that follows your approval matrix — by department, amount threshold, or vendor category. Fully configurable.',
  },
  {
    icon: <Shield size={20} strokeWidth={1.5} />,
    title: 'GSTIN Verification & TDS Automation',
    desc: 'GSTIN validation on every vendor bill, automated TDS calculation by section and PAN status, and ITC reconciliation against what vendors actually filed.',
  },
  {
    icon: <Landmark size={20} strokeWidth={1.5} />,
    title: 'Vendor Bank Verification',
    desc: 'Vendor bank account verification before the first payment, and vendor bank change detection every time those details move afterwards.',
  },
  {
    icon: <Copy size={20} strokeWidth={1.5} />,
    title: 'Duplicate Payment Detection',
    desc: 'Duplicate invoice detection before approval and duplicate payment detection before money moves — the two points a re-sent bill slips through.',
  },
  {
    icon: <BarChart3 size={20} strokeWidth={1.5} />,
    title: 'Tally Prime & Zoho Books Integration',
    desc: 'Direct Tally posting into your existing ledgers and voucher types, plus native Zoho Books integration. Your books stay in lockstep.',
  },
  {
    icon: <Clock size={20} strokeWidth={1.5} />,
    title: 'Audit Trail',
    desc: 'Every action immutably logged with timestamps and user attribution. Always auditor-ready.',
  },
]

const STATS = [
  { value: '95%', label: 'Less manual invoice processing' },
  { value: '3×', label: 'Faster invoice approval cycles' },
  { value: '₹0', label: 'Duplicate payments and late-payment penalties' },
  { value: '100%', label: 'Audit-ready GST and TDS compliance' },
]


export default function LandingHome({ onNavigate, onLogin }: Props) {
  return (
    <div style={{ background: C.bg }}>

      {/* ── Hero ─────────────────────────────────────────────── */}
      <section style={{ background: C.surface, borderBottom: `1px solid ${C.border}`, padding: '80px 24px 0' }}>
        <div className="lp-stack" style={{ maxWidth: 1200, margin: '0 auto', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 64, alignItems: 'center' }}>
          {/* Left */}
          <div className="lp-hero-copy" style={{ paddingBottom: 80 }}>
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, background: C.surfaceLow, border: `1px solid #c3c6d2`, borderRadius: 4, padding: '5px 12px', marginBottom: 24 }}>
              <div style={{ width: 6, height: 6, borderRadius: '50%', background: C.secondary }} />
              <span style={{ fontSize: 11, fontWeight: 600, color: C.secondary, letterSpacing: '0.06em', fontFamily: '"Inter", sans-serif', textTransform: 'uppercase' }}>AI AP Automation for Indian SMEs</span>
            </div>

            <h1 style={{ fontSize: 'clamp(36px, 4vw, 52px)', fontWeight: 700, color: C.onSurface, lineHeight: 1.1, letterSpacing: '-0.02em', margin: '0 0 20px', fontFamily: '"Public Sans", sans-serif' }}>
              AP automation for{' '}
              <span style={{ color: C.primaryCont }}>Indian SMEs</span>
            </h1>

            <p style={{ fontSize: 16, color: C.onSurfaceVar, lineHeight: 1.7, maxWidth: 480, margin: '0 0 36px', fontFamily: '"Inter", sans-serif' }}>
              Accounts payable automation built for India, not adapted for it. AI invoice OCR reads every vendor bill, approval workflows route it, GST and TDS checks clear it — and the approved bill posts straight into Tally Prime or Zoho Books.
            </p>

            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 36 }}>
              <button
                onClick={onLogin}
                style={{ background: C.primaryCont, color: '#fff', border: 'none', borderRadius: 4, padding: '11px 24px', fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: '"Inter", sans-serif', transition: 'background 0.15s' }}
                onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = C.primary }}
                onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = C.primaryCont }}
              >
                Start Free Trial
              </button>
              <button
                onClick={onLogin}
                style={{ background: 'transparent', color: C.primaryCont, border: `1px solid ${C.primaryCont}`, borderRadius: 4, padding: '11px 24px', fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: '"Inter", sans-serif', display: 'flex', alignItems: 'center', gap: 6, transition: 'background 0.15s' }}
                onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = C.surfaceLow }}
                onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
              >
                ▶ Watch Demo
              </button>
            </div>

            <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
              {['No credit card required', '14-day free trial', 'Tally Prime & Zoho Books ready'].map(item => (
                <div key={item} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <CheckCircle size={13} color={C.success} strokeWidth={2} />
                  <span style={{ fontSize: 12, color: C.outline, fontFamily: '"Inter", sans-serif' }}>{item}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Right — Dashboard mockup */}
          <div style={{ paddingBottom: 40 }}>
            <div style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 8, overflow: 'hidden', boxShadow: '0 4px 20px rgba(0,0,0,0.05)' }}>
              {/* Mockup header */}
              <div style={{ background: C.primary, padding: '12px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: '#fff', fontFamily: '"Inter", sans-serif' }}>AP Dashboard</span>
                <div style={{ display: 'flex', gap: 6 }}>
                  {['Invoices', 'Matching', 'Reports'].map(t => (
                    <span key={t} style={{ fontSize: 10, color: 'rgba(255,255,255,0.6)', fontFamily: '"Inter", sans-serif', padding: '2px 8px' }}>{t}</span>
                  ))}
                </div>
              </div>

              {/* Stats row */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 1, background: C.border }}>
                {[
                  { label: 'Invoices Scanned', val: '1,248', color: C.primaryCont },
                  { label: 'Matched', val: '1,180', color: C.success },
                  { label: 'Exceptions', val: '68', color: C.warning },
                ].map(s => (
                  <div key={s.label} style={{ background: C.surface, padding: '16px', textAlign: 'center' }}>
                    <div style={{ fontSize: 22, fontWeight: 700, color: s.color, fontFamily: '"Inter", sans-serif', fontVariantNumeric: 'tabular-nums' }}>{s.val}</div>
                    <div style={{ fontSize: 11, color: C.outline, marginTop: 2, fontFamily: '"Inter", sans-serif' }}>{s.label}</div>
                  </div>
                ))}
              </div>

              {/* Recent invoices */}
              <div style={{ padding: '16px' }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: C.outline, letterSpacing: '0.05em', textTransform: 'uppercase', marginBottom: 12, fontFamily: '"Inter", sans-serif' }}>Recent Invoices</div>
                {[
                  { vendor: 'Razorpay Technologies', amount: '₹2,35,000', status: 'Approved', statusColor: C.success, statusBg: '#d1fae5', statusText: '#065f46' },
                  { vendor: 'AWS India Pvt Ltd', amount: '₹1,18,450', status: 'Pending', statusColor: C.warning, statusBg: '#fef3c7', statusText: '#92400e' },
                  { vendor: 'Freshworks Inc', amount: '₹84,700', status: 'Flagged', statusColor: C.error, statusBg: '#fee2e2', statusText: '#991b1b' },
                  { vendor: 'Airtel Business', amount: '₹42,300', status: 'Approved', statusColor: C.success, statusBg: '#d1fae5', statusText: '#065f46' },
                ].map((row, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '9px 0', borderBottom: i < 3 ? `1px solid ${C.border}` : 'none' }}>
                    <span style={{ fontSize: 12, color: C.onSurface, fontFamily: '"Inter", sans-serif', flex: 1 }}>{row.vendor}</span>
                    <span style={{ fontSize: 12, fontWeight: 600, color: C.onSurface, fontFamily: '"Inter", sans-serif', fontVariantNumeric: 'tabular-nums', marginRight: 12 }}>{row.amount}</span>
                    <span style={{ fontSize: 10, fontWeight: 600, padding: '3px 8px', borderRadius: 4, background: row.statusBg, color: row.statusText, fontFamily: '"Inter", sans-serif' }}>{row.status}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Features ──────────────────────────────────────────── */}
      <section style={{ padding: '80px 24px', background: C.bg }}>
        <div style={{ maxWidth: 1200, margin: '0 auto' }}>
          <div style={{ textAlign: 'center', marginBottom: 56 }}>
            <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.08em', color: C.secondary, textTransform: 'uppercase', marginBottom: 12, fontFamily: '"Inter", sans-serif' }}>Capabilities</div>
            <h2 style={{ fontSize: 'clamp(26px, 3.5vw, 36px)', fontWeight: 600, color: C.onSurface, letterSpacing: '-0.02em', margin: '0 0 14px', fontFamily: '"Public Sans", sans-serif' }}>
              Everything AP automation should do
            </h2>
            <p style={{ fontSize: 15, color: C.onSurfaceVar, maxWidth: 520, margin: '0 auto', lineHeight: 1.7, fontFamily: '"Inter", sans-serif' }}>
              Invoice OCR, approval workflows, GST and TDS compliance, vendor bank verification and duplicate payment detection — one system, from the bill arriving to the payment going out.
            </p>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(320px, 100%), 1fr))', gap: 1, background: C.border, borderRadius: 8, overflow: 'hidden', border: `1px solid ${C.border}` }}>
            {FEATURES.map((f, i) => (
              <div
                key={i}
                style={{ background: C.surface, padding: '28px 32px', transition: 'background 0.15s', cursor: 'default' }}
                onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = C.surfaceLow }}
                onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = C.surface }}
              >
                <div style={{ width: 40, height: 40, borderRadius: 4, background: C.surfaceLow, border: `1px solid #c3c6d2`, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.primaryCont, marginBottom: 16 }}>
                  {f.icon}
                </div>
                <h3 style={{ fontSize: 15, fontWeight: 600, color: C.onSurface, margin: '0 0 8px', fontFamily: '"Public Sans", sans-serif' }}>{f.title}</h3>
                <p style={{ fontSize: 13, color: C.onSurfaceVar, lineHeight: 1.7, margin: 0, fontFamily: '"Inter", sans-serif' }}>{f.desc}</p>
              </div>
            ))}
          </div>

          <div style={{ textAlign: 'center', marginTop: 40 }}>
            <button
              onClick={() => onNavigate('features')}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: C.primaryCont, background: 'transparent', border: `1px solid ${C.primaryCont}`, borderRadius: 4, padding: '10px 20px', fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: '"Inter", sans-serif', transition: 'background 0.15s' }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = C.surfaceLow }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
            >
              Explore all features <ArrowRight size={14} />
            </button>
          </div>
        </div>
      </section>

      {/* ── Stats bar ─────────────────────────────────────────── */}
      <section style={{ background: C.primary, padding: '64px 24px' }}>
        <div style={{ maxWidth: 1200, margin: '0 auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 1, background: 'rgba(255,255,255,0.08)', borderRadius: 8, overflow: 'hidden' }}>
            {STATS.map((s, i) => (
              <div key={i} style={{ textAlign: 'center', padding: '40px 24px', background: C.primary }}>
                <div style={{ fontSize: 'clamp(36px, 4vw, 48px)', fontWeight: 700, color: '#fff', letterSpacing: '-0.02em', lineHeight: 1, fontFamily: '"Public Sans", sans-serif', fontVariantNumeric: 'tabular-nums' }}>{s.value}</div>
                <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.65)', marginTop: 10, fontFamily: '"Inter", sans-serif' }}>{s.label}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── FAQ ───────────────────────────────────────────────
           Rendered from src/seo/faq.ts, the same list the FAQPage schema is
           built from. Structured data has to describe copy the visitor can
           actually see, so the two cannot be allowed to drift apart — and a
           plain <details> list is what an answer engine reads cleanly. */}
      <section id="faq" style={{ padding: '80px 24px', background: C.bg }}>
        <div style={{ maxWidth: 800, margin: '0 auto' }}>
          <div style={{ textAlign: 'center', marginBottom: 48 }}>
            <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.08em', color: C.secondary, textTransform: 'uppercase', marginBottom: 12, fontFamily: '"Inter", sans-serif' }}>FAQ</div>
            <h2 style={{ fontSize: 'clamp(26px, 3.5vw, 36px)', fontWeight: 600, color: C.onSurface, letterSpacing: '-0.02em', margin: '0 0 14px', fontFamily: '"Public Sans", sans-serif' }}>
              AP automation questions, answered
            </h2>
            <p style={{ fontSize: 15, color: C.onSurfaceVar, maxWidth: 560, margin: '0 auto', lineHeight: 1.7, fontFamily: '"Inter", sans-serif' }}>
              What Indian finance teams ask us before they switch — on Tally Prime, Zoho Books, GST, TDS and duplicate payments.
            </p>
          </div>

          <div className="lp-faq" style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, overflow: 'hidden' }}>
            {FAQS.map((item, i) => (
              <details
                key={item.q}
                style={{ borderBottom: i < FAQS.length - 1 ? `1px solid ${C.border}` : 'none' }}
              >
                <summary
                  style={{ cursor: 'pointer', listStyle: 'none', padding: '18px 24px', fontSize: 14, fontWeight: 600, color: C.onSurface, fontFamily: '"Public Sans", sans-serif' }}
                >
                  {item.q}
                </summary>
                <p style={{ padding: '0 24px 20px', fontSize: 13, color: C.onSurfaceVar, lineHeight: 1.75, margin: 0, fontFamily: '"Inter", sans-serif' }}>
                  {item.a}
                </p>
              </details>
            ))}
          </div>
        </div>
      </section>

      {/* ── CTA Banner ───────────────────────────────────────── */}
      <section style={{ background: C.primaryCont, padding: '72px 24px' }}>
        <div style={{ maxWidth: 640, margin: '0 auto', textAlign: 'center' }}>
          <h2 style={{ fontSize: 'clamp(28px, 4vw, 40px)', fontWeight: 700, color: '#fff', letterSpacing: '-0.02em', margin: '0 0 16px', fontFamily: '"Public Sans", sans-serif' }}>
            Ready to automate accounts payable?
          </h2>
          <p style={{ fontSize: 15, color: 'rgba(255,255,255,0.75)', margin: '0 0 36px', lineHeight: 1.7, fontFamily: '"Inter", sans-serif' }}>
            Join hundreds of Indian finance teams who've moved from spreadsheets and email approvals to a fully automated AP workflow — with invoice OCR, a real approval matrix, and direct Tally posting.
          </p>
          <button
            onClick={onLogin}
            style={{ background: '#fff', color: C.primaryCont, border: 'none', borderRadius: 4, padding: '13px 32px', fontSize: 14, fontWeight: 700, cursor: 'pointer', fontFamily: '"Inter", sans-serif', transition: 'opacity 0.15s' }}
            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.opacity = '0.92' }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.opacity = '1' }}
          >
            Book a Demo
          </button>
          <p style={{ marginTop: 16, fontSize: 12, color: 'rgba(255,255,255,0.5)', fontFamily: '"Inter", sans-serif' }}>No credit card required · Setup in under 10 minutes</p>
        </div>
      </section>

    </div>
  )
}
