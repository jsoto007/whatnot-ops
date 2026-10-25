import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../../auth/useAuth'
import { Icon } from '../../components/Icon'
import { ApiError, api, apiGet, money, type Activity, type CountResult, type ReceiveResult, type StockLevel } from '../../lib/api'
import { fmtWhen, plural, requestId } from '../../lib/format'
import { AddItem } from './inventory/AddItem'
import { Import } from './inventory/Import'
import { ItemPanel } from './inventory/ItemPanel'
import { Receive } from './inventory/Receive'
import { describe, stateOf } from './inventory/ledger'

type Filter = 'all' | 'buy' | 'low' | 'out' | 'ok'
const FILTERS: { key: Filter; label: string; match: (s: StockLevel) => boolean }[] = [
  { key: 'all', label: 'All', match: () => true },
  { key: 'buy', label: 'Buy more', match: (s) => s.short > 0 },
  { key: 'low', label: 'Low', match: (s) => s.short === 0 && s.available > 0 && s.low },
  { key: 'out', label: 'Out', match: (s) => s.short === 0 && s.available <= 0 },
  { key: 'ok', label: 'In stock', match: (s) => s.available > 0 && !s.low },
]
type Panel = { kind: 'receive'; preset?: { skuId: number; qty: number }[] } | { kind: 'add' } | { kind: 'import' } | null

/** Order of attention: owed to buyers, then out, then low, then the rest. */
const rank = (s: StockLevel) => (s.short > 0 ? 0 : s.available <= 0 ? 1 : s.low ? 2 : 3)
const byName = (a: StockLevel, b: StockLevel) => a.productName.localeCompare(b.productName) || a.code.localeCompare(b.code)

type SortKey = 'attention' | 'name' | 'onHand' | 'reserved' | 'available' | 'sold'
const SORTS: { key: SortKey; label: string; desc: boolean; value: (s: StockLevel) => number | string }[] = [
  { key: 'attention', label: 'Needs attention', desc: false, value: rank },
  { key: 'name', label: 'Name', desc: false, value: (s) => `${s.productName} ${s.code}`.toLowerCase() },
  { key: 'onHand', label: 'On shelf', desc: true, value: (s) => s.onHand },
  { key: 'reserved', label: 'Held', desc: true, value: (s) => s.reserved },
  { key: 'available', label: 'Available', desc: true, value: (s) => s.available },
  { key: 'sold', label: 'Sold', desc: true, value: (s) => s.sold },
]

