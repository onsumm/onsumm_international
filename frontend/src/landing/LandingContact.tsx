import { useState } from 'react'
import { Mail, Phone, MapPin, ArrowRight } from 'lucide-react'

interface Props { onLogin: () => void }

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
  error: '#DC2626',
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  border: `1px solid ${C.border}`,
  borderRadius: 4,
  padding: '10px 14px',
  fontSize: 14,
  color: C.onSurface,
  background: C.surface,
  fontFamily: '"Inter", sans-serif',
  outline: 'none',
  transition: 'border-color 0.15s',
}

export default function LandingContact({ onLogin }: Props) {
  const [form, setForm] = useState({ name: '', email: '', company: '', message: '' })
  const [submitted, setSubmitted] = useState(false)

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitted(true)
  }

  return (
    <div style={{ background: C.bg }}>

      {/* Header */}
      <section style={{ background: C.surface, borderBottom: `1px solid ${C.border}`, padding: '56px 24px' }}>
        <div style={{ maxWidth: 1200, margin: '0 auto' }}>
          <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.08em', color: C.secondary, textTransform: 'uppercase', marginBottom: 12, fontFamily: '"Inter", sans-serif' }}>Contact</div>
          <h1 style={{ fontSize: 'clamp(28px, 4vw, 44px)', fontWeight: 700, color: C.onSurface, letterSpacing: '-0.02em', margin: '0 0 14px', fontFamily: '"Public Sans", sans-serif' }}>
            Talk to us about AP automation
          </h1>
          <p style={{ fontSize: 15, color: C.onSurfaceVar, maxWidth: 480, lineHeight: 1.7, margin: 0, fontFamily: '"Inter", sans-serif' }}>
            Book a demo, ask about Tally Prime or Zoho Books integration, GST and TDS compliance, or pricing. Our team responds within one business day.
          </p>
        </div>
      </section>

      {/* Contact grid */}
      <section style={{ padding: '64px 24px', background: C.bg }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 64 }} className="lp-stack">

          {/* Left info */}
          <div>
            <div style={{ marginBottom: 40 }}>
              <h2 style={{ fontSize: 18, fontWeight: 600, color: C.onSurface, margin: '0 0 24px', fontFamily: '"Public Sans", sans-serif' }}>Contact Information</h2>
              {[
                { icon: <Mail size={16} strokeWidth={1.5} />, label: 'Email', value: 'hello@onsumm.ai' },
                { icon: <Phone size={16} strokeWidth={1.5} />, label: 'Phone', value: '+91 98765 43210' },
                { icon: <MapPin size={16} strokeWidth={1.5} />, label: 'Address', value: 'Bengaluru, Karnataka, India' },
              ].map(item => (
                <div key={item.label} style={{ display: 'flex', alignItems: 'flex-start', gap: 14, marginBottom: 20 }}>
                  <div style={{ width: 36, height: 36, borderRadius: 4, background: C.surfaceLow, border: `1px solid #c3c6d2`, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.primaryCont, flexShrink: 0 }}>
                    {item.icon}
                  </div>
                  <div>
                    <div style={{ fontSize: 11, fontWeight: 600, color: C.outline, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 2, fontFamily: '"Inter", sans-serif' }}>{item.label}</div>
                    <div style={{ fontSize: 14, color: C.onSurface, fontFamily: '"Inter", sans-serif' }}>{item.value}</div>
                  </div>
                </div>
              ))}
            </div>

            <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: '24px' }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: C.onSurface, marginBottom: 8, fontFamily: '"Public Sans", sans-serif' }}>Already have an account?</div>
              <p style={{ fontSize: 12, color: C.onSurfaceVar, lineHeight: 1.6, margin: '0 0 14px', fontFamily: '"Inter", sans-serif' }}>Sign in to your onsumm.ai dashboard to manage invoices and approvals.</p>
              <button
                onClick={onLogin}
                style={{ background: C.primaryCont, color: '#fff', border: 'none', borderRadius: 4, padding: '9px 18px', fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: '"Inter", sans-serif', display: 'flex', alignItems: 'center', gap: 6, transition: 'background 0.15s' }}
                onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = C.primary }}
                onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = C.primaryCont }}
              >
                Sign In <ArrowRight size={13} />
              </button>
            </div>
          </div>

          {/* Right form */}
          <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: '40px' }}>
            {submitted ? (
              <div style={{ textAlign: 'center', padding: '40px 0' }}>
                <div style={{ width: 56, height: 56, borderRadius: '50%', background: '#d1fae5', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 20px', fontSize: 24 }}>✓</div>
                <h3 style={{ fontSize: 20, fontWeight: 600, color: C.onSurface, margin: '0 0 10px', fontFamily: '"Public Sans", sans-serif' }}>Message sent!</h3>
                <p style={{ fontSize: 14, color: C.onSurfaceVar, lineHeight: 1.7, margin: 0, fontFamily: '"Inter", sans-serif' }}>
                  Thanks for reaching out. Our team will get back to you within one business day.
                </p>
              </div>
            ) : (
              <form onSubmit={submit}>
                <h2 style={{ fontSize: 18, fontWeight: 600, color: C.onSurface, margin: '0 0 28px', fontFamily: '"Public Sans", sans-serif' }}>Book a Demo</h2>

                <div className="lp-stack" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
                  <div>
                    <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: C.onSurfaceVar, marginBottom: 6, fontFamily: '"Inter", sans-serif', letterSpacing: '0.02em' }}>Full Name *</label>
                    <input
                      required type="text" value={form.name} onChange={set('name')}
                      placeholder="Priya Sharma"
                      style={inputStyle}
                      onFocus={e => { e.currentTarget.style.borderColor = C.primaryCont; e.currentTarget.style.outline = `2px solid ${C.primaryCont}33` }}
                      onBlur={e => { e.currentTarget.style.borderColor = C.border; e.currentTarget.style.outline = 'none' }}
                    />
                  </div>
                  <div>
                    <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: C.onSurfaceVar, marginBottom: 6, fontFamily: '"Inter", sans-serif', letterSpacing: '0.02em' }}>Work Email *</label>
                    <input
                      required type="email" value={form.email} onChange={set('email')}
                      placeholder="priya@company.com"
                      style={inputStyle}
                      onFocus={e => { e.currentTarget.style.borderColor = C.primaryCont; e.currentTarget.style.outline = `2px solid ${C.primaryCont}33` }}
                      onBlur={e => { e.currentTarget.style.borderColor = C.border; e.currentTarget.style.outline = 'none' }}
                    />
                  </div>
                </div>

                <div style={{ marginBottom: 16 }}>
                  <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: C.onSurfaceVar, marginBottom: 6, fontFamily: '"Inter", sans-serif', letterSpacing: '0.02em' }}>Company Name</label>
                  <input
                    type="text" value={form.company} onChange={set('company')}
                    placeholder="Your company"
                    style={inputStyle}
                    onFocus={e => { e.currentTarget.style.borderColor = C.primaryCont; e.currentTarget.style.outline = `2px solid ${C.primaryCont}33` }}
                    onBlur={e => { e.currentTarget.style.borderColor = C.border; e.currentTarget.style.outline = 'none' }}
                  />
                </div>

                <div style={{ marginBottom: 28 }}>
                  <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: C.onSurfaceVar, marginBottom: 6, fontFamily: '"Inter", sans-serif', letterSpacing: '0.02em' }}>Message</label>
                  <textarea
                    value={form.message} onChange={set('message')}
                    placeholder="Tell us about your AP workflow and what you're looking to automate..."
                    rows={5}
                    style={{ ...inputStyle, resize: 'vertical', minHeight: 120 }}
                    onFocus={e => { e.currentTarget.style.borderColor = C.primaryCont; e.currentTarget.style.outline = `2px solid ${C.primaryCont}33` }}
                    onBlur={e => { e.currentTarget.style.borderColor = C.border; e.currentTarget.style.outline = 'none' }}
                  />
                </div>

                <button
                  type="submit"
                  style={{ background: C.primaryCont, color: '#fff', border: 'none', borderRadius: 4, padding: '12px 28px', fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: '"Inter", sans-serif', display: 'flex', alignItems: 'center', gap: 6, transition: 'background 0.15s' }}
                  onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = C.primary }}
                  onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = C.primaryCont }}
                >
                  Send Message <ArrowRight size={14} />
                </button>
              </form>
            )}
          </div>
        </div>
      </section>
    </div>
  )
}
