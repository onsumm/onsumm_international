import { useEffect } from 'react'
import { titleForPath } from '../routes/paths'
import { faqSchema } from './faq'
import { ARTICLES, articleSchema } from '../content'
import type { KeywordCluster } from './keywords'

/**
 * Per-route search metadata for the four public marketing pages.
 *
 * index.html carries the static half — the terms and JSON-LD that hold for the
 * whole site, which is all a crawler that doesn't run JavaScript will ever see.
 * This hook rewrites the per-route half once React is mounted: description,
 * keywords, canonical, the Open Graph pair and the page's own schema. It does
 * not touch `document.title`, which `titleForPath` in routes/paths.ts already
 * owns for every route in the app, marketing pages included.
 */

export const SITE_URL = 'https://www.onsumm.ai'

/**
 * The ceiling Google renders before it truncates a description with an ellipsis.
 * Answer engines will read a longer one, but the SERP snippet is still the first
 * thing a human sees, so the description has to land its claim inside this.
 */
export const MAX_DESCRIPTION_CHARS = 160

interface PageSeo {
  description: string
  /**
   * Clusters from keywords.ts, in descending relevance to this page.
   *
   * These no longer become a <meta name="keywords"> tag. Google has ignored
   * that tag since 2009, Bing has historically read a long one as a spam
   * signal, and Bing is what grounds Copilot and much of ChatGPT search — so it
   * was a small risk for no measured benefit. The clusters stay because they
   * are the vocabulary the page copy is written against, and seo.test.ts checks
   * the terms appear in the prose, which is the only place they do any work.
   */
  clusters: KeywordCluster[]
  /** Page-specific schema, added on top of the site-wide graph in index.html. */
  schema?: () => object[]
}

/**
 * Long-form pages describe their own metadata, so an article's description,
 * canonical and schema are derived from the same object that renders it.
 */
const ARTICLE_SEO: Record<string, PageSeo> = Object.fromEntries(
  ARTICLES.map(a => [
    a.path,
    { description: a.description, clusters: a.clusters, schema: () => articleSchema(a) } satisfies PageSeo,
  ]),
)

export const PAGE_SEO: Record<string, PageSeo> = {
  ...ARTICLE_SEO,
  '/': {
    description:
      'AI accounts payable automation for Indian SMEs — invoice OCR, approval workflows, GST and TDS compliance, and direct Tally Prime and Zoho Books posting.',
    clusters: ['apFinance', 'india', 'tally', 'zoho', 'ocr'],
    schema: () => [faqSchema()],
  },
  '/product': {
    description:
      'Invoice OCR, a multi-level approval matrix, GSTIN verification, automated TDS on vendor bills, vendor bank verification and duplicate invoice detection.',
    clusters: ['ocr', 'approval', 'gst', 'tds', 'bankVendor', 'duplicateFraud', 'tally', 'zoho'],
  },
  '/pricing': {
    description:
      'AP automation for Indian SMEs from ₹4,999 a month — invoice OCR, GST and TDS validation, approval workflows, and Tally Prime and Zoho Books integration.',
    clusters: ['apFinance', 'india', 'tally', 'zoho'],
    schema: () => [
      {
        '@context': 'https://schema.org',
        '@type': 'Product',
        '@id': `${SITE_URL}/pricing#product`,
        name: 'Onsumm AP automation',
        description:
          'Accounts payable automation software for Indian SMEs, with invoice OCR, approval workflows, GST and TDS compliance, and Tally Prime and Zoho Books integration.',
        brand: { '@type': 'Brand', name: 'Onsumm' },
        offers: [
          {
            '@type': 'Offer',
            name: 'Starter',
            price: '4999',
            priceCurrency: 'INR',
            description: 'Up to 200 invoices a month, AI invoice OCR, 2-level approval workflow, GST and TDS validation.',
            url: `${SITE_URL}/pricing`,
          },
          {
            '@type': 'Offer',
            name: 'Growth',
            price: '12999',
            priceCurrency: 'INR',
            description:
              'Up to 1,000 invoices a month, multi-level approval chains, 3-way PO matching, Tally Prime and Zoho Books sync.',
            url: `${SITE_URL}/pricing`,
          },
          {
            '@type': 'Offer',
            name: 'Enterprise',
            priceCurrency: 'INR',
            description: 'Unlimited invoices, SAP and Oracle connectors, SSO, and custom approval workflows.',
            url: `${SITE_URL}/contact`,
          },
        ],
      },
    ],
  },
  '/contact': {
    description:
      'Book a demo of Onsumm — AP automation for Indian SMEs. Ask about Tally Prime and Zoho Books integration, GST and TDS compliance, or pricing.',
    clusters: ['apFinance', 'india', 'tally', 'zoho'],
    schema: () => [
      {
        '@context': 'https://schema.org',
        '@type': 'ContactPage',
        '@id': `${SITE_URL}/contact#contactpage`,
        name: 'Contact Onsumm',
        description: 'Contact the Onsumm team about AP automation, invoice automation, Tally Prime and Zoho Books integration.',
        mainEntity: { '@id': `${SITE_URL}/#organization` },
      },
    ],
  },
}

/** Upsert a <meta> by name or property, so navigation replaces rather than stacks. */
function setMeta(attr: 'name' | 'property', key: string, content: string): void {
  let el = document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${key}"]`)
  if (!el) {
    el = document.createElement('meta')
    el.setAttribute(attr, key)
    document.head.appendChild(el)
  }
  el.content = content
}

function setCanonical(href: string): void {
  let el = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]')
  if (!el) {
    el = document.createElement('link')
    el.rel = 'canonical'
    document.head.appendChild(el)
  }
  el.href = href
}

/**
 * Replace the per-route schema.
 *
 * Tagged with data-seo-route so the site-wide graph written into index.html —
 * Organization, WebSite, SoftwareApplication — survives untouched. Without the
 * tag, moving between marketing pages either wiped it or left every visited
 * page's schema stacked on the document at once.
 */
function setRouteSchema(schemas: object[]): void {
  document.head.querySelectorAll('script[data-seo-route]').forEach(el => el.remove())
  for (const schema of schemas) {
    const el = document.createElement('script')
    el.type = 'application/ld+json'
    el.setAttribute('data-seo-route', '')
    el.textContent = JSON.stringify(schema)
    document.head.appendChild(el)
  }
}

export function useSeo(pathname: string): void {
  useEffect(() => {
    const page = PAGE_SEO[pathname]
    if (!page) return

    const url = `${SITE_URL}${pathname === '/' ? '/' : pathname}`
    // Derived, not read back off `document.title`: the effect that sets the
    // title lives in App.tsx, which is a *parent*, so its effect runs after
    // this one — reading the DOM here would tag every share card with the
    // previously visited page's title.
    const title = titleForPath(pathname)

    setMeta('name', 'description', page.description)
    setCanonical(url)

    setMeta('property', 'og:url', url)
    setMeta('property', 'og:title', title)
    setMeta('property', 'og:description', page.description)
    setMeta('name', 'twitter:title', title)
    setMeta('name', 'twitter:description', page.description)

    setRouteSchema(page.schema?.() ?? [])
  }, [pathname])
}
