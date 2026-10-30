# Getting named when a CFO asks ChatGPT

**Goal.** When someone asks an AI assistant *"Best AP automation software for Indian SMEs that
integrates with Tally Prime and Zoho Books, with invoice OCR and approval workflows"* or *"Tool that
automates TDS deduction on vendor bills, verifies GSTIN and bank accounts, and catches duplicate
vendor payments"*, Onsumm is in the answer.

This document is the plan for that. It is written to be argued with: every claim carries how well it
is evidenced, because most of what is published about this subject is sold by people with tooling to
sell, and roughly half of the widely repeated numbers do not survive contact with their source.

---

## 1. Where we actually stood on 19 September 2026

Measured directly, not inferred.

| Check | Result |
|---|---|
| `/`, `/product`, `/pricing`, `/contact` fetched as GPTBot | **Byte-identical.** All four returned the same 9,317-byte shell |
| `<a href>` links across 1,109 lines of marketing code | **Zero.** Every link was `<button onClick>` |
| `curl -I https://www.onsumm.ai/sitemap.xml` | **200, `content-type: text/html`** — the SPA rewrite was swallowing it |
| `<loc>` entries a crawler could read | **0** |
| `sameAs` targets in our Organization schema | **Both 404** (`x.com/onsummai`, `linkedin.com/company/onsummai`) |
| LinkedIn company page, any URL variant | **Does not exist** |
| Crunchbase, G2, Capterra, SoftwareSuggest, Techjockey, Wikidata | **Not present on any** |
| Third-party mention of "Onsumm" anywhere on the web | **None found** |

The site was inviting eleven AI crawlers in `robots.txt` and then handing every one of them an empty
`<div id="root">`. That is the whole story of why we were not being named.

**Competitive scale, for calibration:** Mysa has 587 indexed URLs, TrulyInvoice 534. We had one, and
it contained no prose.

---

## 2. What the evidence actually supports

Graded: **[strong]** = primary logs, controlled experiment, or vendor's own documentation.
**[moderate]** = large-n correlational with published method. **[weak]** = vendor marketing, method
unpublished — treated as a hypothesis, not a fact.

### The one that decides everything else

**No major AI crawler executes JavaScript. [strong]** Vercel/MERJ network-log analysis confirms
GPTBot, OAI-SearchBot, ChatGPT-User, ClaudeBot, PerplexityBot and Meta's agent all parse raw HTML
and stop. They *fetch* JS files — ChatGPT 11.5% of requests, Claude 23.8% — and never run them. Only
Googlebot and Applebot render. A client-rendered React SPA is invisible to ChatGPT, Claude and
Perplexity while looking perfectly healthy in Google Search Console.

Those crawlers also have poor URL hygiene: **~35% of ChatGPT's and Claude's fetches are 404s**,
against 8% for Googlebot. A working sitemap and clean redirects matter more for them, not less.

### What moves citation, in order

1. **Server-rendered HTML.** Prerequisite, not optimisation. **[strong]**
2. **Off-site brand mentions.** Across 75,000 brands, branded web mentions correlate with AI
   visibility at ρ=0.664 and YouTube mentions at ρ≈0.737 — against **backlinks at 0.218** and
   **number of pages on the site at 0.170**. **[moderate; the authors themselves flag that big
   brands accumulate all of these at once, so this describes what a visible brand looks like rather
   than proving mentions cause visibility.]** The practical read stands either way: publishing more
   pages is close to worthless on its own, and being talked about elsewhere is what correlates.
3. **Maintaining pages, not publishing new ones.** 75% of cited pages were updated within twelve
   months; 88% within two years; content older than three years is essentially never cited. The
   finding underneath that: measured by *publish* date only 42% look fresh, so **over a quarter of
   "fresh" cited pages are old pages that were genuinely revised.** **[moderate–strong, 47,097
   citations.]** Do not fake `dateModified` — the dating in that study came from structured signals
   that are trivially diffed against actual content.
4. **Answer-first structure.** 44.2% of ChatGPT citations came from the first ~30% of the page.
   Retrieval works on ~400-token chunks, so every passage has to make sense lifted out of context —
   name Onsumm rather than "the platform", and never write a paragraph that depends on the previous
   three. **[strong for the mechanism.]**
5. **Statistics, quotations and outbound citations in the copy.** The KDD '24 GEO paper measured
   +41% visibility for adding quotations, +31% for statistics, +27% for citing sources, against a
   19.3 baseline. **[strong — but on a researcher-built engine, with only the Perplexity test run
   against a real one, where it measured +37%.]**

   **The caveat almost nobody reports, and it is the one that matters to us:** those gains are
   rank-dependent. For a source already ranking #1 the same tactics measured **−20% to −30%**. For
   the source ranking #5 they measured **+98% to +115%**. This is a challenger's playbook and we are
   the challenger. It would become the wrong playbook if we ever start winning.

### What to skip, and stop paying for

- **`llms.txt`.** Across 137,210 domains, **97% of `llms.txt` files received zero requests**, and AI
  bots never probed for the file on domains that did not have one — they are not looking. A separate
  300,000-domain study found that adding it as a variable made a citation-prediction model *less*
  accurate. Google states outright that no machine-readable AI file is needed. **[strong negative.]**
  It is real and useful for *developer documentation consumed by coding agents* — a different job.
  We do not have that, so we are not shipping one.
