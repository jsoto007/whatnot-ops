import { useMemo, useState } from 'react'
import { Sheet } from '../../../components/Sheet'
import { ApiError, api, type CountResult, type StockLevel } from '../../../lib/api'
import { plural, requestId } from '../../../lib/format'

const diffText = (d: number) => (d === 0 ? 'matches' : `${d > 0 ? '+' : '−'}${Math.abs(d)}`)

/**
 * A shelf count: type what is physically there, see the difference before saving.
 * Blank rows are skipped, so counting one shelf at a time is fine.
 */
export function Count({ skus, onClose, onDone }: { skus: StockLevel[]; onClose: () => void; onDone: (r: CountResult) => void }) {
  const items = useMemo(() => skus.filter((s) => s.active).slice().sort((a, b) => a.productName.localeCompare(b.productName) || a.code.localeCompare(b.code)), [skus])
  const [counted, setCounted] = useState<Record<number, string>>({})
  const [q, setQ] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [countId] = useState(requestId)

  const entries = items.flatMap((s) => {
    const raw = (counted[s.id] ?? '').trim()
    if (raw === '') return []
    const n = /^\d+$/.test(raw) ? parseInt(raw, 10) : NaN
    return [{ sku: s, n, diff: Number.isNaN(n) ? 0 : n - s.onHand }]
  })
  const bad = entries.find((e) => Number.isNaN(e.n))
  const changed = entries.filter((e) => e.diff !== 0)
  const shown = q.trim() ? items.filter((s) => `${s.productName} ${s.name} ${s.code}`.toLowerCase().includes(q.trim().toLowerCase())) : items

  const submit = async () => {
    setError(null)
    if (bad) return setError(`${bad.sku.code}: a count is a whole number, 0 or more`)
    if (!entries.length) return setError('Type at least one count')
    setBusy(true)
    try {
      onDone(await api<CountResult>('POST', '/api/inventory/count', { countId, note: note.trim() || undefined, lines: entries.map((e) => ({ skuId: e.sku.id, counted: e.n })) }))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not save. Try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet
      title="Count stock"
      sub="Type what is on the shelf right now. Leave an item blank to skip it."
      onClose={onClose}
      wide
      footer={
        <>
          <span className="muted small" data-testid="count-total">
            {plural(entries.length, 'item counted', 'items counted')} · {plural(changed.length, 'change', 'changes')}
          </span>
          <button type="button" className="btn primary" onClick={submit} disabled={busy || entries.length === 0} data-testid="count-submit">
            {busy ? 'Saving…' : changed.length ? `Save count (${plural(changed.length, 'change', 'changes')})` : 'Save count'}
          </button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        {items.length > 8 && <input className="sheet-search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find an item" aria-label="Find an item to count" />}
        <table className="table count-table">
          <thead>
            <tr>
              <th>Item</th>
              <th className="num">Expected</th>
              <th className="num">Counted</th>
              <th className="num hide-sm">Difference</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((s) => {
              const raw = counted[s.id] ?? ''
              const n = /^\d+$/.test(raw.trim()) ? parseInt(raw, 10) : null
              const diff = n === null ? null : n - s.onHand
              return (
                <tr key={s.id} className={diff ? 'changed' : ''}>
                  <td>
                    <b>{s.productName}</b> <span className="muted small">{s.code}</span>
                    {s.reserved > 0 && <div className="muted small">{s.reserved} held for open sales</div>}
                  </td>
                  <td className="num">{s.onHand}</td>
                  <td className="num">
                    <input
                      className="count-input"
                      value={raw}
                      onChange={(e) => setCounted((c) => ({ ...c, [s.id]: e.target.value }))}
                      inputMode="numeric"
                      placeholder={String(Math.max(0, s.onHand))}
                      aria-label={`Counted ${s.code}`}
                      size={5}
                    />
                    {diff !== null && <div className={'diff show-sm' + (diff ? (diff > 0 ? ' up' : ' down') : '')}>{diffText(diff)}</div>}
                  </td>
                  <td className={'num diff hide-sm' + (diff ? (diff > 0 ? ' up' : ' down') : '')}>{diff === null ? '' : diffText(diff)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <label className="field">
          <span>Note</span>
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Tuesday shelf count" maxLength={200} />
        </label>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" hidden />
      </form>
    </Sheet>
  )
}
