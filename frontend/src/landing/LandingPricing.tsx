import { CheckCircle, ArrowRight } from 'lucide-react'

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
}

const PLANS = [
  {
    name: 'Starter',
    price: '₹4,999',
    period: '/month',
    desc: 'For small finance teams and Indian SMEs processing up to 200 vendor bills a month.',
    features: [
      'Up to 200 invoices/month',
      'AI invoice OCR & line-item extraction',
      '2-level invoice approval workflow',
      'GSTIN verification & TDS automation',
      'Email notifications',
      'CSV export',
      'Email support',
    ],
    cta: 'Start Free Trial',
    highlighted: false,
  },
  {
    name: 'Growth',
    price: '₹12,999',
    period: '/month',
    desc: 'For growing companies with complex AP approval workflows and ERP integrations.',
    features: [
      'Up to 1,000 invoices/month',
      'Everything in Starter',
      'Multi-level approval matrix',
      '3-way PO matching',
      'Tally Prime & Zoho Books integration',
      'Custom GL code mapping',
      'Duplicate payment detection',
      'Vendor bank verification',
      'Analytics dashboard',
      'Priority support',
    ],
    cta: 'Start Free Trial',
    highlighted: true,
  },
  {
    name: 'Enterprise',
    price: 'Custom',
    period: '',
    desc: 'For large organisations with high invoice volume, custom integrations, and GST and TDS compliance needs.',
    features: [
      'Unlimited invoices',
      'Everything in Growth',
      'SAP / Oracle connector',
      'Custom approval workflows',
      'Dedicated account manager',
      'SSO / SAML integration',
      'SLA guarantee',
      'On-premise option',
    ],
    cta: 'Contact Sales',
    highlighted: false,
  },
]

const FAQS = [
  { q: 'Is there a free trial?', a: 'Yes — all paid plans include a 14-day free trial. No credit card required to start.' },
  { q: 'Can I change plans later?', a: 'Absolutely. You can upgrade or downgrade at any time. Changes take effect from the next billing cycle.' },
  { q: 'What counts as an invoice?', a: 'Each document you upload and process counts as one invoice — regardless of the number of line items.' },
  { q: 'Is Tally Prime integration included?', a: 'Tally Prime and Zoho Books integration is included from the Growth plan onwards, at no extra charge. Starter covers invoice OCR, the approval workflow and GST and TDS validation.' },
  { q: 'Do you support multi-entity businesses?', a: 'Yes. Enterprise plans support multiple legal entities under a single account with separate configurations.' },
  { q: 'Is my financial data secure?', a: 'All data is encrypted at rest and in transit.' },
]

