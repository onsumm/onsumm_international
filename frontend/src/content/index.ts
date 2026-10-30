import type { Article } from './types'
import { tdsOnVendorBills } from './articles/tdsOnVendorBills'

/**
 * The long-form pages, in one list.
 *
 * Adding an article here is all that is required: the route, the tab title, the
 * sitemap entry, the per-page metadata and the prerendered HTML file are all
 * derived from this array, so none of them can drift out of sync with it.
 */
export const ARTICLES: readonly Article[] = [tdsOnVendorBills]

export const ARTICLE_PATHS: readonly string[] = ARTICLES.map(a => a.path)

export function articleFor(pathname: string): Article | undefined {
  return ARTICLES.find(a => a.path === pathname)
}

const SITE = 'https://www.onsumm.ai'

/**
 * TechArticle + FAQPage + BreadcrumbList for one article.
 *
 * Worth being clear-eyed about what this buys. The best controlled evidence
 * available — Ahrefs' difference-in-differences test over 1,885 pages that
 * added JSON-LD against 4,000 matched controls — measured no meaningful change
 * in AI citation from adding schema (-4.6% on AI Overviews, +2.4% on AI Mode,
 * +2.2% on ChatGPT). Google says the same in its own AI-features documentation:
 * no special structured data is needed.
 *
 * It stays because it earns its place elsewhere — rich results, entity
 * disambiguation, and Bing's grounding pipeline, which feeds Copilot and a
 * large share of ChatGPT search. It is not the reason these pages will get
 * cited, and it should not be budgeted as though it were.
 */
export function articleSchema(article: Article): object[] {
  return [
    {
      '@context': 'https://schema.org',
      '@type': 'TechArticle',
      '@id': `${SITE}${article.path}#article`,
      headline: article.h1,
      description: article.description,
      url: `${SITE}${article.path}`,
      datePublished: article.updated,
      dateModified: article.updated,
      inLanguage: 'en-IN',
      author: { '@id': `${SITE}/#organization` },
      publisher: { '@id': `${SITE}/#organization` },
      about: { '@id': `${SITE}/#software` },
      articleSection: article.sections.map(s => s.heading),
    },
    {
      '@context': 'https://schema.org',
      '@type': 'FAQPage',
      '@id': `${SITE}${article.path}#faq`,
      mainEntity: article.faqs.map(({ q, a }) => ({
        '@type': 'Question',
        name: q,
        acceptedAnswer: { '@type': 'Answer', text: a },
      })),
    },
    {
      '@context': 'https://schema.org',
      '@type': 'BreadcrumbList',
      '@id': `${SITE}${article.path}#breadcrumb`,
      itemListElement: [
        { '@type': 'ListItem', position: 1, name: 'Onsumm', item: `${SITE}/` },
        { '@type': 'ListItem', position: 2, name: article.title, item: `${SITE}${article.path}` },
      ],
    },
  ]
}
