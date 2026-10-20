import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../../auth/useAuth'
import { Icon } from '../../components/Icon'
import { apiGet, money, type Activity, type CountResult, type ReceiveResult, type StockLevel } from '../../lib/api'
import { fmtWhen, plural } from '../../lib/format'
import { Count } from './inventory/Count'
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
type Panel = { kind: 'receive'; preset?: { skuId: number; qty: number }[] } | { kind: 'count' } | { kind: 'import' } | null

/** Order of attention: owed to buyers, then out, then low, then the rest by name. */
const rank = (s: StockLevel) => (s.short > 0 ? 0 : s.available <= 0 ? 1 : s.low ? 2 : 3)

export function Inventory() {
  const { can } = useAuth()
  const write = can('inventory.write')
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const filter = (FILTERS.find((f) => f.key === params.get('filter'))?.key ?? 'all') as Filter
  const itemId = Number(params.get('item')) || null
  const [q, setQ] = useState('')
  const [panel, setPanel] = useState<Panel>(null)
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

  const skus = useMemo(() => (levels.data?.skus ?? []).slice().sort((a, b) => rank(a) - rank(b) || a.productName.localeCompare(b.productName) || a.code.localeCompare(b.code)), [levels.data])
  if (levels.isPending) return <p className="muted">Loading…</p>
  if (levels.isError) return <p className="form-error">Could not load inventory.</p>

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
  const countedDone = (r: CountResult) => {
    setPanel(null)
    void refresh(r.changed ? `Count saved: ${plural(r.changed, 'item', 'items')} changed (net ${r.net > 0 ? '+' : r.net < 0 ? '−' : ''}${Math.abs(r.net)})` : 'Count saved: everything matched the shelf.')
  }

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
            <button type="button" className="btn" onClick={() => setPanel({ kind: 'count' })} disabled={!active.length}>
              <Icon name="clipboard" size={16} /> Count stock
            </button>
            <button type="button" className="btn primary" onClick={() => setPanel({ kind: 'receive' })}>
              <Icon name="truck" size={16} /> Receive stock
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
              Receive your first delivery to add items, or import the inventory sheet.
              <div className="empty-actions">
                <button type="button" className="btn primary" onClick={() => setPanel({ kind: 'receive' })}>
                  Receive stock
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
            <div className="seg" role="tablist" aria-label="Show">
              {FILTERS.map((f) => (
                <button key={f.key} type="button" role="tab" aria-selected={filter === f.key} className={filter === f.key ? 'on' : ''} onClick={() => setParam('filter', f.key === 'all' ? null : f.key)}>
                  {f.label} <span className="seg-n">{counts[f.key]}</span>
                </button>
              ))}
            </div>
            <label className="search">
              <Icon name="search" size={16} />
              <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name, SKU or chat name" aria-label="Search inventory" />
            </label>
          </div>
          <div className="table-scroll">
            <table className="table inv-table" data-testid="levels">
              <thead>
                <tr>
                  <th>Item</th>
                  <th className="num">On shelf</th>
                  <th className="num">Held</th>
                  <th className="num">Available</th>
                  <th className="hide-sm">Stock</th>
                  <th className="num hide-sm">Sold</th>
                  <th className="hide-sm">Last received</th>
                  <th aria-label="Open" />
                </tr>
              </thead>
              <tbody>
                {shown.map((s) => (
                  <Row key={s.id} sku={s} onOpen={() => setParam('item', String(s.id))} />
                ))}
              </tbody>
            </table>
            {shown.length === 0 && <p className="muted small inv-none">Nothing matches.</p>}
          </div>
        </div>
      )}

      <section className="card inv-activity" aria-labelledby="inv-activity-title">
        <h3 id="inv-activity-title">Recent activity</h3>
        {activity.isPending ? (
          <p className="muted small">Loading…</p>
        ) : !activity.data?.movements.length ? (
          <p className="muted small">Receipts, counts, sales and payments will show up here.</p>
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
      </section>

      {open && <ItemPanel key={open.id} sku={open} write={write} onClose={closeItem} onChanged={(msg) => refresh(msg)} onReceive={(qty) => (closeItem(), setPanel({ kind: 'receive', preset: [{ skuId: open.id, qty }] }))} />}
      {panel?.kind === 'receive' && <Receive skus={skus} preset={panel.preset} onClose={closePanel} onDone={received} />}
      {panel?.kind === 'count' && <Count skus={skus} onClose={closePanel} onDone={countedDone} />}
      {toast && (
        <div className="toast" role="status">
          {toast}
        </div>
      )}
    </section>
  )
}

function Row({ sku, onOpen }: { sku: StockLevel; onOpen: () => void }) {
  const st = stateOf(sku)
  const pct = sku.onHand > 0 ? Math.round((100 * Math.max(0, sku.available)) / sku.onHand) : 0
  return (
    <tr className={'inv-row' + (sku.short ? ' short' : sku.low ? ' low' : '')} data-testid={`level-${sku.code}`} onClick={onOpen}>
      <td>
        <button type="button" className="linkish item-name" onClick={(e) => (e.stopPropagation(), onOpen())}>
          {sku.productName}
          {sku.name ? ` ${sku.name}` : ''}
        </button>
        <div className="muted small">
          <code>{sku.code}</code>
          {sku.priceCents != null && <> · {money(sku.priceCents)}</>}
        </div>
      </td>
      <td className="num" data-testid="on-hand">
        {sku.onHand}
      </td>
      <td className="num" data-testid="held">
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
      <td className="hide-sm muted small">{sku.lastReceivedAt ? fmtWhen(sku.lastReceivedAt) : sku.lastCountedAt ? `counted ${fmtWhen(sku.lastCountedAt)}` : '—'}</td>
      <td className="chev">
        <Icon name="chevron" size={16} />
      </td>
    </tr>
  )
}
