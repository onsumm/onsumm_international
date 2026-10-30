/**
 * Rupee amount entry with en-IN separators ("4,72,000.50") while you type.
 *
 * A `type="number"` input can't display separators, so this drives a text
 * input instead: spread the returned props onto an <input>/<Input> and the
 * caller still deals only in plain numbers (NaN when empty).
 */

import { useEffect, useState, type ChangeEvent } from 'react'
import { displayCurrency, localeForCurrency } from './format'

/** "472000.5" → "4,72,000.5" (or "472,000.5" for a non-INR tenant): grouping
 *  on the whole part in the tenant's money locale, the decimal part kept as
 *  typed (max two places) so "4,720." survives mid-entry. */
export function groupAmount(raw: string): string {
  const clean = raw.replace(/[^\d.]/g, '')
  const dot = clean.indexOf('.')
  const whole = (dot < 0 ? clean : clean.slice(0, dot)).replace(/^0+(?=\d)/, '')
  const grouped = whole ? Number(whole).toLocaleString(localeForCurrency(displayCurrency())) : (dot < 0 ? '' : '0')
  return dot < 0 ? grouped : `${grouped}.${clean.slice(dot + 1).replace(/\./g, '').slice(0, 2)}`
}

export const parseAmount = (text: string) => (text === '' ? NaN : parseFloat(text.replace(/,/g, '')))

export function useAmountField(value: number, onValue: (v: number) => void) {
  const [text, setText] = useState(() => (Number.isFinite(value) ? groupAmount(String(value)) : ''))

  // Resync only when the parent's number moves away from what's typed.
  useEffect(() => {
    const typed = parseAmount(text)
    if (Number.isFinite(value) ? typed !== value : text !== '' && Number.isFinite(typed)) {
      setText(Number.isFinite(value) ? groupAmount(String(value)) : '')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  const onChange = (e: ChangeEvent<HTMLInputElement>) => {
    const el = e.target
    // Keep the caret after the same number of significant chars once commas shift.
    const sig = el.value.slice(0, el.selectionStart ?? el.value.length).replace(/[^\d.]/g, '').length
    const next = groupAmount(el.value)
    setText(next)
    onValue(parseAmount(next))
    requestAnimationFrame(() => {
      let pos = 0
      for (let seen = 0; pos < next.length && seen < sig; pos++) if (next[pos] !== ',') seen++
      el.setSelectionRange(pos, pos)
    })
  }

  return { type: 'text', inputMode: 'decimal', autoComplete: 'off', value: text, onChange } as const
}
