/**
 * Guards the two properties the marketing pages have to keep for an answer
 * engine to see them at all. Both were broken before, and both break silently:
 * the app still works in a browser either way.
 *
 *  1. Each public route renders different content on the server. When the whole
 *     site was client-rendered, every route returned the same 9 KB shell, so a
 *     crawler that does not run JavaScript — which is most of the AI crawlers —
 *     saw one page.
 *  2. The pages link to each other with real <a href>. Navigation used to be
 *     <button onClick> throughout, which left the rendered HTML with no
 *     outbound links and nothing to crawl from.
 */
import { describe, it, expect } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { StaticRouter } from 'react-router'
import LandingApp from './LandingApp'
import { LANDING_PATHS } from '../routes/paths'

const render = (path: string) =>
  renderToStaticMarkup(
    <StaticRouter location={path}>
      <LandingApp onLogin={() => {}} />
    </StaticRouter>,
  )

const hrefs = (html: string) =>
  new Set(Array.from(html.matchAll(/href="(\/[^"]*)"/g), m => m[1].split('#')[0]))

describe('marketing pages render without JavaScript', () => {
  it.each(LANDING_PATHS)('%s renders substantial markup', path => {
    // The shell was 9 KB and said almost nothing. Anything worth indexing is
    // well past that; the threshold only has to catch a regression to a stub.
    expect(render(path).length).toBeGreaterThan(15_000)
  })

  it('gives every route its own content', () => {
    const rendered = LANDING_PATHS.map(render)
    expect(new Set(rendered).size).toBe(LANDING_PATHS.length)
  })

  it('gives every route its own h1', () => {
    const h1s = LANDING_PATHS.map(p => render(p).match(/<h1[^>]*>(.*?)<\/h1>/s)?.[1])
    expect(h1s.every(Boolean)).toBe(true)
    expect(new Set(h1s).size).toBe(LANDING_PATHS.length)
  })
})

describe('marketing pages are crawlable from one another', () => {
  it.each(LANDING_PATHS)('%s links to every other public route', path => {
    const found = hrefs(render(path))
    for (const other of LANDING_PATHS) expect(found).toContain(other)
  })

  it('uses anchors rather than buttons for navigation', () => {
    const html = render('/')
    // The nav and footer entries: four nav links, the wordmark, three footer
    // columns. The exact count will drift; that there are several will not.
    expect(Array.from(html.matchAll(/<a [^>]*href="\//g)).length).toBeGreaterThanOrEqual(8)
  })
})
