import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../../auth/useAuth'
import { ApiError, api, apiGet, type ImportRow, type Movement, type Sku } from '../../lib/api'

type Level = Sku & { productName: string }

export function Inventory() {
  const { can } = useAuth()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['inventory'], queryFn: () => apiGet<{ skus: Level[] }>('/api/inventory') })
  const refresh = () => Promise.all([qc.invalidateQueries({ queryKey: ['inventory'] }), qc.invalidateQueries({ queryKey: ['products'] })])
  const [open, setOpen] = useState<number | null>(null)
  const write = can('inventory.write')
  if (q.isPending) return <p className="muted">Loading…</p>
  if (q.isError) return <p className="form-error">Could not load inventory.</p>
  const low = q.data.skus.filter((s) => s.low).length
  return (
    <section>
      <h2>Inventory</h2>
      <p className="muted">
        {q.data.skus.length} SKUs · {low} low
      </p>
      {write && <Import onDone={refresh} />}
      {q.data.skus.length === 0 ? (
        <p className="card muted">Nothing in stock yet. Import the inventory sheet or add products.</p>
      ) : (
        <table className="table" data-testid="levels">
          <thead>
            <tr>
              <th>Product</th>
              <th>SKU</th>
              <th className="num">On hand</th>
              <th className="num">Reserved</th>
              <th className="num">Available</th>
              {write && <th>Adjust</th>}
              <th></th>
            </tr>
          </thead>
          <tbody>
            {q.data.skus.map((s) => (
              <LevelRow key={s.id} sku={s} write={write} open={open === s.id} onToggle={() => setOpen(open === s.id ? null : s.id)} onChange={refresh} />
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

function LevelRow({ sku, write, open, onToggle, onChange }: { sku: Level; write: boolean; open: boolean; onToggle: () => void; onChange: () => Promise<unknown> }) {
  const [delta, setDelta] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const adjust = useMutation({
    mutationFn: () => api('POST', `/api/inventory/${sku.id}/adjust`, { delta: parseInt(delta, 10), reason }),
    onSuccess: async () => {
      setDelta('')
      setReason('')
      setError(null)
      await onChange()
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'failed'),
  })
  return (
    <>
      <tr className={sku.low ? 'low' : ''} data-testid={`level-${sku.code}`}>
        <td>{sku.productName}</td>
        <td>
          <code>{sku.code}</code>
          {sku.name && <span className="muted"> {sku.name}</span>}
        </td>
        <td className="num">{sku.onHand}</td>
        <td className="num">{sku.reserved}</td>
        <td className="num">
          <b>{sku.available}</b>
          {sku.low && <span className="badge warn">low</span>}
        </td>
        {write && (
          <td>
            <form
              className="adjust"
              onSubmit={(e) => {
                e.preventDefault()
                if (delta && parseInt(delta, 10) !== 0) adjust.mutate()
              }}
            >
              <input value={delta} onChange={(e) => setDelta(e.target.value)} placeholder="+5 / -2" aria-label={`Adjust ${sku.code}`} size={6} />
              <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="reason" aria-label={`Reason for ${sku.code}`} />
              <button className="btn small" type="submit" disabled={adjust.isPending}>
                Apply
              </button>
            </form>
            {error && <div className="form-error small">{error}</div>}
          </td>
        )}
        <td>
          <button className="btn small" onClick={onToggle}>
            {open ? 'Hide' : 'History'}
          </button>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={write ? 7 : 6}>
            <History skuId={sku.id} />
          </td>
        </tr>
      )}
    </>
  )
}

function History({ skuId }: { skuId: number }) {
  const q = useQuery({ queryKey: ['movements', skuId], queryFn: () => apiGet<{ movements: Movement[] }>(`/api/inventory/${skuId}/movements`) })
  if (q.isPending) return <p className="muted small">Loading…</p>
  if (q.isError || q.data.movements.length === 0) return <p className="muted small">No movements yet.</p>
  return (
    <table className="table small">
      <thead>
        <tr>
          <th>When</th>
          <th>Kind</th>
          <th className="num">Qty</th>
          <th>Source</th>
          <th>Reason</th>
          <th>By</th>
          <th className="num">On hand</th>
          <th className="num">Reserved</th>
        </tr>
      </thead>
      <tbody>
        {q.data.movements.map((m) => (
          <tr key={m.id}>
            <td>{new Date(m.at).toLocaleString()}</td>
            <td>
              <code>{m.kind}</code>
            </td>
            <td className="num">{m.quantity > 0 && m.kind === 'adjust' ? '+' : ''}{m.quantity}</td>
            <td>{m.sourceType}{m.sourceType !== 'manual' ? ` ${m.sourceId}` : ''}</td>
            <td>{m.reason ?? ''}</td>
            <td>{m.actor}</td>
            <td className="num">{m.onHandAfter}</td>
            <td className="num">{m.reservedAfter}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Import({ onDone }: { onDone: () => Promise<unknown> }) {
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
        sheet's count; new items are created. Nothing changes until you apply.
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
