import { gstinChecksumError } from '../utils/gstin'

export interface ValidationIssue {
  field: string
  label: string
  message: string
  severity: 'error' | 'warning'
}

interface ReviewShape {
  vendor?: { name?: string; gstin?: string } | Record<string, unknown>
  invoice_number?: unknown
  invoice_date?: unknown
  due_date?: unknown
  subtotal?: unknown
  total_amount?: unknown
  [k: string]: unknown
}

const num = (v: unknown): number | null => {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

const str = (v: unknown): string => (typeof v === 'string' ? v.trim() : '')

/**
 * Gate for "Send for Approval".
 *
 * The audit's R2 finding: the button was disabled only while a request was in
 * flight, so a bill with no vendor, no amount, no date and a GSTIN that fails
 * its own checksum could be pushed into the approval chain. The checksum was
 * already being computed and displayed — it just never blocked anything.
 *
 * Errors block submission. Warnings are surfaced but do not block, because a
 * legitimately unusual invoice must still be processable.
 */
export function validateForSubmission(d: ReviewShape, category: string): ValidationIssue[] {
  const issues: ValidationIssue[] = []
  const vendor = (d.vendor ?? {}) as { name?: string; gstin?: string }

  const vendorName = str(vendor.name)
  if (!vendorName) {
    issues.push({ field: 'vendor.name', label: 'Vendor name', message: 'Required before approval.', severity: 'error' })
  }

  const gstin = str(vendor.gstin)
  if (gstin) {
    const err = gstinChecksumError(gstin)
    if (err) {
      issues.push({ field: 'vendor.gstin', label: 'Vendor GSTIN', message: err, severity: 'error' })
    }
  } else {
    issues.push({
      field: 'vendor.gstin',
      label: 'Vendor GSTIN',
      message: 'Missing — GST input credit cannot be claimed without it.',
      severity: 'warning',
    })
  }

  const total = num(d.total_amount)
  if (total === null) {
    issues.push({ field: 'total_amount', label: 'Total amount', message: 'Required before approval.', severity: 'error' })
  } else if (total <= 0) {
    issues.push({ field: 'total_amount', label: 'Total amount', message: 'Must be greater than zero.', severity: 'error' })
  }

  if (!str(d.invoice_date)) {
    issues.push({ field: 'invoice_date', label: 'Bill date', message: 'Required before approval.', severity: 'error' })
  }

  if (!str(d.invoice_number)) {
    issues.push({
      field: 'invoice_number',
      label: 'Invoice number',
      message: 'Missing — duplicate detection relies on it.',
      severity: 'warning',
    })
  }

  if (!category.trim()) {
    issues.push({ field: 'category', label: 'Category', message: 'Required for expense reporting.', severity: 'error' })
  }

  // Arithmetic sanity: subtotal must not exceed the total. A ₹1 tolerance
  // absorbs rounding in the source document.
  const subtotal = num(d.subtotal)
  if (subtotal !== null && total !== null && subtotal - total > 1) {
    issues.push({
      field: 'subtotal',
      label: 'Subtotal',
      message: `Subtotal (${subtotal}) is greater than the total (${total}).`,
      severity: 'error',
    })
  }

  // A due date before the bill date is almost always a mis-read year.
  const billDate = str(d.invoice_date)
  const dueDate = str(d.due_date)
  if (billDate && dueDate) {
    const b = new Date(billDate)
    const u = new Date(dueDate)
    if (!Number.isNaN(b.getTime()) && !Number.isNaN(u.getTime()) && u < b) {
      issues.push({
        field: 'due_date',
        label: 'Due date',
        message: 'Falls before the bill date — check the year.',
        severity: 'warning',
      })
    }
  }

  return issues
}

export const blockingIssues = (issues: ValidationIssue[]) => issues.filter(i => i.severity === 'error')