export function Inventory() {
  const { can } = useAuth()
  const write = can('inventory.write')
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const filter = (FILTERS.find((f) => f.key === params.get('filter'))?.key ?? 'all') as Filter
  const itemId = Number(params.get('item')) || null
  const [q, setQ] = useState('')
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: 'attention', desc: false })
  const [panel, setPanel] = useState<Panel>(null)
  const [activityOpen, setActivityOpen] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  const levels = useQuery({ queryKey: ['inventory'], queryFn: () => apiGet<{ skus: StockLevel[] }>('/api/inventory') })
  const activity = useQuery({ queryKey: ['inventory-activity'], queryFn: () => apiGet<{ movements: Activity[] }>('/api/inventory/activity?limit=12') })

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(t)
  }, [toast])

  const setParam = useCallback(
    (key: string, value: string | null) =>
      setParams(
        (p) => {
          const n = new URLSearchParams(p)
          if (value === null) n.delete(key)
          else n.set(key, value)
          return n
        },
        { replace: key === 'filter' },
      ),
    [setParams],
  )
  const closeItem = useCallback(() => setParam('item', null), [setParam])
  const closePanel = useCallback(() => setPanel(null), [])

  const refresh = useCallback(
    async (msg?: string) => {
      if (msg) setToast(msg)
      await Promise.all(
        [['inventory'], ['inventory-activity'], ['products'], ['overview'], ['nav-counts']].map((queryKey) => qc.invalidateQueries({ queryKey })),
      )
    },
    [qc],
  )

  const sorted = useMemo(() => {
    const spec = SORTS.find((s) => s.key === sort.key)!
    const dir = sort.desc ? -1 : 1
    return (levels.data?.skus ?? []).slice().sort((a, b) => {
      const x = spec.value(a)
      const y = spec.value(b)
      return (x < y ? -1 : x > y ? 1 : 0) * dir || byName(a, b)
    })
  }, [levels.data, sort])
  if (levels.isPending) return <p className="muted">Loading…</p>
  if (levels.isError) return <p className="form-error">Could not load inventory.</p>

  const skus = sorted
  const active = skus.filter((s) => s.active)
  const counts = Object.fromEntries(FILTERS.map((f) => [f.key, active.filter(f.match).length])) as Record<Filter, number>
  const needle = q.trim().toLowerCase()
  const shown = active.filter(FILTERS.find((f) => f.key === filter)!.match).filter((s) => !needle || `${s.productName} ${s.name} ${s.code} ${s.aliases.map((a) => a.alias).join(' ')}`.toLowerCase().includes(needle))
  const shelfUnits = active.reduce((n, s) => n + Math.max(0, s.onHand), 0)
  const shelfValue = active.reduce((n, s) => n + Math.max(0, s.onHand) * (s.priceCents ?? 0), 0)
  const held = active.reduce((n, s) => n + s.reserved, 0)
  const short = active.filter((s) => s.short > 0)
  const toBuy = short.reduce((n, s) => n + s.short, 0)
  const low = active.filter((s) => s.short === 0 && s.low)
  const open = itemId ? skus.find((s) => s.id === itemId) : undefined

  const received = (r: ReceiveResult) => {
    setPanel(null)
    void refresh(r.alreadyApplied ? 'That delivery was already received; nothing changed.' : `Received ${plural(r.units, 'unit', 'units')}: ${r.lines.map((l) => `${l.quantity} ${l.product}${l.created ? ' (new)' : ''}`).join(', ')}`)
  }
  const sortBy = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, desc: !s.desc } : { key, desc: SORTS.find((x) => x.key === key)!.desc }))
  const ariaSort = (key: SortKey) => (sort.key === key ? (sort.desc ? 'descending' : 'ascending') : undefined)
  const arrow = (key: SortKey) => (sort.key === key ? (sort.desc ? ' ↓' : ' ↑') : '')
  const th = (key: SortKey, label: string, className?: string) => (
    <th className={className} aria-sort={ariaSort(key)}>
      <button type="button" className="th-sort" onClick={() => sortBy(key)}>
        {label}
        {arrow(key)}
      </button>
    </th>
  )

  return (
    <section className="inv">
      <div className="page-head">
        <div>
          <h2>Inventory</h2>
          <div className="sub">
            {plural(active.length, 'item', 'items')} · {plural(shelfUnits, 'unit', 'units')} on the shelf · {held} held for open sales
            {short.length > 0 && (
              <>
                {' · '}
                <b style={{ color: 'var(--tint-red-ink)' }} data-testid="to-buy">
                  {short.length} oversold, buy {toBuy} more
                </b>
              </>
            )}
          </div>
        </div>
        {write && (
          <div className="head-actions">
            <button type="button" className="btn ghost" onClick={() => setPanel(panel?.kind === 'import' ? null : { kind: 'import' })} data-testid="toggle-import">
              <Icon name="upload" size={16} /> {panel?.kind === 'import' ? 'Hide import' : 'Import sheet'}
            </button>
            <button type="button" className="btn primary" onClick={() => setPanel({ kind: 'add' })}>
              <Icon name="plus" size={16} /> Add new item
            </button>
          </div>
        )}
      </div>

      {panel?.kind === 'import' && <Import onDone={() => refresh('Sheet imported')} />}

      {active.length > 0 && (
        <div className="inv-stats">
          <div className="tile">
            <div className="tile-label">On the shelf</div>
            <div className="tile-value">{shelfUnits}</div>
            <div className="muted small">{shelfValue ? `${money(shelfValue)} at list price` : 'units'}</div>
          </div>
          <div className="tile">
            <div className="tile-label">Held for open sales</div>
            <div className="tile-value">{held}</div>
            <div className="muted small">leave the shelf when paid</div>
          </div>
          <button type="button" className={'tile tile-btn' + (toBuy ? ' accent-red' : '')} onClick={() => setParam('filter', 'buy')} disabled={!toBuy}>
            <div className="tile-label">To buy</div>
            <div className="tile-value">{toBuy}</div>
            <div className="muted small">{toBuy ? `owed to buyers across ${plural(short.length, 'item', 'items')}` : 'nothing oversold'}</div>
          </button>
          <button type="button" className={'tile tile-btn' + (low.length ? ' accent-amber' : '')} onClick={() => setParam('filter', 'low')} disabled={!low.length}>
            <div className="tile-label">Running low</div>
            <div className="tile-value">{low.length}</div>
            <div className="muted small">{low.length ? low.slice(0, 2).map((s) => s.productName).join(', ') + (low.length > 2 ? '…' : '') : 'all above their alert'}</div>
          </button>
        </div>
      )}

      {write && toBuy > 0 && (
        <div className="inv-callout" role="note">
          <span>
            <b>
              {short.length === 1 ? `${short[0]!.productName} is oversold by ${short[0]!.short}.` : `${short.length} items are oversold by ${toBuy} units.`}
            </b>{' '}
            Receive them when they arrive and the orders can ship.
          </span>
          <button type="button" className="btn small primary" onClick={() => setPanel({ kind: 'receive', preset: short.map((s) => ({ skuId: s.id, qty: s.short })) })}>
            Receive what’s owed
          </button>
        </div>
      )}

      {skus.length === 0 ? (
        <div className="empty">
          <b>Nothing tracked yet</b>
          {write ? (
            <>
              Add your first item, or import the inventory sheet.
              <div className="empty-actions">
                <button type="button" className="btn primary" onClick={() => setPanel({ kind: 'add' })}>
                  <Icon name="plus" size={16} /> Add new item
                </button>
              </div>
            </>
          ) : (
            'A manager adds items here.'
          )}
        </div>
      ) : (
        <div className="card flush">
          <div className="inv-toolbar">
            <label className="search">
              <Icon name="search" size={16} />
              <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name, SKU or chat name" aria-label="Search inventory" />
              {needle && (
                <>
                  <span className="search-n" aria-live="polite">
                    {shown.length} of {active.length}
                  </span>
                  <button type="button" className="search-clear" onClick={() => setQ('')} aria-label="Clear search">
                    ×
                  </button>
                </>
              )}
            </label>
            <div className="inv-tools">
              <div className="seg" role="tablist" aria-label="Show">
                {FILTERS.map((f) => (
                  <button key={f.key} type="button" role="tab" aria-selected={filter === f.key} className={filter === f.key ? 'on' : ''} onClick={() => setParam('filter', f.key === 'all' ? null : f.key)}>
                    {f.label} <span className="seg-n">{counts[f.key]}</span>
                  </button>
                ))}
              </div>
              <label className="sort">
                Sort by
                <select value={sort.key} onChange={(e) => sortBy(e.target.value as SortKey)} aria-label="Sort by">
                  {SORTS.map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
              <button type="button" className="btn sort-dir" onClick={() => setSort((s) => ({ ...s, desc: !s.desc }))} aria-label={sort.desc ? 'Sorted high to low, switch to low to high' : 'Sorted low to high, switch to high to low'}>
                {sort.desc ? '↓' : '↑'}
              </button>
            </div>
          </div>
          <div className="inv-scroll">
            <table className={'table inv-table' + (write ? ' editable' : '')} data-testid="levels">
              <thead>
                <tr>
                  {th('name', 'Item')}
                  {th('onHand', 'On shelf', 'num col-shelf')}
                  {th('reserved', 'Held', 'num hide-sm')}
                  {th('available', 'Available', 'num')}
                  <th className="hide-sm">Stock</th>
                  {th('sold', 'Sold', 'num hide-sm')}
                  {write && <th className="col-adjust">Adjust shelf</th>}
                </tr>
              </thead>
              <tbody>
                {shown.map((s) => (
                  <Row key={`${s.id}:${s.onHand}`} sku={s} write={write} onOpen={() => setParam('item', String(s.id))} onSaved={(msg) => refresh(msg)} />
                ))}
              </tbody>
            </table>
            {shown.length === 0 && <p className="muted inv-none">{needle ? `Nothing matches “${q.trim()}”.` : 'Nothing in this view.'}</p>}
          </div>
        </div>
      )}

      <section className="card inv-activity" aria-labelledby="inv-activity-title">
        <button type="button" className="collapse-head" aria-expanded={activityOpen} aria-controls="inv-activity-body" onClick={() => setActivityOpen((o) => !o)} data-testid="toggle-activity">
          <span>
            <h3 id="inv-activity-title">Recent activity</h3>
          </span>
          <Icon name="chevron" size={16} className="chev" />
        </button>
        <div id="inv-activity-body" className={'collapse-body' + (activityOpen ? ' open' : '')} inert={!activityOpen}>
          <div>
            {activity.isPending ? (
              <p className="muted small inv-none">Loading…</p>
            ) : !activity.data?.movements.length ? (
              <p className="muted small inv-none">Receipts, counts, sales and payments will show up here.</p>
            ) : (
              <ol className="ledger" data-testid="activity">
                {activity.data.movements.map((m) => {
                  const d = describe(m)
                  return (
                    <li key={m.id} className={`ledger-row tone-${d.tone}`}>
                      <span className="ledger-dot" aria-hidden="true" />
                      <div className="ledger-main">
                        <div>
                          <button type="button" className="linkish" onClick={() => setParam('item', String(m.skuId))}>
                            {m.productName}
                          </button>{' '}
                          · {d.text}
                        </div>
                        <div className="muted small">
                          {fmtWhen(m.at)} · {m.actor}
                        </div>
                      </div>
                      <span className="ledger-effect">{d.effect}</span>
                    </li>
                  )
                })}
              </ol>
            )}
          </div>
        </div>
      </section>

      {open && <ItemPanel key={open.id} sku={open} write={write} onClose={closeItem} onChanged={(msg) => refresh(msg)} onReceive={(qty) => (closeItem(), setPanel({ kind: 'receive', preset: [{ skuId: open.id, qty }] }))} />}
      {panel?.kind === 'receive' && <Receive skus={skus} preset={panel.preset} onClose={closePanel} onDone={received} />}
      {panel?.kind === 'add' && (
        <AddItem
          onClose={closePanel}
          onDone={(msg) => {
            setPanel(null)
            void refresh(msg)
          }}
        />
      )}
      {toast && (
        <div className="toast" role="status">
          {toast}
        </div>
      )}
    </section>
  )
}

