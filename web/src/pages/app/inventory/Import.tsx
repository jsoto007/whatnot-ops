import { useState } from 'react'
import { ApiError, api, type ImportRow } from '../../../lib/api'

/** The team's existing spreadsheet: preview first, nothing changes until Apply. */
export function Import({ onDone }: { onDone: () => Promise<unknown> }) {
  const [text, setText] = useState<string | null>(null)
  const [fileName, setFileName] = useState('')
  const [plan, setPlan] = useState<{ rows: ImportRow[]; summary: Record<string, number> } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async (apply: boolean) => {
    if (!text) return
    setBusy(true)
    setError(null)
    try {
      const r = await api<{ rows: ImportRow[]; summary: Record<string, number> }>('POST', '/api/inventory/import', { csv: text, apply })
      setPlan(r)
      if (apply) {
        setText(null)
        setFileName('')
        await onDone()
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'import failed')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="card import">
      <h3>Import the inventory sheet</h3>
      <p className="muted small">
        CSV with an <b>Item</b> column and a <b>Current Stock</b> (or Start of Show Inventory) column. Existing items are set to the
        sheet's count (a negative count is kept and shows as short); new items are created. Nothing changes until you
        apply.
      </p>
      <div className="inline-form">
        <label className="btn">
          {fileName || 'Choose CSV'}
          <input
            type="file"
            accept=".csv,text/csv"
            hidden
            onChange={async (e) => {
              const f = e.target.files?.[0]
              if (!f) return
              setFileName(f.name)
              setText(await f.text())
              setPlan(null)
            }}
          />
        </label>
        <button className="btn" disabled={!text || busy} onClick={() => run(false)}>
          Preview
        </button>
        <button className="btn primary" disabled={!plan || busy || !text} onClick={() => run(true)} data-testid="apply-import">
          Apply
        </button>
      </div>
      {error && <p className="form-error">{error}</p>}
      {plan && (
        <>
          <p className="small" data-testid="import-summary">
            {Object.entries(plan.summary)
              .map(([k, v]) => `${v} ${k}`)
              .join(' · ')}
          </p>
          <table className="table small">
            <thead>
              <tr>
                <th>Item</th>
                <th className="num">Sheet</th>
                <th className="num">Before</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {plan.rows.map((r, i) => (
                <tr key={i} className={r.action === 'skip' ? 'disabled' : ''}>
                  <td>{r.item}</td>
                  <td className="num">{r.quantity}</td>
                  <td className="num">{r.before ?? '—'}</td>
                  <td>
                    {r.action}
                    {r.error ? ` (${r.error})` : ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}