- **Schema.org as a citation lever.** A difference-in-differences test over 1,885 pages that added
  JSON-LD against 4,000 matched controls measured −4.6% on AI Overviews, +2.4% on AI Mode, +2.2% on
  ChatGPT — no effect. **[strong negative.]** We keep our schema, because it earns rich results,
  entity disambiguation and a place in Bing's grounding pipeline. It is not why we will get cited
  and should not be budgeted as though it were.
- **Keyword stuffing.** Measured **−8%**, the only method in the GEO paper that scored below
  baseline. The `<meta name="keywords">` tag has been ignored by Google since 2009. Both are gone.
- **Chasing review-count milestones past the threshold.** Review count correlates *weakly negatively*
  with ChatGPT ranking. Directory presence is an inclusion gate; volume beyond it buys nothing.

### Numbers circulating in this field that are not real

Refuse to repeat these; they trace to blogs citing an unnamed "analysis" and nothing further:
"tables are cited 4.2× more", "FAQPage schema +200%", "listicles are 50% of AI citations", "42% of
JavaScript content is never indexed by AI". Also beware the denominator swap: "Wikipedia is 47.9% of
ChatGPT's sources" is share *of the top ten sources*. Share of all citations is **7.8%**.

---

## 3. Done in the codebase

| Change | Where |
|---|---|
| Public routes prerendered to real HTML at build time | `frontend/prerender/` |
| Nav and footer converted from `<button>` to real `<a href>` | `frontend/src/landing/LandingApp.tsx` |
| Footer links that pointed at the wrong page removed (Blog, Careers, Documentation, Privacy, Terms all led to `/` or `/product`) | same |
| Long-form content system: answer-first, question-shaped headings, visible update date, TechArticle + FAQPage + BreadcrumbList | `frontend/src/content/` |
| First article: TDS automation on vendor bills | `frontend/src/content/articles/` |
| Sitemap generated at build from the route list, with `lastmod` | `frontend/prerender/prerender.mjs` |
| `<meta name="keywords">` removed everywhere | `index.html`, `src/seo/useSeo.ts` |
| Dead `sameAs` and `twitter:site` removed | `index.html` |
| Regression tests: routes render distinctly without JS, pages link to each other, copy says what the description promises | `src/landing/prerender.test.tsx`, `src/seo/seo.test.tsx` |

Result: 9.3 KB of metadata became five distinct pages, 24–50 KB each, of prose a crawler can read.

### Blocked on someone with console access

**The CDN rewrite rules must change before any of this reaches a crawler.** Full instructions,
including the `aws amplify update-app` command and the verification curl, are at the top of
`frontend/amplify.yml`. In short: the extension allow-list has no `xml`, which is why the sitemap
serves as HTML; and the catch-all now needs explicit rules ahead of it for `/product`, `/pricing`,
`/contact` and the article, or it will shadow the prerendered files and serve the home page at every
URL. This is reproducible locally — `vite preview` does exactly that today.

---

## 4. What is left, in priority order

Nothing here is code. All of it is the part the evidence says actually decides the outcome.

### Now — entity existence. Nothing else works until we exist.

An LLM names a company confidently when several independent sources agree it exists. We currently
have one source, ourselves, and we were asserting two profiles that return 404.

1. **LinkedIn company page**, with employees attaching themselves to it. Single highest-value entity
   anchor for Indian B2B SaaS. Free. Then restore `sameAs` in `index.html` — the note is already
   there.
2. **Crunchbase** (free basic) and **Tracxn**.
3. **SoftwareSuggest** and **Techjockey** — both free, both India-first, both rank for our queries,
   and both have AP categories nearly empty of India-native vendors. Mysa, Clear AP, RazorpayX S2P,
   EnKash and Kodo are absent from all of them. This is the cheapest available win in the whole plan.
4. **G2** (claim the profile; ~10 category reviews for Grid placement) and **Capterra India** (20
   reviews in 24 months for Shortlist). Stop at the threshold — see the negative correlation above.

Skip Wikipedia. No competitor at our size has an article and we would not survive notability review.
Wikidata is worth an item later, once news coverage exists to cite; not before.

### Next — the distribution surfaces that double as proof

Our Tally Prime claim is currently self-asserted. These make it third-party-verified *and* are
distribution channels:

- **TallyShop / Tally Developer Hub** — Tally's own add-on marketplace, 28,000+ partners. Being
  absent while claiming Tally Prime posting as a headline feature is a credibility hole.
- **Zoho Marketplace** — there is **no AP or invoice-OCR extension for Zoho Books**. Open slot.
- **Microsoft Azure Marketplace** — one competitor is there; low competition.

### Then — the content that gets quoted

The category's ranking content is vendor-authored, and one competitor (AI Accountant) is running
4,500-word pillar pages with bylined authors and named case studies. That is the bar.

Priority order, chosen by how uncontested each is:

1. **Vendor bank account verification / penny drop / bank-change detection.** **No AP automation
   competitor claims this at all**, and no page on the web compares AP tools on it. Our strongest
   genuine differentiator and completely uncontested.
2. **Duplicate invoice and payment detection — the actual matching method.** One competitor
   publishes their logic. Publishing a better version is a direct citation steal.
3. **GSTIN verification and GST compliance.** Table stakes as a claim; depth is not.
4. **Tally Prime AP automation** and **Zoho Books AP automation** — the integration queries.
5. **Alternatives pages.** Mysa is Zoho-first and weak on Tally; Clear is enterprise-only. Both
   leave the Tally-using SME uncovered, and no alternatives page exists for either.

Write them the way `tdsOnVendorBills.ts` is written: the answer above the first heading, every
heading a question, every paragraph able to stand alone, a real table for the comparison, a visible
update date — and every claim traceable to something the product does. An answer engine repeating a
claim we cannot honour is a worse outcome than not being cited.

**And schedule the maintenance.** Revisit every page quarterly with substantive revision. On the
evidence this beats publishing new pages.

### In parallel — the community layer

Uncontested, because it is nearly empty: in ~20 searches across these queries, **not one Reddit,
Quora, YouTube or LinkedIn result appeared**. That cuts both ways, but it means no incumbent holds it.

- **CAclubindia** is demonstrably indexed and ranking. There is a live thread —
  *"Building a Tally Automation Tool for Invoice Entry & GST Accuracy"*, 1,234 views — where a
  heavyweight member asks about faded thermal receipts, multi-page invoices with mixed GST rates and
  smart ledger mapping, and **no vendor is named in the replies**. Answer it properly, as a person,
  disclosing the affiliation.
- **nasscom community** and **TaxGuru** accept vendor-authored articles on high-authority Indian
  domains. nasscom already ranks for AP automation.
- **YouTube** is the strongest single correlate found in the data and we have nothing there.

One caution on channel strategy: **source mix is a platform policy decision and moves without
warning.** ChatGPT's Reddit citation share fell from ~60% of responses to ~10% in a single week in
September 2025. Build breadth; never build a plan on one channel's current share.

---

## 5. Measuring it

**Free and first-party, and more trustworthy than any panel:**

- **Server log analysis.** Count hits from `OAI-SearchBot`, `Claude-SearchBot`, `PerplexityBot`,
  `GPTBot`, `ClaudeBot`, and especially the user-triggered `ChatGPT-User`, `Claude-User` and
  `Perplexity-User` — those mean a real person's question routed to our URL at that moment. Verify
  by reverse DNS against the published IP ranges (`openai.com/searchbot.json`,
  `claude.com/crawling/bots.json`, `perplexity.com/perplexitybot.json`).
  **The first success signal will be these appearing at all, then fetching more than one URL.**
- **Bing Webmaster Tools AI Performance report.** Free, first-party, and the most underused signal
  available given how much of ChatGPT search rides Bing.
- **Referral traffic** from `chatgpt.com`, `perplexity.ai`, `claude.ai`, `gemini.google.com`. Known
  undercount: Claude's native app sends no `Referer` header, so that traffic is invisible.

**A prompt panel.** 100–300 buyer-intent prompts, run weekly, logging brand-mention rate, position
within the answer, share of voice against a named competitor set, and which domains got cited. DIY
via the Perplexity Sonar, Anthropic, OpenAI and Gemini APIs costs single-digit dollars per run.

> The caveat that decides whether DIY is worth it: **the API is not the product.** API search tools
> use different retrieval paths, prompts and sometimes indexes than the consumer apps, so a DIY panel
> measures the API surface, not what a CFO sees in chatgpt.com. Use it for trend direction and
> competitive delta, not absolute numbers. Tools that scrape the consumer UIs (Otterly from $29/mo,
> Profound, Peec, Scrunch, Ahrefs Brand Radar) are charging for exactly that difference.

---

## 6. Honest expectations

Retrieval and parametric memory are different games. The commercial queries we care about almost
always trigger live search — one study found ChatGPT searched on 86.5% of commercial prompts against
0.9% of informational ones — so **the work above is aimed at the queries that matter and can move
within a quarter.**

What it will not do quickly is get Onsumm named in the unsourced framing sentence — *"the leading
tools in this space are A, B and C."* That comes from training data, training cutoffs lag by many
months, and nothing published today enters on a timeline a quarterly plan can use. Anyone selling
"get into the model's memory" as a 90-day deliverable is selling retrieval optimisation with the
wrong label.

The field is also thinner than it is made to look. The genuinely controlled evidence amounts to the
KDD '24 GEO paper, its 2026 successor, one schema difference-in-differences test, and log-level
studies from Vercel and Ahrefs. Everything else is correlational or unpublished — much of it from
companies selling GEO tooling. Treat this document the same way: the measurements in §1 are ours and
reproducible, and the rest is graded so it can be challenged.

The encouraging part is that the field here is unusually soft. The Indian AP category is near-empty
on every review platform; the second buyer query has exactly one serious competitor; and its rarest
component — vendor bank verification with bank-change detection — is claimed by nobody. The gap was
total, and so is the opening.