function Row({ sku, write, onOpen, onSaved }: { sku: StockLevel; write: boolean; onOpen: () => void; onSaved: (msg: string) => void }) {
  const st = stateOf(sku)
  const pct = sku.onHand > 0 ? Math.round((100 * Math.max(0, sku.available)) / sku.onHand) : 0
  const label = `${sku.productName}${sku.name ? ` ${sku.name}` : ''}`
  return (
    <tr className={sku.short ? 'short' : sku.low ? 'low' : undefined} data-testid={`level-${sku.code}`}>
      <td>
        <button type="button" className="linkish item-name" onClick={onOpen}>
          {label}
        </button>
        <div className="muted small">
          <code>{sku.code}</code>
          {sku.priceCents != null && <> · {money(sku.priceCents)}</>}
        </div>
      </td>
      <td className={'num col-shelf' + (sku.onHand < 0 ? ' neg' : '')} data-testid="on-hand">
        {sku.onHand}
      </td>
      <td className="num hide-sm" data-testid="held">
        {sku.reserved}
      </td>
      <td className="num" data-testid="available">
        <b className={sku.short ? 'neg' : undefined}>{sku.available}</b>
        {st.tone !== 'green' && <span className={`badge ${st.tone === 'red' ? 'danger' : 'warn'}`}>{sku.short ? `buy ${sku.short}` : st.label.toLowerCase()}</span>}
      </td>
      <td className="hide-sm">
        <div className="avail" title={sku.short ? `${sku.short} owed to buyers` : `${sku.available} of ${sku.onHand} on the shelf free to sell`}>
          <div className="avail-track">
            <div className={'avail-fill' + (sku.short || sku.available <= 0 ? ' out' : sku.low ? ' low' : '')} style={{ width: sku.short ? '100%' : `${sku.onHand ? Math.max(2, pct) : 0}%` }} />
          </div>
        </div>
      </td>
      <td className="num hide-sm">{sku.sold}</td>
      {write && (
        <td className="col-adjust">
          <ShelfStepper sku={sku} label={label} onSaved={onSaved} />
        </td>
      )}
    </tr>
  )
}

