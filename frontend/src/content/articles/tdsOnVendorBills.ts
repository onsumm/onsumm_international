import type { Article } from '../types'

/**
 * The flagship long-form page.
 *
 * Chosen because it is the one buyer question in this category that nobody in
 * AP automation answers properly. Competitors claim "TDS automation" as a
 * feature bullet; almost none describe what the software actually does when a
 * vendor crosses a threshold in November on bills raised since April. That gap
 * is the whole opportunity: an answer engine asked "tool that automates TDS
 * deduction on vendor bills" has nothing method-level to quote, so it falls
 * back to listing brands it has heard of.
 *
 * Every mechanism described here is implemented in utils/tds_engine.py. Where
 * the engine reads a value from dim_tds_sections rather than hardcoding it, the
 * copy says so rather than quoting a rate — statutory rates and thresholds move,
 * and a page that quotes the wrong one is worse than a page that explains the
 * machinery. Nothing here asserts a number the product does not hold.
 */
export const tdsOnVendorBills: Article = {
  path: '/tds-automation-on-vendor-bills',
  title: 'TDS Automation on Vendor Bills',
  h1: 'TDS automation on vendor bills: how it actually has to work',
  description:
    'How TDS on vendor bills is automated — choosing the section, the GST-exclusive base, threshold tracking across the financial year, and Section 206AA.',
  clusters: ['tds', 'gst', 'apFinance', 'india', 'tally'],
  updated: '2026-09-19',

  answer:
    'Automating TDS on vendor bills means four things the software has to get right in order: picking the correct section for the vendor and the expense, calculating the deduction on the right base (GST-exclusive when GST is shown separately on the invoice), tracking the vendor’s cumulative billing across the financial year so the threshold fires on the bill that actually crosses it, and recovering the tax on earlier bills once it does. Onsumm does all four on every vendor bill it processes, and holds the section rules as data rather than code so a rate change is a configuration change rather than a release.',

  sections: [
    {
      heading: 'Why is TDS on vendor bills hard to automate?',
      body: [
        'TDS is hard to automate because the deduction on any single bill depends on every other bill from that vendor in the same financial year. A tool that looks at one invoice at a time can calculate a rate, but it cannot know whether the threshold has been crossed, and it cannot know that it now owes tax on three bills it has already passed for payment.',
        'This is why most accounts payable software treats TDS as a field the approver types in. The rate is a lookup; the liability is a running total. Getting the second one right is what separates a TDS feature from TDS automation.',
      ],
      list: [
        'The applicable section depends on the vendor’s category and the nature of the expense, not on the invoice alone.',
        'Some sections apply per bill, some on the annual aggregate, and some on both at once — a per-transaction floor and a separate annual ceiling.',
        'Crossing an annual threshold in month eight creates a liability on months one to seven.',
        'A vendor without a PAN attracts a higher rate under Section 206AA, and a vendor with a lower-deduction certificate attracts a lower one.',
        'Two bills from the same vendor arriving at the same moment must not both read the same pre-crossing total.',
      ],
    },
    {
      heading: 'How does Onsumm decide which TDS section applies?',
      body: [
        'Onsumm resolves the section in a fixed order of precedence: a per-vendor override first, then the rule mapped to the vendor’s category, and if neither exists the bill is flagged as uncategorised rather than passed with no deduction. A vendor nobody has categorised is a question for a human, not a silent zero.',
        'The sections themselves live in a table, not in code. Each row carries its return code, the legacy section label accountants still use in conversation, the default rate, the rate that applies when no PAN is available, the threshold type and amounts, whether the base is GST-exclusive, and the dates it is effective between. When the statute changes, the fix is a row with a new effective-from date — and bills dated before it keep computing the way they did at the time, which is what an assessment will ask for.',
      ],
    },
    {
      heading: 'Is TDS deducted on the invoice total or the taxable value?',
      body: [
        'TDS is deducted on the value excluding GST whenever GST is shown as a separate line on the invoice, and on the gross amount when it is not. Onsumm reads both off the bill during extraction and applies whichever the section’s base calculation calls for, so the distinction is made from what the invoice actually says rather than from a default someone set once.',
        'This is the single most common source of a rupee-level mismatch between the books and the return. It is also the easiest one to get wrong silently, because deducting on the gross amount produces a number that looks entirely reasonable until the vendor disputes it.',
      ],
    },
    {
      heading: 'How is the annual threshold tracked across bills?',
      body: [
        'Onsumm keeps a running state per vendor, per section, per financial year: total billed so far, whether the threshold has been crossed, tax deducted to date, and any catch-up still outstanding. Every new bill is added to that total before the threshold is evaluated, so the deduction fires on the bill that crosses the line rather than on the next one after it.',
        'Four threshold shapes are supported, because the statute uses all four: no threshold at all, a per-transaction floor, an annual aggregate, and the dual form where a bill is liable if it clears the per-transaction floor on its own or if the year-to-date total clears the annual one. The financial year is derived from the invoice date and the tenant’s own year-start month, not from the date the bill was uploaded.',
        'That state is locked while a bill is being computed. Two invoices from the same vendor posted at the same instant would otherwise both read the same pre-crossing total, both decide the threshold had not been reached, and both deduct nothing — a race that produces a shortfall nobody discovers until the return is filed.',
      ],
    },
    {
      heading: 'What happens when a vendor crosses the threshold mid-year?',
      body: [
        'When the annual aggregate is crossed, the tax on the earlier bills becomes payable too, and Onsumm carries that forward as a catch-up balance against the vendor rather than writing it off. The crossing bill is marked as the crossing bill, the arrears are recorded, and the recovery is tracked until the balance is zero.',
        'This is the step that is almost universally missing. A tool that starts deducting from the crossing bill onward is under-deducting by exactly the tax on everything before it, and the shortfall surfaces at filing time as a reconciliation nobody can explain without going back through the year bill by bill.',
      ],
    },
    {
      heading: 'What happens when a vendor has no PAN?',
      body: [
        'Section 206AA requires a higher rate when the deductee has not furnished a PAN, and Onsumm applies the section’s no-PAN rate when none is on file. Before it does, it tries to recover the PAN from the vendor’s GSTIN — characters three to twelve of a GSTIN are the PAN — so a vendor whose PAN was simply never typed into the master is not penalised at twenty percent for a data-entry gap.',
        'A lower-deduction certificate works the other way: the certificate number and its rate are held against the vendor, and the override is applied in place of the section default with the certificate recorded alongside the deduction, so the reason for the lower rate travels with the entry.',
      ],
    },
    {
      heading: 'How does the deduction reach Tally Prime and the return?',
      body: [
        'The deduction is computed before the bill is approved, so what the approver signs off is the net payable rather than an amount that changes afterwards. The approved bill posts to Tally Prime or Zoho Books with the TDS entry against the section’s ledger, carrying the return code that the quarterly statement needs.',
        'Because the running state is held per vendor, per section, per year, the quarterly return is a read of something that already exists rather than a reconstruction. The common alternative — exporting every bill to a spreadsheet in the last week of the quarter and re-deriving the deductions — is where most of the effort and most of the errors are.',
      ],
    },
    {
      heading: 'How does this compare with doing TDS in Tally alone?',
      body: [
        'Tally computes TDS correctly once the entry is in front of it. What it cannot do is read the vendor bill, decide the section from the vendor and the expense, or tell you in September that a vendor you have been paying since April is about to cross an annual threshold. Those are accounts-payable problems that happen before the voucher exists.',
        'The division of labour that works is the one where the bill is read, classified, checked and approved upstream, and Tally receives a finished entry. Onsumm is built for that half; it posts into Tally Prime rather than replacing it.',
      ],
      table: {
        caption: 'Where each step happens',
        head: ['Step', 'In Tally alone', 'With Onsumm upstream'],
        rows: [
          ['Reading the bill', 'Manual data entry', 'OCR, with the text layer read before vision'],
          ['Choosing the section', 'The person entering it decides', 'Vendor category rule, or a per-vendor override'],
          ['GST-exclusive base', 'Depends who typed it', 'Read from the invoice, per the section’s rule'],
          ['Annual threshold', 'Tracked by hand, if at all', 'Running per-vendor state for the financial year'],
          ['Crossing mid-year', 'Noticed at filing, if noticed', 'Catch-up raised and recovered'],
          ['No PAN', 'Higher rate if someone remembers', 'PAN recovered from GSTIN, else the 206AA rate'],
        ],
      },
    },
  ],

  faqs: [
    {
      q: 'Can software deduct TDS on vendor bills automatically?',
      a: 'Yes. Onsumm calculates TDS on every vendor bill automatically — it maps the vendor to a section, computes the deduction on the GST-exclusive value where the invoice shows GST separately, tracks the vendor’s cumulative billing for the financial year so the threshold fires on the right bill, and raises the arrears on earlier bills when an annual threshold is crossed mid-year.',
    },
    {
      q: 'How does Onsumm handle a TDS threshold crossed in the middle of the year?',
      a: 'Onsumm raises a catch-up balance against the vendor for the tax due on the bills already processed that year, marks the bill that crossed the threshold, and tracks the recovery until it is cleared. Deducting only from the crossing bill onward leaves a shortfall equal to the tax on everything before it, which is the discrepancy most teams find at filing time.',
    },
    {
      q: 'Does Onsumm apply Section 206AA when a vendor has no PAN?',
      a: 'Yes, but it checks first. Onsumm derives the PAN from characters three to twelve of the vendor’s GSTIN before falling back to the higher no-PAN rate, so a vendor whose PAN was never entered in the master is not charged the Section 206AA penalty rate over a missing field.',
    },
    {
      q: 'Is TDS calculated on the invoice total or on the value before GST?',
      a: 'On the value before GST when the invoice shows GST as a separate line, and on the gross amount when it does not. Onsumm takes both figures from the bill during extraction and applies whichever the section calls for, rather than assuming one.',
    },
    {
      q: 'Do TDS rates have to be updated in the software when the law changes?',
      a: 'The sections, rates and thresholds are configuration in Onsumm, not code, and each carries the dates it is effective between. A change is a new row with a new effective-from date; bills dated earlier continue to compute the way they did at the time, which is what an assessment expects to see.',
    },
    {
      q: 'Does the TDS entry post into Tally Prime?',
      a: 'Yes. The deduction is computed before approval, so the approver signs off the net payable, and the approved bill posts to Tally Prime or Zoho Books with the TDS entry against the section’s ledger and the return code the quarterly statement needs.',
    },
  ],

  related: [
    { label: 'Product — invoice OCR, approvals, GST and TDS', path: '/product' },
    { label: 'Pricing', path: '/pricing' },
    { label: 'Book a demo', path: '/contact' },
  ],
}
