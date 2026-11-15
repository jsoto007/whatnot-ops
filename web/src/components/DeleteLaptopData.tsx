import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError, api, apiGet, type LaptopDataDeleted, type LaptopDataPreview } from '../lib/api'
import { plural } from '../lib/format'
import { Modal } from './Modal'

/**
 * Owner only: delete what a laptop recorded for a show (its chat and everything the app made
 * from it), never Whatnot's own data. Shows what goes and what stays, says why it cannot be
 * done now when it cannot, and asks for "delete" typed out, because it cannot be undone.
 */
export function DeleteLaptopData({ showId, label, onClose, onDone }: { showId: string; label: string; onClose: () => void; onDone: (r: LaptopDataDeleted) => void }) {
  const qc = useQueryClient()
  const url = `/api/shows/${showId}/laptop-data`
  const q = useQuery({ queryKey: ['laptop-data', showId], queryFn: () => apiGet<LaptopDataPreview>(url), retry: false })
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const p = q.data
  const blocked = !p || p.blockers.length > 0
  async function remove() {
    setBusy(true)
    setError('')
    try {
      const r = await api<LaptopDataDeleted>('DELETE', url, { confirm: 'delete' })
      // Close (and leave a show that is gone) first, then refresh what changed: sales,
      // stock, orders and the dashboard all lost these rows.
      qc.removeQueries({ queryKey: ['laptop-data', showId] })
      onDone(r)
      void qc.invalidateQueries()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not delete it. Try again.')
      setBusy(false)
      void q.refetch() // what stops it now (chat that just arrived, a parse) is shown again
    }
  }
  return (
    <Modal
      title="Delete this show's laptop data"
      sub={label}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn danger-solid" disabled={busy || blocked || typed.trim().toLowerCase() !== 'delete'} onClick={remove} data-testid="laptop-data-delete">
            {busy ? 'Deleting…' : 'Delete laptop data'}
          </button>
        </>
      }
    >
      {q.isPending && <p className="muted">Looking at what the laptop recorded…</p>}
      {q.isError && <p className="form-error">{q.error instanceof ApiError ? q.error.message : 'Could not look at this show.'}</p>}
      {p && (
        <div data-testid="laptop-data-preview">
          <p style={{ marginTop: 0 }}>This deletes what the laptop recorded for this show, and cannot be undone:</p>
          <ul className="small">
            <li>{plural(p.messages, 'chat line', 'chat lines')} and what the app read from them</li>
            <li>
              {plural(p.sales, 'sale', 'sales')} made from the chat
              {p.standing ? `; ${p.standing === 1 ? 'the one still standing is closed and its stock goes' : `the ${p.standing} still standing are closed and their stock goes`} back on the shelf` : ''}
            </li>
            {p.moved > 0 && (
              <li>
                {plural(p.moved, "sale moves to the other laptop's recording", "sales move to the other laptop's recording")} of this show, which saw the same win. Nothing changes in stock.
              </li>
            )}
            {p.inPackage > 0 && (
              <li className="danger-ink">
                {plural(p.inPackage, 'sale was', 'sales were')} already picked, packed or shipped: that stock is not counted back. Count it in Inventory if it is on the shelf.
              </li>
            )}
            <li>
              {plural(p.orders, 'order', 'orders')} and {plural(p.reviews, 'question', 'questions')} in Review
            </li>
          </ul>
          <p className="small">
            <b>Kept:</b> everything from Whatnot itself: its shows, orders, payments, shipments and fees
            {p.keptFromWhatnot ? `, and ${plural(p.keptFromWhatnot, 'sale', 'sales')} Whatnot's orders made or vouched for (the show stays for them)` : ''}. A laptop that sends this recording again is ignored.
          </p>
          {p.keptFromWhatnot > 0 && (
            <p className="small muted" data-testid="laptop-data-by-hand">
              The kept sales lose their chat: one still waiting for its product (the winner's pick in chat) must have its product named by hand.
            </p>
          )}
          {p.blockers.map((b) => (
            <p key={b} className="card small warn-card" role="status" data-testid="laptop-data-blocker">
              {b}
            </p>
          ))}
          {!p.blockers.length && (
            <label className="field">
              <span>
                Type <b>delete</b> to confirm
              </span>
              <input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" aria-label="Type delete to confirm" data-testid="laptop-data-confirm" />
            </label>
          )}
        </div>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </Modal>
  )
}