/**
 * − [n] + ✓ : set what is on the shelf right here. Saving is a one-line stock count,
 * so it is audited, shows in the ledger, and a retried click moves nothing twice.
 */
function ShelfStepper({ sku, label, onSaved }: { sku: StockLevel; label: string; onSaved: (msg: string) => void }) {
  // A shelf below zero (paid oversells) stays in the On shelf column; the stepper rests at 0 until edited.
  const shelf = Math.max(0, sku.onHand)
  const [draft, setDraft] = useState(String(shelf))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const ids = useRef<{ value: string; id: string } | null>(null)
  const n = /^\d+$/.test(draft.trim()) ? parseInt(draft.trim(), 10) : NaN
  const base = Number.isNaN(n) ? shelf : n
  const dirty = draft.trim() !== '' && n !== shelf
  const valid = !Number.isNaN(n)

  const save = async () => {
    if (!dirty || busy) return
    if (!valid) return setError('Whole number, 0 or more')
    if (ids.current?.value !== draft) ids.current = { value: draft, id: requestId() }
    setBusy(true)
    setError(null)
    try {
      const r = await api<CountResult>('POST', '/api/inventory/count', { countId: ids.current.id, lines: [{ skuId: sku.id, counted: n }] })
      const d = r.lines[0]?.delta ?? n - sku.onHand
      onSaved(`${label}: shelf set to ${n} (${d > 0 ? '+' : '−'}${Math.abs(d)})`)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not save')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="qty-step">
      <button type="button" className="step" onClick={() => setDraft(String(Math.max(0, base - 1)))} disabled={base <= 0 || busy} aria-label={`One fewer ${label}`}>
        <Icon name="minus" size={16} />
      </button>
      <input
        type="number"
        min={0}
        inputMode="numeric"
        value={draft}
        onChange={(e) => (setDraft(e.target.value), setError(null))}
        onKeyDown={(e) => {
          if (e.key === 'Enter') void save()
          if (e.key === 'Escape') setDraft(String(shelf))
        }}
        aria-label={`${label} on shelf`}
        aria-invalid={dirty && !valid}
        disabled={busy}
      />
      <button type="button" className="step" onClick={() => setDraft(String(base + 1))} disabled={busy} aria-label={`One more ${label}`}>
        <Icon name="plus" size={16} />
      </button>
      {dirty ? (
        <>
          <button type="button" className="save" onClick={save} disabled={busy || !valid} aria-label={`Save ${label} at ${draft}`} title="Save">
            {busy ? <span className="spinner" aria-hidden /> : <Icon name="check" size={16} strokeWidth={2.6} />}
          </button>
          <button type="button" className="discard" onClick={() => (setDraft(String(shelf)), setError(null))} disabled={busy} aria-label={`Discard change to ${label}`} title="Discard">
            ×
          </button>
        </>
      ) : (
        <span className="spacer" aria-hidden />
      )}
      {error && (
        <span className="qty-error" role="alert">
          {error}
        </span>
      )}
    </div>
  )
}
