/**
 * The FAQ that appears on the home page, and the FAQPage schema built from it.
 *
 * One list feeds both on purpose. Structured data has to describe copy the
 * visitor can actually see — an FAQPage block over questions that aren't on the
 * page is a manual-action risk with Google, and an answer engine that quotes us
 * should be quoting something a human could have read.
 *
 * Each answer leads with the claim and names the product in it, because that
 * first sentence is what gets lifted into an AI answer, usually without the
 * question and rarely with the sentence after it.
 */
export interface FaqEntry {
  q: string
  a: string
}

export const FAQS: readonly FaqEntry[] = [
  {
    q: 'What is AP automation?',
    a: 'AP automation is software that takes a vendor bill from arrival to payment without manual data entry. Onsumm reads the invoice with AI invoice OCR, routes it through an approval workflow, checks GST and TDS, and posts the approved bill to your accounting system — so accounts payable runs on exceptions rather than on typing.',
  },
  {
    q: 'Does Onsumm integrate with Tally Prime?',
    a: 'Yes — Onsumm posts approved bills directly into Tally Prime and Tally ERP 9. The Tally connector maps each bill to your existing ledgers, voucher types and GST classifications, so automated Tally posting produces the same entries your accountant would have made by hand, with the invoice image attached.',
  },
  {
    q: 'Does Onsumm work with Zoho Books?',
    a: 'Yes. Zoho Books integration is native, and teams running Tally Prime and Zoho Books side by side can post to both from one approved bill — useful during a migration, or when the books and the statutory filing live in different systems.',
  },
  {
    q: 'How accurate is the invoice OCR?',
    a: "Onsumm reads a PDF's embedded text layer first and falls back to vision OCR only for scans, which is why GSTINs, invoice numbers and bank details come through exactly rather than approximately. Every extracted field carries a confidence score, and anything below the bar is held for human review instead of being posted quietly.",
  },
  {
    q: 'Does Onsumm handle GST and GSTIN verification?',
    a: 'Yes. Onsumm runs GSTIN verification on every vendor and every bill, validates the GST invoice against the GSTIN it claims, and flags a mismatch before approval. ITC reconciliation compares what you booked against what your vendors actually filed, so input tax credit you are owed does not lapse unnoticed.',
  },
  {
    q: 'Can Onsumm calculate TDS on vendor bills?',
    a: 'Yes — automated TDS calculation is applied per bill, using the vendor type, PAN status and the section that applies to the expense. TDS tracking then carries the deduction through to payment and to the quarterly return, so vendor TDS does not have to be reconstructed at filing time.',
  },
  {
    q: 'How does Onsumm catch duplicate invoices and duplicate payments?',
    a: 'Onsumm checks each new bill against every bill already in the system — same vendor and invoice number, same amount and date, or the same document arriving by a second route. Duplicate invoice detection runs before approval and duplicate payment detection runs again before money moves, which is where a re-sent bill usually slips through.',
  },
  {
    q: "Does Onsumm verify a vendor's bank account?",
    a: 'Yes. Vendor bank account verification confirms the account belongs to the vendor before the first payment, and vendor bank change detection flags it whenever those details change afterwards — the change being the moment invoice fraud and payment fraud usually happen.',
  },
  {
    q: 'Is Onsumm built for Indian SMEs?',
    a: 'Onsumm is built for Indian SMEs specifically, not adapted from a US product. GST, TDS, HSN codes, e-invoicing, Tally Prime and Zoho Books are first-class, and the approval workflows follow how Indian finance teams already sign off on a vendor bill.',
  },
  {
    q: 'How long does it take to get started?',
    a: 'Most teams are processing live invoices the same week. Setup is connecting Tally Prime or Zoho Books, importing your vendor master, and setting the approval matrix — there is no data migration, because Onsumm reads the bills as they arrive from that point on.',
  },
]

/** FAQPage schema for the home route, built from the list rendered above it. */
export function faqSchema(): object {
  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    '@id': 'https://www.onsumm.ai/#faq',
    mainEntity: FAQS.map(({ q, a }) => ({
      '@type': 'Question',
      name: q,
      acceptedAnswer: { '@type': 'Answer', text: a },
    })),
  }
}
