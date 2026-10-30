import type { Article } from './types'
import { FAQS_STYLE_TOKENS as C } from './tokens'

/**
 * Renders a long-form page.
 *
 * The layout is deliberately plain. The measured drivers of whether a passage
 * gets retrieved and quoted are structural, not visual: the answer sits above
 * the first heading, headings are questions, paragraphs stand alone, the update
 * date is visible, and the comparison lives in a real <table> rather than in
 * styled divs. A crawler that strips every style rule should still get the
 * document's shape intact.
 */

function Paragraphs({ body }: { body: string[] }) {
  return (
    <>
      {body.map((p, i) => (
        <p key={i} style={{ fontSize: 16, lineHeight: 1.75, color: C.onSurfaceVar, margin: '0 0 18px' }}>
          {p}
        </p>
      ))}
    </>
  )
}

export default function ArticlePage({ article, onNavigate }: { article: Article; onNavigate: (path: string) => void }) {
  const updated = new Date(article.updated).toLocaleDateString('en-IN', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  })

  return (
    <article style={{ maxWidth: 760, margin: '0 auto', padding: '56px 24px 80px' }}>
      <nav aria-label="Breadcrumb" style={{ fontSize: 13, color: C.outline, marginBottom: 20 }}>
        <a href="/" style={{ color: C.primaryCont, textDecoration: 'none' }}>
          Onsumm
        </a>
        <span aria-hidden="true"> / </span>
        <span>{article.title}</span>
      </nav>

      <h1
        style={{
          fontSize: 38,
          lineHeight: 1.2,
          fontWeight: 800,
          color: C.onSurface,
          fontFamily: '"Public Sans", sans-serif',
          margin: '0 0 20px',
        }}
      >
        {article.h1}
      </h1>

      {/*
        The answer-first paragraph. Placed above everything because the passage
        a generative engine quotes is disproportionately drawn from the opening
        of the document, and because a reader who reads only this should still
        have the answer they came for.
      */}
      <p
        style={{
          fontSize: 18,
          lineHeight: 1.7,
          color: C.onSurface,
          margin: '0 0 28px',
          paddingLeft: 18,
          borderLeft: `3px solid ${C.secondary}`,
        }}
      >
        {article.answer}
      </p>

      <p style={{ fontSize: 13, color: C.outline, margin: '0 0 44px' }}>
        Last updated <time dateTime={article.updated}>{updated}</time> · Onsumm
      </p>

      {article.sections.map(section => (
        <section key={section.heading} style={{ marginBottom: 44 }}>
          <h2
            style={{
              fontSize: 24,
              lineHeight: 1.3,
              fontWeight: 700,
              color: C.onSurface,
              fontFamily: '"Public Sans", sans-serif',
              margin: '0 0 16px',
            }}
          >
            {section.heading}
          </h2>

          <Paragraphs body={section.body} />

          {section.list && (
            <ul style={{ margin: '0 0 18px', paddingLeft: 22 }}>
              {section.list.map(item => (
                <li key={item} style={{ fontSize: 16, lineHeight: 1.7, color: C.onSurfaceVar, marginBottom: 8 }}>
                  {item}
                </li>
              ))}
            </ul>
          )}

          {section.table && (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14, margin: '8px 0 18px' }}>
              <caption style={{ captionSide: 'top', textAlign: 'left', fontSize: 13, color: C.outline, paddingBottom: 8 }}>
                {section.table.caption}
              </caption>
              <thead>
                <tr>
                  {section.table.head.map(h => (
                    <th
                      key={h}
                      scope="col"
                      style={{
                        textAlign: 'left',
                        padding: '10px 12px',
                        borderBottom: `2px solid ${C.outlineVar}`,
                        color: C.onSurface,
                        fontWeight: 700,
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {section.table.rows.map(row => (
                  <tr key={row[0]}>
                    {row.map((cell, i) => (
                      <td
                        key={i}
                        style={{
                          padding: '10px 12px',
                          borderBottom: `1px solid ${C.border}`,
                          color: i === 0 ? C.onSurface : C.onSurfaceVar,
                          fontWeight: i === 0 ? 600 : 400,
                        }}
                      >
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      ))}

      <section style={{ marginBottom: 44 }}>
        <h2
          style={{
            fontSize: 24,
            fontWeight: 700,
            color: C.onSurface,
            fontFamily: '"Public Sans", sans-serif',
            margin: '0 0 16px',
          }}
        >
          Frequently asked questions
        </h2>
        {/*
          Rendered open rather than behind a <details> toggle. The answers are
          the most quotable passages on the page and they are short; hiding them
          buys a tidier page at the cost of the thing the page is for.
        */}
        <dl style={{ margin: 0 }}>
          {article.faqs.map(faq => (
            <div key={faq.q} style={{ marginBottom: 22 }}>
              <dt style={{ fontSize: 16, fontWeight: 700, color: C.onSurface, marginBottom: 6 }}>{faq.q}</dt>
              <dd style={{ margin: 0, fontSize: 16, lineHeight: 1.7, color: C.onSurfaceVar }}>{faq.a}</dd>
            </div>
          ))}
        </dl>
      </section>

      <nav aria-label="Related pages" style={{ borderTop: `1px solid ${C.border}`, paddingTop: 24 }}>
        <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexWrap: 'wrap', gap: 16 }}>
          {article.related.map(link => (
            <li key={link.path}>
              <a
                href={link.path}
                onClick={e => {
                  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
                  e.preventDefault()
                  onNavigate(link.path)
                }}
                style={{ fontSize: 14, color: C.primaryCont, textDecoration: 'none', fontWeight: 600 }}
              >
                {link.label}
              </a>
            </li>
          ))}
        </ul>
      </nav>
    </article>
  )
}
