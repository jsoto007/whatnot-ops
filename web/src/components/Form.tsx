import { useState, type FormEvent, type ReactNode } from 'react'
import { ApiError } from '../lib/api'

interface Props {
  onSubmit: () => Promise<void>
  submitLabel: string
  children: ReactNode
  className?: string
}

/** A form that runs an async submit, disables itself meanwhile and shows the API's error message. */
export function Form({ onSubmit, submitLabel, children, className }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await onSubmit()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Try again.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <form onSubmit={submit} className={className} aria-busy={busy}>
      {children}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" className="btn primary" disabled={busy}>
        {busy ? '…' : submitLabel}
      </button>
    </form>
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  )
}
