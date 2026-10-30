import { FileSearch, GitBranch, Shield, BarChart3, Zap, Clock, Globe, Database, AlertTriangle, Eye, RefreshCw, Lock, CheckSquare, Upload, ArrowRight, Landmark, Copy, UserCheck, Fingerprint } from 'lucide-react'

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
}

const SECTIONS = [
  {
    id: 'invoice-ocr',
    badge: 'INVOICE OCR',
    badgeColor: C.primaryCont,
    headline: 'AI invoice OCR that reads Indian bills exactly',
    sub: 'Our invoice OCR engine is trained on lakhs of Indian invoices — GST invoices, credit notes, debit notes, proforma, PO-based bills. Invoice data extraction runs off the document text layer first and falls back to vision only for scans, which is why GSTINs and bank details come through exact rather than close.',
    features: [
      { icon: <FileSearch size={16} strokeWidth={1.5} />, title: 'Multi-format support', desc: 'PDF, JPG, PNG, TIFF, scanned documents — invoice scanning and digitization for any format your vendors use.' },
      { icon: <Globe size={16} strokeWidth={1.5} />, title: 'Indian invoice formats', desc: 'GST invoice, bill of supply, credit note, debit note, e-invoice — all supported.' },
      { icon: <Database size={16} strokeWidth={1.5} />, title: 'Line-item extraction', desc: 'Pulls each line item with HSN code, quantity, rate, GST rate, and amount.' },
      { icon: <AlertTriangle size={16} strokeWidth={1.5} />, title: 'Confidence scoring', desc: 'Flags fields with low confidence for human review. Nothing slips through undetected.' },
    ],
  },
  {
    id: 'approval-workflows',
    badge: 'APPROVAL WORKFLOWS',
    badgeColor: C.secondary,
    headline: 'Invoice approval workflows built around how Indian teams actually work',
    sub: 'A configurable multi-level approval matrix with role-based access, covering vendor bill approval and purchase invoice approval alike. Every person sees exactly what they need to — nothing more.',
    features: [
      { icon: <GitBranch size={16} strokeWidth={1.5} />, title: 'Multi-level approval', desc: 'Member → Approver → Finance Controller → Admin. Configure the approval matrix per company.' },
      { icon: <Eye size={16} strokeWidth={1.5} />, title: 'Role-based visibility', desc: 'Accountants see GL codes, approvers see amounts, auditors see everything read-only.' },
      { icon: <Zap size={16} strokeWidth={1.5} />, title: 'Threshold-based routing', desc: 'Auto-escalate invoices above ₹1L to Finance Controller. Set any threshold.' },
      { icon: <Clock size={16} strokeWidth={1.5} />, title: 'SLA tracking', desc: 'Set approval deadlines. Auto-escalate overdue items. No invoice left pending.' },
    ],
  },
  {
    id: 'gst-tds-compliance',
    badge: 'GST & TDS COMPLIANCE',
    badgeColor: '#059669',
    headline: 'GST compliance, TDS automation and audit trail — built in',
    sub: 'onsumm.ai was designed from the ground up for India. Not retrofitted. GST automation, GSTIN verification and TDS on vendor bills are first-class, and every edge case in Indian taxation is handled.',
    features: [
      { icon: <Shield size={16} strokeWidth={1.5} />, title: 'GSTIN verification', desc: 'GSTIN validation against the GST portal in real time, plus GST invoice validation against the GSTIN the bill claims.' },
      { icon: <CheckSquare size={16} strokeWidth={1.5} />, title: 'Automated TDS calculation', desc: 'TDS deduction automation at source by vendor type, section and PAN status, with TDS tracking through to the quarterly return.' },
      { icon: <Lock size={16} strokeWidth={1.5} />, title: 'Immutable audit trail', desc: 'Every action is locked and timestamped. Perfect for CA audits and statutory requirements.' },
      { icon: <BarChart3 size={16} strokeWidth={1.5} />, title: 'GST reconciliation', desc: 'Export GSTR-2A and ITC reconciliation, TDS summaries, and vendor ageing in one click — input tax credit validated against what vendors actually filed.' },
    ],
  },
  {
    id: 'vendor-bank-verification',
    badge: 'VENDOR & PAYMENT CONTROLS',
    badgeColor: '#b91c1c',
    headline: 'Vendor bank verification and duplicate payment detection',
    sub: 'The two ways money actually leaves an AP function by mistake: paying the same bill twice, and paying the right amount into the wrong account. Both are checked automatically, before the payment run rather than in next quarter’s reconciliation.',
    features: [
      { icon: <Landmark size={16} strokeWidth={1.5} />, title: 'Bank account verification', desc: 'Vendor bank account verification confirms the account belongs to the vendor before the first payment. Bank account validation runs on every new account added.' },
      { icon: <Fingerprint size={16} strokeWidth={1.5} />, title: 'Vendor bank change detection', desc: 'Flags the moment a vendor’s bank details change — the point at which payment fraud almost always enters.' },
      { icon: <Copy size={16} strokeWidth={1.5} />, title: 'Duplicate invoice detection', desc: 'Duplicate bill detection on vendor, invoice number, amount and date, plus the same document arriving by a second route. Runs before approval.' },
      { icon: <UserCheck size={16} strokeWidth={1.5} />, title: 'Vendor master verification', desc: 'Vendor verification and supplier verification against GSTIN, PAN and bank records, so the vendor master stays clean as it grows.' },
    ],
  },
  {
    id: 'integrations',
    badge: 'INTEGRATIONS',
    badgeColor: '#d97706',
    headline: 'Tally Prime and Zoho Books integration, out of the box',
    sub: 'onsumm.ai integrates with the tools Indian finance teams already use — no rip-and-replace required. Approved bills post themselves, so the automation ends in your books rather than in another export.',
    features: [
      { icon: <RefreshCw size={16} strokeWidth={1.5} />, title: 'Tally Prime integration', desc: 'The Tally connector syncs bi-directionally with Tally Prime and Tally ERP 9. Direct Tally posting maps each approved bill to your existing ledgers and voucher types.' },
      { icon: <Upload size={16} strokeWidth={1.5} />, title: 'SAP & Oracle', desc: 'Enterprise-grade connectors for SAP S/4HANA, Oracle Fusion, and NetSuite.' },
      { icon: <Database size={16} strokeWidth={1.5} />, title: 'Zoho Books integration', desc: 'Native Zoho Books AP automation, and Tally Prime + Zoho Books side by side for teams mid-migration. QuickBooks too.' },
      { icon: <Globe size={16} strokeWidth={1.5} />, title: 'Open API', desc: 'REST API for custom integrations. Connect any system your business already uses.' },
    ],
  },
]