export default function LandingPricing({ onNavigate, onLogin }: Props) {
  return (
    <div style={{ background: C.bg }}>

      {/* Header */}
      <section style={{ background: C.surface, borderBottom: `1px solid ${C.border}`, padding: '56px 24px' }}>
        <div style={{ maxWidth: 720, margin: '0 auto', textAlign: 'center' }}>
          <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.08em', color: C.secondary, textTransform: 'uppercase', marginBottom: 12, fontFamily: '"Inter", sans-serif' }}>Pricing</div>
          <h1 style={{ fontSize: 'clamp(28px, 4vw, 44px)', fontWeight: 700, color: C.onSurface, letterSpacing: '-0.02em', margin: '0 0 14px', fontFamily: '"Public Sans", sans-serif' }}>
            AP automation pricing for Indian SMEs
          </h1>
          <p style={{ fontSize: 15, color: C.onSurfaceVar, lineHeight: 1.7, margin: 0, fontFamily: '"Inter", sans-serif' }}>
            Simple and transparent. No hidden fees, no per-user charges — pay for the invoices you process, with GST and TDS compliance and Tally Prime integration on every plan.
          </p>
        </div>
      </section>

      {/* Plans */}
      <section style={{ padding: '64px 24px', background: C.bg }}>
        <div style={{ maxWidth: 1100, margin: '0 auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(300px, 100%), 1fr))', gap: 24 }}>
            {PLANS.map((plan, i) => (
              <div
                key={i}
                style={{
                  background: plan.highlighted ? C.primaryCont : C.surface,
                  border: plan.highlighted ? `2px solid ${C.primaryCont}` : `1px solid ${C.border}`,
                  borderRadius: 8,
                  padding: '36px 32px',
                  position: 'relative',
                  display: 'flex',
                  flexDirection: 'column',
                }}
              >
                {plan.highlighted && (
                  <div style={{ position: 'absolute', top: -12, left: '50%', transform: 'translateX(-50%)', background: C.secondary, color: '#fff', fontSize: 10, fontWeight: 700, padding: '4px 14px', borderRadius: 4, letterSpacing: '0.06em', fontFamily: '"Inter", sans-serif', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>
                    Most Popular
                  </div>
                )}

                <div style={{ marginBottom: 24 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: plan.highlighted ? 'rgba(255,255,255,0.7)' : C.outline, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8, fontFamily: '"Inter", sans-serif' }}>{plan.name}</div>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
                    <span style={{ fontSize: 'clamp(28px,4vw,36px)', fontWeight: 700, color: plan.highlighted ? '#fff' : C.onSurface, fontFamily: '"Public Sans", sans-serif', fontVariantNumeric: 'tabular-nums' }}>{plan.price}</span>
                    {plan.period && <span style={{ fontSize: 14, color: plan.highlighted ? 'rgba(255,255,255,0.6)' : C.outline, fontFamily: '"Inter", sans-serif' }}>{plan.period}</span>}
                  </div>
                  <p style={{ fontSize: 13, color: plan.highlighted ? 'rgba(255,255,255,0.7)' : C.onSurfaceVar, lineHeight: 1.6, margin: '10px 0 0', fontFamily: '"Inter", sans-serif' }}>{plan.desc}</p>
                </div>

                <div style={{ flex: 1, marginBottom: 28 }}>
                  {plan.features.map((f, fi) => (
                    <div key={fi} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 10 }}>
                      <CheckCircle size={14} color={plan.highlighted ? '#5cd8da' : C.success} strokeWidth={2} style={{ flexShrink: 0, marginTop: 2 }} />
                      <span style={{ fontSize: 13, color: plan.highlighted ? 'rgba(255,255,255,0.85)' : C.onSurfaceVar, fontFamily: '"Inter", sans-serif', lineHeight: 1.5 }}>{f}</span>
                    </div>
                  ))}
                </div>

                <button
                  onClick={plan.cta === 'Contact Sales' ? () => onNavigate('contact') : onLogin}
                  style={{
                    background: plan.highlighted ? '#fff' : C.primaryCont,
                    color: plan.highlighted ? C.primaryCont : '#fff',
                    border: 'none', borderRadius: 4, padding: '11px', fontSize: 14,
                    fontWeight: 600, cursor: 'pointer', fontFamily: '"Inter", sans-serif',
                    width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                    transition: 'opacity 0.15s',
                  }}
                  onMouseEnter={e => { (e.currentTarget as HTMLElement).style.opacity = '0.9' }}
                  onMouseLeave={e => { (e.currentTarget as HTMLElement).style.opacity = '1' }}
                >
                  {plan.cta} <ArrowRight size={14} />
                </button>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* FAQs */}
      <section style={{ padding: '64px 24px', background: C.surface, borderTop: `1px solid ${C.border}` }}>
        <div style={{ maxWidth: 720, margin: '0 auto' }}>
          <h2 style={{ fontSize: 'clamp(22px, 3vw, 30px)', fontWeight: 600, color: C.onSurface, letterSpacing: '-0.02em', margin: '0 0 40px', textAlign: 'center', fontFamily: '"Public Sans", sans-serif' }}>
            Frequently asked questions
          </h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 1, background: C.border, borderRadius: 8, overflow: 'hidden', border: `1px solid ${C.border}` }}>
            {FAQS.map((faq, i) => (
              <div key={i} style={{ background: C.surface, padding: '24px 28px' }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: C.onSurface, marginBottom: 8, fontFamily: '"Public Sans", sans-serif' }}>{faq.q}</div>
                <p style={{ fontSize: 13, color: C.onSurfaceVar, lineHeight: 1.7, margin: 0, fontFamily: '"Inter", sans-serif' }}>{faq.a}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section style={{ background: C.primary, padding: '64px 24px' }}>
        <div style={{ maxWidth: 560, margin: '0 auto', textAlign: 'center' }}>
          <h2 style={{ fontSize: 'clamp(24px, 3.5vw, 34px)', fontWeight: 700, color: '#fff', letterSpacing: '-0.02em', margin: '0 0 14px', fontFamily: '"Public Sans", sans-serif' }}>
            Not sure which plan is right?
          </h2>
          <p style={{ fontSize: 14, color: 'rgba(255,255,255,0.65)', margin: '0 0 32px', lineHeight: 1.7, fontFamily: '"Inter", sans-serif' }}>
            Talk to our team. We'll recommend the right plan based on your invoice volume and workflow needs.
          </p>
          <button onClick={() => onNavigate('contact')} style={{ background: '#fff', color: C.primary, border: 'none', borderRadius: 4, padding: '11px 28px', fontSize: 14, fontWeight: 700, cursor: 'pointer', fontFamily: '"Inter", sans-serif' }}>
            Talk to Sales
          </button>
        </div>
      </section>
    </div>
  )
}
