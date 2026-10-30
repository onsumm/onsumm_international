import type { FaqEntry } from '../seo/faq'
import type { KeywordCluster } from '../seo/keywords'

/**
 * The shape of a long-form marketing page.
 *
 * These pages exist to be quoted. An answer engine does not read a page and
 * summarise it the way a person would — it retrieves a passage and repeats it,
 * usually one or two sentences, usually without the heading above it and
 * usually without the sentence after it. So the model here is deliberately not
 * "a blob of HTML":
 *
 *   - every section has a question-shaped heading, because the retrieval query
 *     is a question and the match is made against the heading as much as the
 *     body;
 *   - `body` is an array of paragraphs rather than one string, so that each
 *     paragraph can stand on its own when lifted out of context;
 *   - `answer` is the one-paragraph reply to the page's own title, placed
 *     before anything else on the page, because that is the passage that gets
 *     quoted when the engine has no better anchor;
 *   - `updated` is rendered visibly and emitted in schema, because freshness is
 *     one of the few things a retrieval layer can check cheaply.
 *
 * Claims that name the product must be things the product actually does. The
 * TDS article, for instance, describes the behaviour in utils/tds_engine.py and
 * nothing beyond it — an answer engine repeating a claim we cannot honour is a
 * worse outcome than not being cited.
 */

export interface ArticleTable {
  caption: string
  head: string[]
  rows: string[][]
}

export interface ArticleSection {
  /** H2. Phrased as the question a reader would have typed. */
  heading: string
  /** Paragraphs. The first one answers the heading on its own. */
  body: string[]
  list?: string[]
  table?: ArticleTable
}

export interface Article {
  /** Absolute route, e.g. '/tds-automation-on-vendor-bills'. */
  path: string
  /** <title>, before the ` · Onsumm` suffix. Keep the whole thing under ~60. */
  title: string
  h1: string
  /** <meta description>. Under 160 characters — see MAX_DESCRIPTION_CHARS. */
  description: string
  clusters: KeywordCluster[]
  /** ISO date. Rendered on the page and emitted as dateModified. */
  updated: string
  /** The answer-first paragraph, above the first heading. */
  answer: string
  sections: ArticleSection[]
  faqs: FaqEntry[]
  related: { label: string; path: string }[]
}