export default function LandingFeatures({ onNavigate, onLogin }: Props) {
  return (
    <div style={{ background: C.bg }}>

      {/* Page header */}
      <section style={{ background: C.surface, borderBottom: `1px solid ${C.border}`, padding: '56px 24px' }}>
        <div style={{ maxWidth: 1200, margin: '0 auto' }}>
          <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.08em', color: C.secondary, textTransform: 'uppercase', marginBottom: 12, fontFamily: '"Inter", sans-serif' }}>Product</div>
          <h1 style={{ fontSize: 'clamp(28px, 4vw, 44px)', fontWeight: 700, color: C.onSurface, letterSpacing: '-0.02em', margin: '0 0 16px', fontFamily: '"Public Sans", sans-serif' }}>
            Accounts payable software for Indian finance teams
          </h1>
          <p style={{ fontSize: 15, color: C.onSurfaceVar, maxWidth: 560, lineHeight: 1.7, margin: '0 0 32px', fontFamily: '"Inter", sans-serif' }}>
            Invoice OCR, multi-level approval workflows, GST and TDS compliance, vendor bank verification, duplicate payment detection, and direct Tally Prime and Zoho Books posting — purpose-built for Indian compliance, workflows and ERP systems.
          </p>
          <button
            onClick={onLogin}
            style={{ background: C.primaryCont, color: '#fff', border: 'none', borderRadius: 4, padding: '11px 24px', fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: '"Inter", sans-serif', display: 'inline-flex', alignItems: 'center', gap: 6, transition: 'background 0.15s' }}
            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = C.primary }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = C.primaryCont }}
          >
            Get a Demo <ArrowRight size={14} />
          </button>
        </div>
      </section>

      {/* Feature sections */}
      {SECTIONS.map((section, si) => (
        <section key={si} id={section.id} style={{ padding: '72px 24px', background: si % 2 === 0 ? C.bg : C.surface, borderBottom: `1px solid ${C.border}`, scrollMarginTop: 80 }}>
          <div style={{ maxWidth: 1200, margin: '0 auto' }}>
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, background: C.surfaceLow, border: `1px solid #c3c6d2`, borderRadius: 4, padding: '4px 10px', marginBottom: 20 }}>
              <span style={{ fontSize: 10, fontWeight: 700, color: section.badgeColor, letterSpacing: '0.06em', fontFamily: '"Inter", sans-serif' }}>{section.badge}</span>
            </div>
            <div className="lp-stack" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 64, alignItems: 'start' }}>
              <div>
                <h2 style={{ fontSize: 'clamp(22px, 3vw, 30px)', fontWeight: 600, color: C.onSurface, letterSpacing: '-0.02em', margin: '0 0 14px', fontFamily: '"Public Sans", sans-serif' }}>
                  {section.headline}
                </h2>
                <p style={{ fontSize: 14, color: C.onSurfaceVar, lineHeight: 1.75, margin: 0, fontFamily: '"Inter", sans-serif' }}>
                  {section.sub}
                </p>
              </div>
              <div className="lp-stack-sm" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                {section.features.map((f, fi) => (
                  <div key={fi} style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 4, padding: '20px' }}>
                    <div style={{ color: section.badgeColor, marginBottom: 10 }}>{f.icon}</div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: C.onSurface, margin: '0 0 6px', fontFamily: '"Public Sans", sans-serif' }}>{f.title}</div>
                    <p style={{ fontSize: 12, color: C.onSurfaceVar, lineHeight: 1.65, margin: 0, fontFamily: '"Inter", sans-serif' }}>{f.desc}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>
      ))}

      {/* CTA */}
      <section style={{ background: C.primaryCont, padding: '64px 24px' }}>
        <div style={{ maxWidth: 600, margin: '0 auto', textAlign: 'center' }}>
          <h2 style={{ fontSize: 'clamp(24px, 3.5vw, 36px)', fontWeight: 700, color: '#fff', letterSpacing: '-0.02em', margin: '0 0 14px', fontFamily: '"Public Sans", sans-serif' }}>
            Ready to see it in action?
          </h2>
          <p style={{ fontSize: 14, color: 'rgba(255,255,255,0.75)', margin: '0 0 32px', lineHeight: 1.7, fontFamily: '"Inter", sans-serif' }}>
            Book a personalised demo with our team. We'll walk through your specific accounts payable workflow — including your Tally Prime or Zoho Books setup — and show you exactly how onsumm.ai fits in.
          </p>
          <div style={{ display: 'flex', gap: 12, justifyContent: 'center', flexWrap: 'wrap' }}>
            <button onClick={onLogin} style={{ background: '#fff', color: C.primaryCont, border: 'none', borderRadius: 4, padding: '11px 28px', fontSize: 14, fontWeight: 700, cursor: 'pointer', fontFamily: '"Inter", sans-serif' }}>
              Book a Demo
            </button>
            <button onClick={() => onNavigate('pricing')} style={{ background: 'transparent', color: '#fff', border: '1px solid rgba(255,255,255,0.4)', borderRadius: 4, padding: '11px 28px', fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: '"Inter", sans-serif' }}>
              View Pricing
            </button>
          </div>
        </div>
      </section>
    </div>
  )
}
