/**
 * Build-time render of the marketing pages.
 *
 * The app is a client-rendered SPA, which is fine for the authenticated half —
 * nobody indexes /bills. It is fatal for the marketing half. The answer-engine
 * crawlers that decide whether Onsumm gets named in "best AP automation for
 * Indian SMEs" answers (GPTBot, OAI-SearchBot, ClaudeBot, Claude-SearchBot,
 * PerplexityBot) read raw HTML and do not run JavaScript. Before this file,
 * every marketing route returned the same 9 KB shell, so to those crawlers the
 * site was a single page with a <noscript> paragraph on it.
 *
 * This renders each public route to static HTML at build time. The client
 * bundle still boots and takes over; the difference is only in what arrives
 * before it does.
 */
import { renderToStaticMarkup } from 'react-dom/server'
import { StaticRouter } from 'react-router'
import LandingApp from '../src/landing/LandingApp'
import { PAGE_SEO, SITE_URL } from '../src/seo/useSeo'
import { titleForPath } from '../src/routes/paths'
import { PUBLIC_PATHS } from '../src/routes/paths'
import { ARTICLES } from '../src/content'

export const ROUTES: readonly string[] = PUBLIC_PATHS

/**
 * What the sitemap needs, per URL. Articles carry their own last-updated date;
 * the four marketing pages have no edit date of their own, so they are left
 * without one rather than stamped with the build date. A lastmod that moves
 * every deploy tells a crawler nothing and, once it notices, costs credibility
 * on the dates that are real.
 */
export function sitemapEntries(): { loc: string; lastmod?: string; changefreq: string; priority: string }[] {
  const articleByPath = new Map(ARTICLES.map(a => [a.path, a]))
  const weight: Record<string, [string, string]> = {
    '/': ['weekly', '1.0'],
    '/product': ['weekly', '0.9'],
    '/pricing': ['monthly', '0.8'],
    '/contact': ['monthly', '0.6'],
  }
  return PUBLIC_PATHS.map(path => {
    const [changefreq, priority] = weight[path] ?? ['monthly', '0.8']
    return {
      loc: `${SITE_URL}${path === '/' ? '/' : path}`,
      lastmod: articleByPath.get(path)?.updated,
      changefreq,
      priority,
    }
  })
}

export interface RouteHead {
  title: string
  description: string
  canonical: string
  schemas: object[]
}

/** The head that useSeo would have written on the client, computed in Node. */
export function headFor(pathname: string): RouteHead {
  const page = PAGE_SEO[pathname]
  if (!page) throw new Error(`no PAGE_SEO entry for ${pathname}`)
  return {
    title: titleForPath(pathname),
    description: page.description,
    canonical: `${SITE_URL}${pathname === '/' ? '/' : pathname}`,
    schemas: page.schema?.() ?? [],
  }
}

/**
 * onLogin is a no-op here: the prerendered markup is a snapshot, and every
 * interactive path is re-bound the moment the client bundle mounts.
 */
export function renderRoute(pathname: string): string {
  return renderToStaticMarkup(
    <StaticRouter location={pathname}>
      <LandingApp onLogin={() => {}} />
    </StaticRouter>,
  )
}
