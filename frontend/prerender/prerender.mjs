/**
 * Writes one real HTML file per public marketing route.
 *
 * Runs after `vite build`. dist/index.html is the template: it already carries
 * the site-wide head and the Organization / WebSite / SoftwareApplication
 * graph. For each route this swaps in that route's own title, description,
 * keywords, canonical, social pair and schema, and drops the route's rendered
 * markup into #root.
 *
 *   /          -> dist/index.html
 *   /product   -> dist/product/index.html
 *   /pricing   -> dist/pricing/index.html
 *   /contact   -> dist/contact/index.html
 *
 * The <noscript> summary is removed from every prerendered page: it existed
 * only because #root used to be empty, and leaving it in would hand a crawler
 * the same claims twice.
 */
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const DIST = join(here, '..', 'dist')

const { ROUTES, headFor, renderRoute, sitemapEntries } = await import(join(here, '.out', 'entry-server.mjs'))

const template = await readFile(join(DIST, 'index.html'), 'utf8')

/** JSON-LD is inlined into HTML, so a literal </script> in a value would end it. */
const jsonLd = obj => JSON.stringify(obj).replace(/<\//g, '<\\/')

const attr = s => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')

/** Replace by a function so `$&` and friends in the content stay literal. */
const sub = (html, pattern, value) => {
  if (!pattern.test(html)) throw new Error(`prerender: template no longer matches ${pattern}`)
  return html.replace(pattern, () => value)
}

function pageHtml(pathname) {
  const head = headFor(pathname)
  const markup = renderRoute(pathname)
  let html = template

  html = sub(html, /<title>[\s\S]*?<\/title>/, `<title>${attr(head.title)}</title>`)
  html = sub(
    html,
    /<meta\s+name="description"[\s\S]*?\/>/,
    `<meta name="description" content="${attr(head.description)}" />`,
  )
  html = sub(html, /<link rel="canonical"[^>]*>/, `<link rel="canonical" href="${attr(head.canonical)}" />`)

  html = sub(
    html,
    /<meta property="og:url"[^>]*>/,
    `<meta property="og:url" content="${attr(head.canonical)}" />`,
  )
  html = sub(
    html,
    /<meta property="og:title"[^>]*>/,
    `<meta property="og:title" content="${attr(head.title)}" />`,
  )
  html = sub(
    html,
    /<meta\s+property="og:description"[\s\S]*?\/>/,
    `<meta property="og:description" content="${attr(head.description)}" />`,
  )
  html = sub(
    html,
    /<meta name="twitter:title"[^>]*>/,
    `<meta name="twitter:title" content="${attr(head.title)}" />`,
  )
  html = sub(
    html,
    /<meta\s+name="twitter:description"[\s\S]*?\/>/,
    `<meta name="twitter:description" content="${attr(head.description)}" />`,
  )

  const routeSchema = head.schemas
    .map(s => `    <script type="application/ld+json" data-seo-route>${jsonLd(s)}</script>`)
    .join('\n')
  if (routeSchema) html = sub(html, /<\/head>/, `${routeSchema}\n  </head>`)

  // Anchored on the <h1> that opens it. The head of index.html carries a
  // comment that mentions <noscript> by name, and an unanchored match starts
  // there and swallows most of the document.
  html = html.replace(/\n[ \t]*<noscript>\s*<h1[\s\S]*?<\/noscript>\n?/, '\n')
  html = sub(html, /<div id="root"><\/div>/, `<div id="root">${markup}</div>`)

  return html
}

/**
 * The sitemap is generated rather than hand-maintained, because the version in
 * public/ had already drifted: it listed four URLs with no lastmod, and adding
 * an article would have meant remembering to edit it. It is written straight
 * into dist/, overwriting the copy Vite copied out of public/.
 *
 * Worth knowing while reading this: in production the sitemap is currently
 * unreachable regardless of what we write here — the CDN's SPA rewrite has no
 * `xml` in its extension allow-list and serves index.html for it. See the note
 * at the top of amplify.yml.
 */
async function writeSitemap() {
  const urls = sitemapEntries()
    .map(u =>
      [
        '  <url>',
        `    <loc>${u.loc}</loc>`,
        u.lastmod ? `    <lastmod>${u.lastmod}</lastmod>` : null,
        `    <changefreq>${u.changefreq}</changefreq>`,
        `    <priority>${u.priority}</priority>`,
        '  </url>',
      ]
        .filter(Boolean)
        .join('\n'),
    )
    .join('\n')
  const xml = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`
  await writeFile(join(DIST, 'sitemap.xml'), xml, 'utf8')
  console.log(`sitemap    ${sitemapEntries().length} urls -> dist/sitemap.xml`)
}

for (const pathname of ROUTES) {
  const html = pageHtml(pathname)
  const out = pathname === '/' ? join(DIST, 'index.html') : join(DIST, pathname, 'index.html')
  await mkdir(dirname(out), { recursive: true })
  await writeFile(out, html, 'utf8')
  console.log(
    `prerendered ${pathname.padEnd(10)} -> ${out.replace(DIST, 'dist')}  (${(html.length / 1024).toFixed(1)} KB)`,
  )
}

await writeSitemap()
