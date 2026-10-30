import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { StaticRouter } from 'react-router'
import LandingApp from '../landing/LandingApp'
import { LANDING_PATHS, PUBLIC_PATHS, titleForPath } from '../routes/paths'
import { FAQS, faqSchema } from './faq'
import { ALL_KEYWORDS, KEYWORD_CLUSTERS, keywordsFor } from './keywords'
import { MAX_DESCRIPTION_CHARS, PAGE_SEO, SITE_URL } from './useSeo'

describe('marketing metadata', () => {
  it('covers every public route', () => {
    // PUBLIC_PATHS, not LANDING_PATHS: a long-form article that shipped without
    // a description would inherit the home page's, and two URLs describing
    // themselves identically is exactly what a canonical audit flags.
    for (const path of PUBLIC_PATHS) {
      expect(PAGE_SEO[path], `no SEO entry for ${path}`).toBeDefined()
    }
  })

  it('keeps each description inside the length Google renders', () => {
    for (const [path, page] of Object.entries(PAGE_SEO)) {
      expect(page.description.length, `${path} description is truncated in the SERP`)
        .toBeLessThanOrEqual(MAX_DESCRIPTION_CHARS)
    }
  })

  it('gives each page a distinct description', () => {
    const seen = Object.values(PAGE_SEO).map(p => p.description)
    expect(new Set(seen).size).toBe(seen.length)
  })

  it('gives each page a distinct, keyword-bearing title', () => {
    const titles = LANDING_PATHS.map(titleForPath)
    expect(new Set(titles).size).toBe(titles.length)
    expect(titleForPath('/')).toContain('AP Automation')
  })

  // The clusters used to be emitted as <meta name="keywords">. They are not
  // any more — that tag is ignored by Google and read as a spam signal by Bing,
  // which is what grounds Copilot and much of ChatGPT search. What replaced it
  // is this: the terms have to appear in copy a reader can actually see, which
  // is the only form in which a retrieval layer can match on them.
  it('names at least one keyword cluster per page', () => {
    for (const [path, page] of Object.entries(PAGE_SEO)) {
      expect(page.clusters.length, `${path} has no keyword clusters`).toBeGreaterThan(0)
      expect(keywordsFor(...page.clusters).length).toBeGreaterThan(0)
    }
  })

  it('emits schema whose @id sits on our own domain', () => {
    for (const page of Object.values(PAGE_SEO)) {
      for (const schema of page.schema?.() ?? []) {
        const id = (schema as { '@id'?: string })['@id']
        if (id) expect(id.startsWith(SITE_URL)).toBe(true)
      }
    }
  })
})

describe('keyword vocabulary', () => {
  it('de-duplicates across clusters', () => {
    expect(new Set(ALL_KEYWORDS).size).toBe(ALL_KEYWORDS.length)
  })

  it('has no empty or untrimmed terms', () => {
    for (const term of ALL_KEYWORDS) {
      expect(term).toBe(term.trim())
      expect(term.length).toBeGreaterThan(0)
    }
  })

  it('draws only from the requested clusters', () => {
    expect(keywordsFor('tally')).toEqual([...KEYWORD_CLUSTERS.tally])
    expect(keywordsFor('gst')).not.toContain('Tally connector')
  })
})

describe('FAQ schema', () => {
  // The schema must describe copy that is actually on the page — LandingHome
  // renders this same list, and an FAQPage block over unrendered questions is a
  // manual-action risk.
  it('mirrors the questions the home page renders', () => {
    const schema = faqSchema() as { mainEntity: { name: string; acceptedAnswer: { text: string } }[] }
    expect(schema.mainEntity).toHaveLength(FAQS.length)
    expect(schema.mainEntity.map(e => e.name)).toEqual(FAQS.map(f => f.q))
    expect(schema.mainEntity.map(e => e.acceptedAnswer.text)).toEqual(FAQS.map(f => f.a))
  })

  it('asks each question only once', () => {
    const questions = FAQS.map(f => f.q)
    expect(new Set(questions).size).toBe(questions.length)
  })
})

describe('the page copy carries the terms the metadata claims', () => {
  const render = (path: string) =>
    renderToStaticMarkup(
      <StaticRouter location={path}>
        <LandingApp onLogin={() => {}} />
      </StaticRouter>,
    )
      .replace(/<style[\s\S]*?<\/style>/g, ' ')
      .replace(/<[^>]+>/g, ' ')
      .replace(/&#x27;/g, "'")
      .replace(/&amp;/g, '&')
      .replace(/\s+/g, ' ')
      .toLowerCase()

  /**
   * Crude suffix stripping, so "tracking" in a description matches "tracked" in
   * the copy. The check is that the page discusses the thing, not that it
   * repeats the exact inflection.
   */
  const stem = (w: string) => {
    for (const suffix of ['ing', 'ed', 'es', 's']) {
      if (w.endsWith(suffix) && w.length - suffix.length >= 4) return w.slice(0, -suffix.length)
    }
    return w
  }

  it.each(PUBLIC_PATHS)('%s says in prose what its description promises', path => {
    const text = render(path)
    // Content words from the description, minus the ones too short or too
    // common to mean anything. Every one of them has to be somewhere on the
    // page: a description is a promise about the copy, and an answer engine
    // that retrieves the page on the strength of the description and then finds
    // nothing supporting it is the failure this catches.
    const stop = new Set(['and', 'the', 'for', 'with', 'from', 'that', 'this', 'your', 'are', 'its'])
    const words = PAGE_SEO[path].description
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, ' ')
      .split(/\s+/)
      .filter(w => w.length > 3 && !stop.has(w))
    const missing = words.filter(w => !text.includes(stem(w)))
    expect(missing, `${path} description uses terms the page never says`).toEqual([])
  })
})
