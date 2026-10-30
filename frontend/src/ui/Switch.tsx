export interface SwitchProps {
  checked: boolean
  onChange: (next: boolean) => void
  /** Accessible name. Required — a switch with no name is unusable non-visually. */
  label: string
  /** Set false to render the name visually alongside (default renders it hidden). */
  showLabel?: boolean
  disabled?: boolean
  /** Extra context announced with the name, e.g. "watching vendor@acme.com". */
  description?: string
}

/**
 * A real, keyboard-operable switch.
 *
 * The audit found the Agent Hub master toggle — the control that starts and
 * stops an agent reading a vendor's mailbox — built as `<span onClick>` inside a
 * `<label>` with no form control: not focusable, no role, no state. A keyboard
 * user could not turn the agent off. Same for every per-contact toggle.
 *
 * `<button role="switch">` gets focus, Space/Enter activation and state
 * announcement for free.
 */
export function Switch({ checked, onChange, label, showLabel, disabled, description }: SwitchProps) {
  const control = (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={showLabel ? undefined : label}
      className="u-switch"
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span className="u-switch__thumb" />
    </button>
  )

  if (!showLabel && !description) return control

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--sp-2)' }}>
      {control}
      <span>
        {showLabel && (
          <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--ink-700)', fontWeight: 500 }}>{label}</span>
        )}
        {description && (
          <span style={{ display: 'block', fontSize: 'var(--fs-2xs)', color: 'var(--ink-500)' }}>
            {description}
          </span>
        )}
      </span>
    </span>
  )
}
