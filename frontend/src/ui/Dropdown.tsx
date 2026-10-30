import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { Check, ChevronDown } from 'lucide-react'

export interface DropdownOption<T extends string | number> {
  value: T
  label: string
}

/**
 * A compact single-choice menu in our own style — a button showing the value,
 * opening a list of options (instead of the browser's native <select>, whose
 * list cannot be styled). Opens upward when there is no room below, so it works
 * at the foot of a table. Keyboard: ↑ / ↓ move, Enter or Space picks, Escape
 * (or a click outside) closes.
 */
export function Dropdown<T extends string | number>({
  value, options, onChange, ariaLabel, disabled, className,
}: {
  value: T
  options: DropdownOption<T>[]
  onChange: (value: T) => void
  ariaLabel: string
  disabled?: boolean
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [upward, setUpward] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const listId = useId()
  const current = options.find(o => o.value === value) ?? options[0]

  // A click anywhere else closes it.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const show = () => {
    if (disabled) return
    const r = rootRef.current?.getBoundingClientRect()
    // Each option is ~36px; open upward if the list would not fit below.
    setUpward(!!r && window.innerHeight - r.bottom < options.length * 36 + 16)
    setActive(Math.max(options.findIndex(o => o.value === value), 0))
    setOpen(true)
  }

  const pick = (i: number) => {
    const o = options[i]
    if (o) onChange(o.value)
    setOpen(false)
  }

  const onKey = (e: KeyboardEvent) => {
    if (!open) {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) { e.preventDefault(); show() }
      return
    }
    if (e.key === 'Escape') { e.preventDefault(); setOpen(false) }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(a + 1, options.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(a - 1, 0)) }
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(active) }
    else if (e.key === 'Tab') setOpen(false)
  }

  return (
    <div className={`u-dd${className ? ` ${className}` : ''}`} ref={rootRef}>
      <button
        type="button"
        className="u-dd__btn"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={`${ariaLabel}: ${current?.label}`}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={onKey}
      >
        <span className="u-dd__value">{current?.label}</span>
        <ChevronDown size={14} aria-hidden="true" className={`u-dd__chev${open ? ' u-dd__chev--open' : ''}`} />
      </button>
      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-label={ariaLabel}
          className={`u-dd__list${upward ? ' u-dd__list--up' : ''}`}
        >
          {options.map((o, i) => {
            const selected = o.value === value
            return (
              <li
                key={String(o.value)}
                role="option"
                aria-selected={selected}
                className={`u-dd__opt${i === active ? ' u-dd__opt--active' : ''}${selected ? ' u-dd__opt--on' : ''}`}
                onMouseEnter={() => setActive(i)}
                // mousedown, not click: picks before the button loses focus.
                onMouseDown={e => { e.preventDefault(); pick(i) }}
              >
                <span>{o.label}</span>
                {selected && <Check size={13} strokeWidth={3} aria-hidden="true" />}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
