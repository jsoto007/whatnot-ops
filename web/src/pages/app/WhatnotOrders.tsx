import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api, apiGet, money, type WhatnotCompare } from '../../lib/api'
import { fmtWhen, plural } from '../../lib/format'
import { saleStatusLabel, summaryWords, whatnotOrderActionLabel, whatnotPaymentLabel, whatnotShowStatusLabel } from '../../lib/labels'

const CHANGES = ['mark_paid', 'cancel', 'decline', 'record'] as const

/** A show's Payments tab, first half: Whatnot's own orders next to the app's sales. Nothing
 * changes until someone presses Apply (unless the shop chose to apply them as they arrive). */
export function WhatnotOrders({ showId, canApply }: { showId: string; canApply: boolean }) {
  const qc = useQueryClient()
  const key = ['whatnot-orders', showId]
  const q = useQuery({ queryKey: key, queryFn: () => apiGet<WhatnotCompare>(`/api/shows/${showId}/whatnot`) })
  const [choice, setChoice] = useState('')
  const [done, setDone] = useState<string | null>(null)
  const act = useMutation({
    mutationFn: (a: { path: 'refresh' | 'apply' | 'link'; body?: unknown }) => api<WhatnotCompare>('POST', `/api/shows/${showId}/whatnot/${a.path}`, a.body ?? {}),
    onSuccess: async (data, a) => {
      qc.setQueryData(key, data)
      if (a.path === 'apply') {
        setDone(summaryWords(data.applied ?? {}, whatnotOrderActionLabel) || 'Nothing needed changing.')
        await qc.invalidateQueries({ predicate: (query) => query.queryKey[0] !== 'whatnot-orders' })
      } else {
        setDone(a.path === 'refresh' && data.queued ? 'Reading from Whatnot now. This page shows the result in a minute.' : null)
      }
    },
  })
  const d = q.data
  if (!d || !d.connected) return null
  const error = act.error instanceof ApiError ? act.error.message : act.error ? 'That did not work. Try again.' : null
  const ws = d.show
  const rows = d.rows ?? []
  const uncovered = d.uncovered ?? []
  const summary = d.summary ?? {}
  const toChange = CHANGES.reduce((n, k) => n + (summary[k] ?? 0), 0) + (d.complete ? uncovered.length : 0)
  const noBuyers = (d.info?.noBuyer ?? 0) > 0

  const matcher = canApply && (d.choices?.length ?? 0) > 0 && d.link !== 'made' && (
    <span className="inline-form">
      <select value={choice} onChange={(e) => setChoice(e.target.value)} aria-label="Show on Whatnot">
        <option value="">{ws ? 'Change to…' : 'Choose the show on Whatnot…'}</option>
        {d.choices!.map((c) => (
          <option key={c.id} value={c.id}>
            {c.title || 'Untitled show'}
            {c.startsAt ? ` · ${new Date(c.startsAt).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}` : ''}
          </option>
        ))}
        {ws && <option value="none">None of these</option>}
      </select>
      <button type="button" className="btn" disabled={!choice || act.isPending} onClick={() => act.mutate({ path: 'link', body: { whatnotShowId: choice === 'none' ? null : Number(choice) } })} data-testid="whatnot-link">
        Match
      </button>
    </span>
  )

  return (
    <div className="card import" data-testid="whatnot-orders">
      <h3>Whatnot's orders</h3>
      {!ws ? (
        <>
          <p className="muted small" data-testid="whatnot-orders-state">
            This show is not matched to a show on Whatnot yet. It is matched on its own once the app has read Whatnot's show list; you can also choose it here.
          </p>
          {matcher}
        </>
      ) : (
        <>
          <p className="small" data-testid="whatnot-orders-state">
            <b>{ws.title || 'Untitled show'}</b> · {whatnotShowStatusLabel(ws.status)} · {ws.ordersReadAt ? `orders read ${fmtWhen(ws.ordersReadAt)}` : 'orders not read yet'}
            {d.info ? ` · ${plural(d.info.orders, 'order', 'orders')}${d.info.giveaways ? `, ${plural(d.info.giveaways, 'giveaway', 'giveaways')}` : ''}` : ''}
          </p>
          <p className="muted small">
            {d.mode === 'auto'
              ? 'Whatnot is the record of what was sold and paid: its orders are applied here as they arrive.'
              : 'Whatnot is the record of what was sold and paid. Below is what its orders would change here; nothing changes until someone presses Apply.'}
            {!d.complete && ' Until the show has ended and all its orders are read, a sale with no order is not called out.'}
          </p>
          {noBuyers && (
            <p className="form-error small" data-testid="whatnot-no-buyers">
              Whatnot is not telling the app who bought {plural(d.info!.noBuyer, 'item', 'items')}, so they cannot be matched. The access token needs the permission to read customers.
            </p>
          )}
          {canApply && (
            <div className="inline-form">
              <button type="button" className="btn" disabled={act.isPending} onClick={() => act.mutate({ path: 'refresh' })} data-testid="whatnot-refresh">
                Read again from Whatnot
              </button>
              <button type="button" className="btn primary" disabled={act.isPending || noBuyers || toChange === 0} onClick={() => act.mutate({ path: 'apply' })} data-testid="whatnot-apply">
                {toChange ? `Apply ${plural(toChange, 'change', 'changes')}` : 'Nothing to apply'}
              </button>
              {matcher}
            </div>
          )}
          {done && (
            <p className="small" role="status" data-testid="whatnot-applied">
              {done}
            </p>
          )}
          {rows.length > 0 && (
            <>
              <p className="small" data-testid="whatnot-summary">
                {summaryWords(summary, whatnotOrderActionLabel)}
              </p>
              <table className="table small" data-testid="whatnot-rows">
                <thead>
                  <tr>
                    <th>Buyer</th>
                    <th>Listing</th>
                    <th className="num">Qty</th>
                    <th>Price</th>
                    <th>Whatnot says</th>
                    <th>Here</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i} className={r.action === 'unchanged' || r.action === 'skip' ? 'low' : ''}>
                      <td>{r.buyer || '—'}</td>
                      <td>{r.item}</td>
                      <td className="num">{r.quantity}</td>
                      <td>{money(r.priceCents)}</td>
                      <td>{whatnotPaymentLabel(r.status)}</td>
                      <td>
                        {whatnotOrderActionLabel(r.action)}
                        {r.note ? <span className="muted"> ({r.note})</span> : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          {uncovered.length > 0 && (
            <>
              <h4>Sales here with no order on Whatnot</h4>
              <p className="muted small">
                {d.complete
                  ? 'The show is over and every order was read: these may be wins that were never charged, or lines that were not real sales. Apply puts each one in Review.'
                  : 'Their orders may simply not have arrived yet.'}
              </p>
              <table className="table small" data-testid="whatnot-uncovered">
                <tbody>
                  {uncovered.map((s) => (
                    <tr key={s.saleId}>
                      <td>{s.buyer}</td>
                      <td>{s.item}</td>
                      <td className="num">{s.quantity}</td>
                      <td>{money(s.priceCents)}</td>
                      <td>{saleStatusLabel(s.status)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
      {error && <p className="form-error small">{error}</p>}
    </div>
  )
}
