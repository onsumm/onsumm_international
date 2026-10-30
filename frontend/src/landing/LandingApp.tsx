import { useState, useEffect } from 'react'
import type { CSSProperties, MouseEventHandler, ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Menu, X } from 'lucide-react'
import LandingHome from './LandingHome'
import LandingFeatures from './LandingFeatures'
import LandingPricing from './LandingPricing'
import LandingContact from './LandingContact'
import { useSeo } from '../seo/useSeo'
import { articleFor } from '../content'
import ArticlePage from '../content/ArticlePage'

type Page = 'home' | 'features' | 'pricing' | 'contact'

interface Props { onLogin: () => void }

/** Marketing pages are real URLs so they can be linked, shared and indexed. */
const PAGE_PATH: Record<Page, string> = {
  home: '/', features: '/product', pricing: '/pricing', contact: '/contact',
}
const PATH_PAGE: Record<string, Page> = {
  '/': 'home', '/product': 'features', '/pricing': 'pricing', '/contact': 'contact',
}

// "Integrations" previously pointed at the same page as "Product", so both lit
// up as active at once. It now deep-links to the integrations section instead.
const NAV_LINKS: { label: string; page: Page; hash?: string }[] = [
  { label: 'Product', page: 'features' },
  { label: 'Integrations', page: 'features', hash: 'integrations' },
  { label: 'Pricing', page: 'pricing' },
  { label: 'About', page: 'contact' },
]

// Sovereign Ledger tokens
const C = {
  bg: '#f8f9ff',
  surface: '#ffffff',
  primary: '#002a58',
  primaryCont: '#004080',
  secondary: '#00696b',
  onSurface: '#0b1c30',
  onSurfaceVar: '#424750',
  outline: '#737781',
  outlineVar: '#c3c6d2',
  border: '#e2e8f0',
}

/**
 * A marketing link that is a real <a href> in the HTML and still navigates
 * client-side.
 *
 * The nav and the footer were <button onClick> throughout, so the rendered
 * marketing pages contained no outbound links at all. A crawler that landed on
 * / had no way to reach /product, /pricing or /contact — and since the sitemap
 * was being swallowed by the SPA rewrite, nothing else advertised them either.
 * The four pages also passed no signal to one another.
 *
 * A plain left click is intercepted and handled by the router. Modifier and
 * middle clicks fall through to the browser, so "open in new tab" and "copy
 * link address" behave the way they do on any other link.
 */
interface PageLinkProps {
  page: Page
  hash?: string
  onNavigate: (page: Page, hash?: string) => void
  children: ReactNode
  style?: CSSProperties
  className?: string
  'aria-current'?: 'page'
  'aria-label'?: string
  onMouseEnter?: MouseEventHandler<HTMLAnchorElement>
  onMouseLeave?: MouseEventHandler<HTMLAnchorElement>
}

function PageLink({ page, hash, onNavigate, children, ...rest }: PageLinkProps) {
  return (
    <a
      href={PAGE_PATH[page] + (hash ? `#${hash}` : '')}
      onClick={e => {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
        e.preventDefault()
        onNavigate(page, hash)
      }}
      {...rest}
    >
      {children}
    </a>
  )
}

