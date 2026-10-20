import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiGet, type Show } from '../../lib/api'

export function Reports() {
  const shows = useQuery({ queryKey: ['shows'], queryFn: () => apiGet<{ shows: Show[] }>('/api/shows') })
  const [showId, setShowId] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  return (
    <section>
      <h2>Reports</h2>
      <p className="muted">
        Excel workbooks with these sheets: sales by show, customers, products sold, inventory remaining, cancellations and failed
        payments, revenue, fulfilment list, stock movements.
      </p>
      <div className="card">
        <h3>One show</h3>
        <div className="inline-form">
          <label className="field">
            <span>Show</span>
            <select value={showId} onChange={(e) => setShowId(e.target.value)} aria-label="Report show">
              <option value="">Choose…</option>
              {shows.data?.shows.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.date} · {s.messageCount} messages
                </option>
              ))}
            </select>
          </label>
          <a className={'btn primary' + (showId ? '' : ' disabled')} href={showId ? `/api/reports/show/${showId}.xlsx` : undefined} data-testid="download-show" aria-disabled={!showId}>
            Download .xlsx
          </a>
        </div>
      </div>
      <div className="card">
        <h3>All shows in a date range</h3>
        <div className="inline-form">
          <label className="field">
            <span>From</span>
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </label>
          <label className="field">
            <span>To</span>
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </label>
          <a className="btn primary" href={`/api/reports/overview.xlsx?from=${from}&to=${to}`} data-testid="download-overview">
            Download .xlsx
          </a>
        </div>
      </div>
    </section>
  )
}
