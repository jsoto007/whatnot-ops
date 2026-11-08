import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError, api, money, type ReconcileRow } from '../../lib/api'
import { reconcileActionLabel, summaryWords } from '../../lib/labels'

interface Plan {
  rows: ReconcileRow[]
  uncovered: number[]
  summary: Record<string, number>
}

/** Upload the Whatnot payment / order export for one show: preview, then apply. */
export function Reconcile({ showId }: { showId: string }) {
  const qc = useQueryClient()
  const [text, setText] = useState<string | null>(null)
  const [fileName, setFileName] = useState('')
  const [plan, setPlan] = useState<Plan | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async (apply: boolean) => {
    if (!text) return
    setBusy(true)
    setError(null)
    try {
      const r = await api<Plan>('POST', `/api/orders/shows/${showId}/reconcile`, { csv: text, apply })
      setPlan(r)
      if (apply) {
        setText(null)
        setFileName('')
        await qc.invalidateQueries()
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'import failed')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="card import">
      <h3>Reconcile payments</h3>
      <p className="muted small">
        Upload Whatnot's order export for this show (columns like <b>Buyer</b>, <b>Product</b>, <b>Status</b>). Whatnot charges at the
        win, so sales are already paid: paid rows confirm them (and restore one whose card was declined), failed or declined rows
        mark the payment declined, cancelled or refunded rows cancel the sale. Either way the stock goes back. Preview first.
      </p>
      <div className="inline-form">
        <label className="btn">
          {fileName || 'Choose CSV'}
          <input
            type="file"
            accept=".csv,text/csv"
            hidden
            data-testid="reconcile-file"
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
        <button className="btn primary" disabled={!plan || busy || !text} onClick={() => run(true)} data-testid="apply-reconcile">
          Apply
        </button>
      </div>
      {error && <p className="form-error">{error}</p>}
      {plan && (
        <>
          <p className="small" data-testid="reconcile-summary">
            {summaryWords(plan.summary, reconcileActionLabel)}
          </p>
          <table className="table small">
            <thead>
              <tr>
                <th>Buyer</th>
                <th>Item</th>
                <th className="num">Qty</th>
                <th>Price</th>
                <th>File says</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {plan.rows.map((r, i) => (
                <tr key={i} className={r.action === 'skip' || r.action === 'unmatched' ? 'low' : ''}>
                  <td>{r.buyer}</td>
                  <td>{r.item}</td>
                  <td className="num">{r.quantity}</td>
                  <td>{money(r.priceCents)}</td>
                  <td>{r.status}</td>
                  <td>
                    {reconcileActionLabel(r.action)}
                    {r.note ? <span className="muted"> ({r.note})</span> : null}
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