function Navbar({ page, onNavigate, onLogin }: { page: Page | null; onNavigate: (p: Page, hash?: string) => void; onLogin: () => void }) {
  const [scrolled, setScrolled] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    const handler = () => setScrolled(window.scrollY > 8)
    window.addEventListener('scroll', handler, { passive: true })
    return () => window.removeEventListener('scroll', handler)
  }, [])

  return (
    <>
      <nav style={{
        position: 'fixed', top: 0, left: 0, right: 0, zIndex: 100,
        background: C.surface,
        borderBottom: `1px solid ${scrolled ? C.border : 'transparent'}`,
        transition: 'border-color 0.2s',
        padding: '0 16px',
      }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', height: 64, display: 'flex', alignItems: 'center', gap: 40 }}>
          {/* Logo */}
          <PageLink
            page="home"
            onNavigate={onNavigate}
            aria-label="Onsumm home"
            style={{ display: 'block', padding: 0, flexShrink: 0, lineHeight: 0 }}
          >
            <img
              src="/brand/onsumm-wordmark-dark.svg"
              alt="Onsumm — AP automation for Indian SMEs"
              style={{ display: 'block', width: 126, height: 27 }}
            />
          </PageLink>

          {/* Desktop nav */}
          <div className="lp-desktop-nav" style={{ display: 'flex', alignItems: 'center', gap: 4, flex: 1 }}>
            {NAV_LINKS.map(link => {
              // Only the plain (hash-less) link owns the active state, so two
              // links pointing at one page can't both light up.
              const active = page === link.page && !link.hash
              return (
                <PageLink
                  key={link.page + link.label}
                  page={link.page}
                  hash={link.hash}
                  onNavigate={onNavigate}
                  aria-current={active ? 'page' : undefined}
                  style={{
                    textDecoration: 'none', cursor: 'pointer',
                    padding: '6px 14px', fontSize: 14, fontWeight: 500,
                    fontFamily: '"Inter", sans-serif',
                    color: active ? C.primaryCont : C.onSurfaceVar,
                    borderRadius: 4,
                    borderBottom: active ? `2px solid ${C.primaryCont}` : '2px solid transparent',
                    transition: 'color 0.15s',
                  }}
                >
                  {link.label}
                </PageLink>
              )
            })}
          </div>

          {/* CTA buttons */}
          <div className="lp-desktop-nav" style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
            <button
              onClick={onLogin}
              style={{
                background: 'transparent', border: `1px solid ${C.primaryCont}`,
                color: C.primaryCont, borderRadius: 4, padding: '7px 18px',
                fontSize: 13, fontWeight: 600, cursor: 'pointer',
                fontFamily: '"Inter", sans-serif', transition: 'background 0.15s',
              }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = '#eff4ff' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
            >
              Sign In
            </button>
            <button
              onClick={onLogin}
              style={{
                background: C.primaryCont, color: '#fff', border: 'none',
                borderRadius: 4, padding: '8px 20px', fontSize: 13,
                fontWeight: 600, cursor: 'pointer', fontFamily: '"Inter", sans-serif',
                transition: 'background 0.15s',
              }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = '#002a58' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = C.primaryCont }}
            >
              Get a Demo
            </button>
          </div>

          {/* Mobile hamburger */}
          <button
            className="lp-hamburger"
            onClick={() => setMobileOpen(o => !o)}
            aria-label={mobileOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={mobileOpen}
            style={{ background: 'transparent', border: 'none', color: C.onSurface, cursor: 'pointer', padding: 10, margin: '0 -10px 0 auto', display: 'none', alignItems: 'center', justifyContent: 'center' }}
          >
            {mobileOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </nav>

      {/* Mobile nav */}
      {mobileOpen && (
        <div style={{ position: 'fixed', top: 64, left: 0, right: 0, bottom: 0, background: C.surface, zIndex: 99, padding: 24, display: 'flex', flexDirection: 'column', gap: 4, borderTop: `1px solid ${C.border}` }}>
          {NAV_LINKS.map(link => (
            <PageLink
              key={link.label}
              page={link.page}
              hash={link.hash}
              onNavigate={(pg, hash) => { onNavigate(pg, hash); setMobileOpen(false) }}
              style={{ textDecoration: 'none', color: C.onSurface, fontSize: 16, fontWeight: 600, cursor: 'pointer', padding: '14px 0', textAlign: 'left', fontFamily: '"Inter", sans-serif', borderBottom: `1px solid ${C.border}` }}
            >
              {link.label}
            </PageLink>
          ))}
          <div style={{ marginTop: 24, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <button onClick={() => { onLogin(); setMobileOpen(false) }} style={{ padding: '13px', background: 'transparent', border: `1px solid ${C.primaryCont}`, color: C.primaryCont, borderRadius: 4, fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: '"Inter", sans-serif' }}>
              Sign In
            </button>
            <button onClick={() => { onLogin(); setMobileOpen(false) }} style={{ padding: '13px', background: C.primaryCont, border: 'none', color: '#fff', borderRadius: 4, fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: '"Inter", sans-serif' }}>
              Get a Demo
            </button>
          </div>
        </div>
      )}
    </>
  )
}

interface FooterLink {
  label: string
  page: Page
  hash?: string
}

const FOOTER_COLUMNS: { title: string; links: FooterLink[] }[] = [
  {
    title: 'Product',
    links: [
      { label: 'Features', page: 'features' },
      { label: 'Integrations', page: 'features', hash: 'integrations' },
      { label: 'Pricing', page: 'pricing' },
    ],
  },
  { title: 'Company', links: [{ label: 'About', page: 'contact' }] },
  { title: 'Support', links: [{ label: 'Contact Us', page: 'contact' }] },
]

function Footer({ onNavigate }: { onNavigate: (p: Page, hash?: string) => void }) {
  return (
    <footer style={{ background: C.primary, padding: '56px 24px 32px' }}>
      <div style={{ maxWidth: 1200, margin: '0 auto' }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 40, marginBottom: 48 }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: '#fff', marginBottom: 12, fontFamily: '"Public Sans", sans-serif' }}>
              onsumm<span style={{ color: '#5cd8da' }}>.</span>ai
            </div>
            <p style={{ fontSize: 13, color: 'rgba(255,255,255,0.75)', lineHeight: 1.7, margin: '0 0 20px', fontFamily: '"Inter", sans-serif' }}>
              AI accounts payable automation for Indian SMEs — invoice OCR, approval workflows, GST and TDS compliance, and direct Tally Prime and Zoho Books posting.
            </p>
            {/* Real links. These were <div>s with cursor:pointer and no
                handler — they looked clickable and did nothing. */}
            <div style={{ display: 'flex', gap: 10 }}>
              {[
                { label: 'X', href: 'https://x.com/onsummai', name: 'onsumm.ai on X' },
                { label: 'in', href: 'https://www.linkedin.com/company/onsummai', name: 'onsumm.ai on LinkedIn' },
              ].map(s => (
                <a
                  key={s.label}
                  href={s.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={s.name}
                  style={{ width: 34, height: 34, borderRadius: 4, background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.12)', display: 'flex', alignItems: 'center', justifyContent: 'center', textDecoration: 'none' }}
                >
                  <span aria-hidden="true" style={{ fontSize: 12, fontWeight: 700, color: 'rgba(255,255,255,0.8)' }}>{s.label}</span>
                </a>
              ))}
            </div>
          </div>

          {/*
              Every label here has to lead somewhere it honestly describes.
              "Blog", "Careers", "Documentation", "Privacy Policy" and "Terms of
              Service" all used to point at /product or / — a crawler following
              them found the home page under five different names, and a reader
              clicking "Privacy Policy" got marketing copy. They are gone until
              the pages behind them exist.
          */}
          {FOOTER_COLUMNS.map(col => (
            <div key={col.title}>
              <div style={{ fontSize: 11, fontWeight: 700, color: 'rgba(255,255,255,0.7)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 16, fontFamily: '"Inter", sans-serif' }}>{col.title}</div>
              {col.links.map(link => (
                <PageLink key={link.label} page={link.page} hash={link.hash} onNavigate={onNavigate} style={{ display: 'block', textDecoration: 'none', padding: '5px 0', fontSize: 13, color: 'rgba(255,255,255,0.75)', cursor: 'pointer', fontFamily: '"Inter", sans-serif', textAlign: 'left', transition: 'color 0.12s' }}
                  onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = '#fff' }}
                  onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'rgba(255,255,255,0.75)' }}
                >{link.label}</PageLink>
              ))}
            </div>
          ))}
        </div>

        <div style={{ borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
          <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.7)', fontFamily: '"Inter", sans-serif' }}>© {new Date().getFullYear()} onsumm.ai. AP automation for Indian SMEs.</span>
        </div>
      </div>
    </footer>
  )
}

export default function LandingApp({ onLogin }: Props) {
  const routerNavigate = useNavigate()
  const location = useLocation()
  const article = articleFor(location.pathname)
  // null on an article: those pages are not one of the four nav destinations.
  const page: Page | null = article ? null : PATH_PAGE[location.pathname] ?? 'home'

  // Description, keywords, canonical, Open Graph and the page's own JSON-LD.
  // The tab title is not set here — titleForPath() in routes/paths.ts owns it
  // for every route, marketing pages included.
  useSeo(article ? article.path : PAGE_PATH[page ?? 'home'])

  /** Scroll behaviour shared by both navigation paths below. */
  const afterNavigate = () => {
    // Respect users who've asked the OS to reduce motion.
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    window.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' })
  }

  /** Articles are addressed by path rather than by one of the four page keys. */
  const navigateToPath = (path: string) => {
    routerNavigate(path)
    afterNavigate()
  }

  const navigate = (p: string, hash?: string) => {
    routerNavigate(PAGE_PATH[p as Page] + (hash ? `#${hash}` : ''))
    afterNavigate()
  }

  return (
    <div style={{ fontFamily: '"Inter", -apple-system, sans-serif', minHeight: '100vh', background: C.bg }}>
      <style>{`
        /* Fonts are loaded once in index.html — see the note there. */
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body { overflow-x: hidden; background: #f8f9ff; }
        @media (max-width: 768px) {
          .lp-desktop-nav { display: none !important; }
          .lp-hamburger { display: flex !important; }
          /* Two-up grids are written inline at desktop widths; on a phone each
             half needs the full column, or the right half runs off-screen. */
          .lp-stack { grid-template-columns: 1fr !important; gap: 32px !important; }
          .lp-hero-copy { padding-bottom: 0 !important; }
          section { padding-left: 16px !important; padding-right: 16px !important; }
          footer { padding-left: 16px !important; padding-right: 16px !important; }
        }
        @media (max-width: 480px) {
          .lp-stack-sm { grid-template-columns: 1fr !important; }
        }
        /* FAQ disclosures. The default triangle is suppressed so the rows read
           as part of the card, which leaves nothing saying they open — hence
           our own +/− marker. WebKit needs its pseudo-element hidden by name;
           list-style alone doesn't reach it. */
        .lp-faq summary::-webkit-details-marker { display: none; }
        .lp-faq summary { position: relative; padding-right: 48px !important; }
        .lp-faq summary::after {
          content: '+';
          position: absolute; right: 24px; top: 50%; transform: translateY(-50%);
          font-size: 18px; font-weight: 400; color: #737781; line-height: 1;
        }
        .lp-faq details[open] summary::after { content: '−'; }
        .lp-faq summary:hover { background: #eff4ff; }
      `}</style>

      <Navbar page={page} onNavigate={(p, hash) => navigate(p, hash)} onLogin={onLogin} />

      <div style={{ paddingTop: 64 }}>
        {article && <ArticlePage article={article} onNavigate={navigateToPath} />}
        {page === 'home'     && <LandingHome onNavigate={navigate} onLogin={onLogin} />}
        {page === 'features' && <LandingFeatures onNavigate={navigate} onLogin={onLogin} />}
        {page === 'pricing'  && <LandingPricing onNavigate={navigate} onLogin={onLogin} />}
        {page === 'contact'  && <LandingContact onLogin={onLogin} />}
      </div>

      <Footer onNavigate={(p, hash) => navigate(p, hash)} />
    </div>
  )
}
