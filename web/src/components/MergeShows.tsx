import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError, api, apiGet, type ShowMergePreview, type ShowMerged } from '../lib/api'
import { invalidateSales } from '../lib/invalidate'
import { plural } from '../lib/format'
import { Modal } from './Modal'

/**
 * Owner only: one show on Whatnot became two shows here (the app made one from Whatnot's
 * orders before a laptop started recording). Merging moves everything into the laptop's
 * recording: sales, each buyer's order (two orders of one buyer become one), questions and
 * Whatnot's parcel lines. Nothing changes in stock. Says why it cannot be done now when it
 * cannot.
 */
export function MergeShows({ showId, label, onClose, onDone }: { showId: string; label: string; onClose: () => void; onDone: (r: ShowMerged) => void }) {
  const qc = useQueryClient()
  const url = `/api/shows/${showId}/merge`
  const q = useQuery({ queryKey: ['show-merge', showId], queryFn: () => apiGet<ShowMergePreview>(url), retry: false })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const p = q.data
  const blocked = !p || p.blockers.length > 0 || p.folded.length === 0
  async function merge() {
    setBusy(true)
    setError('')
    try {
      const r = await api<ShowMerged>('POST', url, { confirm: 'merge' })
      qc.removeQueries({ queryKey: ['show-merge', showId] })
      onDone(r)
      void invalidateSales(qc)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not merge them. Try again.')
      setBusy(false)
      void q.refetch()
    }
  }
  return (
    <Modal
      title="Merge into one show"
      sub={label}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn primary" disabled={busy || blocked} onClick={merge} data-testid="show-merge-confirm">
            {busy ? 'Merging…' : 'Merge'}
          </button>
        </>
      }
    >
      {q.isPending && <p className="muted">Looking at both shows…</p>}
      {q.isError && <p className="form-error">{q.error instanceof ApiError ? q.error.message : 'Could not look at this show.'}</p>}
      {p && (
        <div data-testid="show-merge-preview">
          {p.folded.length === 0 ? (
            <p style={{ marginTop: 0 }}>
              {p.laptops.length > 0 ? 'Each of these shows was recorded by a laptop, so they stay apart.' : 'This show is already one show here.'}
            </p>
          ) : (
            <>
              <p style={{ marginTop: 0 }}>
                This is one show on Whatnot. Merging moves everything into the show the laptop recorded, and cannot be undone:
              </p>
              <ul className="small">
                <li>{plural(p.sales, 'sale', 'sales')} and {plural(p.orders, 'order', 'orders')}</li>
                {p.joined > 0 && <li>{plural(p.joined, "buyer's two orders become one", "buyers' two orders become one each")}: one slip, one box</li>}
                <li>
                  {plural(p.reviews, 'question', 'questions')} in Review and {plural(p.lots, 'item', 'items')} of Whatnot's parcels
                </li>
                {p.laptops.length > 0 && <li>{plural(p.laptops.length, "other laptop's recording stays as it is", "other laptops' recordings stay as they are")}</li>}
              </ul>
              <p className="small">Nothing changes in stock: every sale keeps its product and its payment.</p>
            </>
          )}
          {p.blockers.map((b) => (
            <p key={b} className="card small warn-card" role="status" data-testid="show-merge-blocker">
              {b}
            </p>
          ))}
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
