import { useId } from 'react'
import type { InputHTMLAttributes, SelectHTMLAttributes, TextareaHTMLAttributes, ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'

interface FieldShellProps {
  label: string
  hint?: string
  error?: string | null
  required?: boolean
  children: (ids: { id: string; describedBy?: string; invalid: boolean }) => ReactNode
}

/**
 * Binds a <label> to its control and wires aria-describedby / aria-invalid.
 *
 * The audit found 36 labels with no associated control — they read as plain
 * text to a screen reader, so the user hears an unlabelled edit box. Going
 * through this shell makes that impossible.
 */
export function FieldShell({ label, hint, error, required, children }: FieldShellProps) {
  const id = useId()
  const hintId = `${id}-hint`
  const errId = `${id}-err`
  const describedBy = [error ? errId : null, hint ? hintId : null].filter(Boolean).join(' ') || undefined

  return (
    <div className="u-field">
      <label className="u-field__label" htmlFor={id}>
        {label}
        {required && (
          <span className="u-field__req" aria-hidden="true">
            *
          </span>
        )}
      </label>
      {children({ id, describedBy, invalid: !!error })}
      {hint && !error && (
        <span className="u-field__hint" id={hintId}>
          {hint}
        </span>
      )}
      {error && (
        <span className="u-field__error" id={errId} role="alert">
          <AlertTriangle size={12} aria-hidden="true" />
          {error}
        </span>
      )}
    </div>
  )
}

type NativeInput = Omit<InputHTMLAttributes<HTMLInputElement>, 'className' | 'id'>

export interface InputProps extends NativeInput {
  label: string
  hint?: string
  error?: string | null
  mono?: boolean
}

export function Input({ label, hint, error, mono, required, ...rest }: InputProps) {
  return (
    <FieldShell label={label} hint={hint} error={error} required={required}>
      {({ id, describedBy, invalid }) => (
        <input
          id={id}
          className={`u-input${invalid ? ' u-input--invalid' : ''}${mono ? ' u-input--mono' : ''}`}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          required={required}
          {...rest}
        />
      )}
    </FieldShell>
  )
}

export interface TextareaProps
  extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'className' | 'id'> {
  label: string
  hint?: string
  error?: string | null
}

export function Textarea({ label, hint, error, required, rows = 3, ...rest }: TextareaProps) {
  return (
    <FieldShell label={label} hint={hint} error={error} required={required}>
      {({ id, describedBy, invalid }) => (
        <textarea
          id={id}
          rows={rows}
          className={`u-textarea${invalid ? ' u-textarea--invalid' : ''}`}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          required={required}
          {...rest}
        />
      )}
    </FieldShell>
  )
}

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'className' | 'id'> {
  label: string
  hint?: string
  error?: string | null
  options: { value: string; label: string; disabled?: boolean }[]
  placeholder?: string
}

export function Select({ label, hint, error, options, placeholder, required, ...rest }: SelectProps) {
  return (
    <FieldShell label={label} hint={hint} error={error} required={required}>
      {({ id, describedBy, invalid }) => (
        <select
          id={id}
          className={`u-select${invalid ? ' u-select--invalid' : ''}`}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          required={required}
          {...rest}
        >
          {placeholder && <option value="">{placeholder}</option>}
          {options.map(o => (
            <option key={o.value} value={o.value} disabled={o.disabled}>
              {o.label}
            </option>
          ))}
        </select>
      )}
    </FieldShell>
  )
}

export interface CheckboxProps extends Omit<NativeInput, 'type'> {
  label: ReactNode
  /** Renders the mixed state used by "select all" headers. */
  indeterminate?: boolean
}

export function Checkbox({ label, indeterminate, disabled, ...rest }: CheckboxProps) {
  return (
    <label className={`u-check${disabled ? ' u-check--disabled' : ''}`}>
      <input
        type="checkbox"
        disabled={disabled}
        ref={el => {
          if (el) el.indeterminate = !!indeterminate
        }}
        {...rest}
      />
      {label}
    </label>
  )
}
