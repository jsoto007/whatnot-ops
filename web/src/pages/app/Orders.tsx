import { Fragment, useDeferredValue, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams, useSearchParams } from 'react-router'
import { useAuth } from '../../auth/useAuth'
import { BuyerFlagChip } from '../../components/BuyerFlag'
import { ChangeProduct } from '../../components/ChangeProduct'
import { DeclineDialog } from '../../components/DeclineDialog'
import { Icon } from '../../components/Icon'
import { ProductPicker, type PickerTarget } from '../../components/ProductPicker'
import { ApiError, api, apiGet, money, type BoardBuyer, type BoardShow, type BoardTab, type BuyerTier, type Giveaway, type Order, type OrderStatus, type PickItem, type Sale, type SaleProduct, type SaleStatus, type ShowBoard, type ToPick } from '../../lib/api'
import { SHOP_TZ, fmtLongDate, fmtWhen, plural } from '../../lib/format'
import { BOARD_TAB_LABEL, LEFT_UNNAMED_LABEL, NOT_OURS_LABEL, ORDER_NEXT_STEP, ORDER_STEP_BACK, TIER_LABEL, lotLabel, lotText, orderStatusLabel, packageStatusLabel, parcelStateLabel, pickStateWords, saleLabel, saleStatusLabel, unknownWords } from '../../lib/labels'
import { invalidateSales } from '../../lib/invalidate'
import { NameGiveaway } from './Giveaways'

const NEXT = ORDER_NEXT_STEP

/** Whatnot charges the winner at the win, so nothing waits for a payment: an order is ready to
 * pick at once. `open` only means an item still needs a person (not recognised); a declined
 * card shows on the order. */
const declined = (o: Order) => (o.sales ?? []).filter((s) => s.status === 'payment_failed')
/** Lots with no product yet (the order stays open until each one is named). */
const unnamed = (o: Order) => (o.sales ?? []).filter((s) => s.status === 'detected' && !s.sku)
/** The ones a person can name from here: a decision is still open on them. */
const unknown = (o: Order) => unnamed(o).filter((s) => s.review?.reason === 'unmatched_item')
const TABS: BoardTab[] = ['review', 'pack', 'shipped']
/** Links made before the three tabs (2026-11-24) land on the tab that now holds those orders. */
const VIEW_OF: Record<string, BoardTab> = { review: 'review', declined: 'review', all: 'review', pack: 'pack', queue: 'pack', progress: 'pack', shipped: 'shipped' }
/** Narrow the Shipped tab to where the parcels are, as Whatnot last said. */
const PARCEL_FILTERS: { key: string; label: string; match: (state: string | null | undefined) => boolean }[] = [
  { key: '', label: 'All shipped', match: () => true },
  { key: 'carrier', label: 'Not with the carrier yet', match: (s) => s === 'waiting' || s === 'late' || s === 'nolabel' },
  { key: 'moving', label: 'On its way', match: (s) => s === 'moving' },
  { key: 'failed', label: 'Delivery failed', match: (s) => s === 'failed' },
  { key: 'delivered', label: 'Delivered', match: (s) => s === 'delivered' },
]
const STANDING: SaleStatus[] = ['reserved', 'paid', 'fulfilled', 'detected']
/** Whether the Need review tab leaves out orders Whatnot cancelled: kept on this laptop. */
const HIDE_CANCELLED_KEY = 'wn.ordersHideCancelled'
const readHide = (): boolean => {
  try {
    return localStorage.getItem(HIDE_CANCELLED_KEY) === 'yes'
  } catch {
    return false
  }
}
const saveHide = (on: boolean) => {
  try {
    localStorage.setItem(HIDE_CANCELLED_KEY, on ? 'yes' : 'no')
  } catch {
    /* private window or storage off: the choice lasts for this visit */
  }
}

const titleOf = (s: Sale) => (s.sku ? `${s.sku.productName}${s.sku.name ? ' ' + s.sku.name : ''}` : s.itemTitle || s.item || '?')
const nameOf = (s: { productName: string; name: string }) => `${s.productName} ${s.name}`.trim()
/** A change to one sale's products: change one (which, when it holds several), or add one. */
interface ProductChange {
  sale: Sale
  product?: SaleProduct
  add?: boolean
}
/** Lots that are "the same item": one show, one title without the lot number. A title that is
 * only a lot number ("#520") names nothing, so such lots are never grouped. */
const groupKey = (s: Sale): string | null => {
  const title = (s.itemTitle || '').toLowerCase()
  return title ? `${s.showId}|${title}` : null
}
const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZone: SHOP_TZ })

function wonAt(o: Order): number {
  return Math.max(0, ...(o.sales ?? []).map((s) => (s.wonAt ? Date.parse(s.wonAt) : s.paidAt ? Date.parse(s.paidAt) : 0)))
}

/** What the page's dialogs act on: the row that opened them passes what it knows. */
interface Acts {
  onPick: (sale: Sale, similar: Sale[], applyToSimilar?: boolean) => void
  onDecline: (sale: Sale) => void
  onChange: (c: ProductChange) => void
  onGiveaway: (g: Giveaway) => void
  onDone: (msg: string) => void | Promise<void>
}

/**
 * Orders, one show at a time, in three tabs: Need review (something waits on a person), Ready
 * to pack (print the pick list and the labels) and Shipped (where each parcel is). A buyer's
 * wins and Whatnot giveaways of one show are one row: one box, one label. The server places
 * each buyer (`services/order_board.py`); new and returning buyers share the list, each with
 * a small badge for how much and how often they buy.
 */
export function Orders() {
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const showId = params.get('show') ?? ''
  const [find, setFind] = useState('')
  const [hideCancelled, setHideCancelled] = useState(readHide)
  const [picking, setPicking] = useState<PickerTarget | null>(null)
  const [decline, setDecline] = useState<Sale | null>(null)
  const [changing, setChanging] = useState<ProductChange | null>(null)
  const [naming, setNaming] = useState<Giveaway | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  // Shows opened or closed by hand; the rest follow the default (the newest one open).
  const [opened, setOpened] = useState<Record<number, boolean>>({})
  const board = useQuery({ queryKey: ['orders', 'board'], queryFn: () => apiGet<{ shows: BoardShow[] }>('/api/orders/board') })
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(t)
  }, [toast])
  const set = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v)
      else next.delete(k)
    }
    setParams(next)
  }
  const shows = board.data?.shows ?? []
  const count = (s: BoardShow, t: BoardTab) => (t === 'review' && hideCancelled ? s.review - s.cancelled : s[t])
  const inShow = shows.filter((s) => !showId || String(s.showId) === showId)
  const total = (t: BoardTab) => inShow.reduce((a, s) => a + count(s, t), 0)
  // With no tab asked for, the first one with someone in it (work first: Need review).
  const tab: BoardTab = VIEW_OF[params.get('view') ?? ''] ?? TABS.find((t) => total(t) > 0) ?? 'review'
  const listed = shows.filter((s) => (!showId || String(s.showId) === showId) && count(s, tab) > 0)
  const cancelled = inShow.reduce((n, s) => n + s.cancelled, 0)
  const deferredFind = useDeferredValue(find)
  const needle = deferredFind.trim().toLowerCase()
  const parcel = PARCEL_FILTERS.find((f) => f.key === (params.get('parcel') ?? '')) ?? PARCEL_FILTERS[0]!
  const isOpen = (s: BoardShow, i: number) => opened[s.showId] ?? (i === 0 || !!showId || !!needle)

  /** The dialog stays up until the list has refetched, so no button can act on a lot just settled. */
  const done = async (msg: string) => {
    await invalidateSales(qc)
    setPicking(null)
    setDecline(null)
    setChanging(null)
    setNaming(null)
    setToast(msg)
  }
  const acts: Acts = {
    onPick: (sale, similar, applyToSimilar = false) => {
      if (!sale.review) return
      setPicking({ sale, buyer: sale.buyer, showDate: sale.showDate, reviewId: sale.review.id, similar, applyToSimilar })
    },
    onDecline: setDecline,
    onChange: setChanging,
    onGiveaway: setNaming,
    onDone: done,
  }

  return (
    <section className="orders">
      <div className="page-head">
        <div>
          <h2>Orders</h2>
          <div className="sub">One box per buyer per show: every win and giveaway of the show together, one label. Whatnot decides what was sold, paid and cancelled.</div>
        </div>
        <div className="head-actions">
          <select className="show-select" value={showId} onChange={(e) => set({ show: e.target.value })} aria-label="Show">
            <option value="">All shows</option>
            {shows.map((s) => (
              <option key={s.showId} value={s.showId}>
                {fmtLongDate(s.showDate)}
                {s.title ? ` · ${s.title}` : ''}
              </option>
            ))}
          </select>
          <GiveawaysLink />
        </div>
      </div>
      <div className="toolbar">
        <div className="tabs" role="tablist" style={{ marginBottom: 0, borderBottom: 0 }}>
          {TABS.map((t) => {
            const n = total(t)
            return (
              <button key={t} role="tab" aria-selected={tab === t} className={(tab === t ? 'active' : '') + (n === 0 ? ' zero' : '')} onClick={() => set({ view: t, parcel: '' })} data-testid={`tab-${t}`}>
                {BOARD_TAB_LABEL[t]}
                <span className="tab-count">{n}</span>
              </button>
            )
          })}
        </div>
        <label className="search">
          <Icon name="search" size={16} />
          <input type="search" value={find} onChange={(e) => setFind(e.target.value)} placeholder="Find a buyer" aria-label="Find a buyer" />
        </label>
      </div>
      {tab === 'review' && cancelled > 0 && (
        <div className="parcel-filters" role="group" aria-label="Cancelled orders">
          <button
            type="button"
            className={'chip-btn' + (hideCancelled ? ' on' : '')}
            aria-pressed={hideCancelled}
            onClick={() => {
              saveHide(!hideCancelled)
              setHideCancelled(!hideCancelled)
            }}
            data-testid="hide-cancelled"
          >
            Hide cancelled orders <span className="tab-count">{cancelled}</span>
          </button>
        </div>
      )}
      {tab === 'shipped' && (
        <div className="parcel-filters" role="group" aria-label="Where the parcels are" data-testid="parcel-filters">
          {PARCEL_FILTERS.map((f) => (
            <button key={f.key} type="button" className={'chip-btn' + (parcel.key === f.key ? ' on' : '')} aria-pressed={parcel.key === f.key} onClick={() => set({ parcel: f.key })}>
              {f.label}
            </button>
          ))}
        </div>
      )}
      {board.isPending ? (
        <p className="muted">Loading…</p>
      ) : board.isError ? (
        <p className="form-error">Could not load orders.</p>
      ) : listed.length === 0 ? (
        <div className="empty">
          <b>{tab === 'review' ? 'Nothing needs review' : tab === 'pack' ? 'Nothing to pack' : 'Nothing shipped yet'}</b>
          {tab === 'review' ? 'A win with no product, a declined card, a giveaway with no product or a cancelled order shows here.' : tab === 'pack' ? 'A buyer is ready to pack once nothing of theirs waits for a person.' : ''}
        </div>
      ) : (
        listed.map((s, i) => (
          <ShowBlock
            key={s.showId}
            show={s}
            tab={tab}
            open={isOpen(s, i)}
            onToggle={() => setOpened((was) => ({ ...was, [s.showId]: !isOpen(s, i) }))}
            count={count(s, tab)}
            needle={needle}
            hideCancelled={hideCancelled}
            parcel={parcel.match}
            acts={acts}
          />
        ))
      )}
      {picking && <ProductPicker target={picking} onClose={() => setPicking(null)} onDone={done} />}
      {changing && <ChangeProduct sale={changing.sale} buyer={changing.sale.buyer} showDate={changing.sale.showDate} product={changing.product} add={changing.add} onClose={() => setChanging(null)} onDone={done} />}
      {decline && decline.review && <DeclineDialog sale={decline} buyer={decline.buyer} showDate={decline.showDate} reviewId={decline.review.id} onClose={() => setDecline(null)} onDone={done} />}
      {naming && <NameGiveaway giveaway={naming} onClose={() => setNaming(null)} onDone={done} />}
      {toast && (
        <div className="toast" role="status">
          {toast}
        </div>
      )}
    </section>
  )
}

/** One show in the current tab: its heading (open or closed), and once open its buyers. The
 *  buyers are read only while the show is open: each show is one costly read. */
function ShowBlock({ show, tab, open, onToggle, count, needle, hideCancelled, parcel, acts }: { show: BoardShow; tab: BoardTab; open: boolean; onToggle: () => void; count: number; needle: string; hideCancelled: boolean; parcel: (state: string | null | undefined) => boolean; acts: Acts }) {
  const { can } = useAuth()
  const q = useQuery({ queryKey: ['orders', 'board', show.showId], queryFn: () => apiGet<ShowBoard>(`/api/orders/shows/${show.showId}/board`), enabled: open })
  const id = `show-${show.showId}-${tab}`
  const everyone = q.data?.buyers ?? []
  const rows = everyone
    .filter((b) => b.tab === tab)
    .filter((b) => !(tab === 'review' && hideCancelled && b.hideable))
    .filter((b) => tab !== 'shipped' || parcel(b.order?.parcel?.state ?? b.parcel))
    .filter((b) => !needle || b.username.toLowerCase().includes(needle))
    // Buyers who bought come first; those who won only a giveaway after them, marked as such.
    .sort((a, b) => Number(a.giveawayOnly) - Number(b.giveawayOnly) || (a.order ? wonAt(a.order) : 0) - (b.order ? wonAt(b.order) : 0) || a.username.localeCompare(b.username))
  // Lots nobody has named yet, grouped by title: "$1 BEAUTY PULLS #520" and "#521" are one group.
  // Never a lot whose title says the winner chooses ("CHOOSE ANY…"): every one is another product.
  const groups = useMemo(() => {
    const g = new Map<string, Sale[]>()
    for (const b of everyone)
      for (const s of b.order ? unknown(b.order) : []) {
        if (s.choiceTitle) continue
        const key = groupKey(s)
        if (!key) continue
        const list = g.get(key)
        if (list) list.push(s)
        else g.set(key, [s])
      }
    // Oldest win first: "Name it once" names that lot and the rest follow.
    const when = (s: Sale) => (s.wonAt ? Date.parse(s.wonAt) : 0)
    for (const list of g.values()) list.sort((a, b) => when(a) - when(b) || a.id - b.id)
    return g
  }, [everyone])
  const similar = (sale: Sale) => {
    const key = groupKey(sale)
    return (key ? (groups.get(key) ?? []) : []).filter((s) => s.id !== sale.id)
  }
  const biggest = [...groups.values()].sort((a, b) => b.length - a.length)[0]
  const stuck = tab === 'review' && biggest && biggest.length > 1 && can('review.act') ? biggest : null
  const firstGiftOnly = rows.findIndex((b) => b.giveawayOnly)
  return (
    <section className="show-block" aria-label={`Show of ${fmtLongDate(show.showDate)}`} data-testid={`show-${show.showId}`}>
      <div className="show-block-head">
        <button type="button" className="show-block-toggle" aria-expanded={open} aria-controls={id} onClick={onToggle}>
          <Icon name="chevron" size={16} />
          <b>{fmtLongDate(show.showDate)}</b>
          {show.title && <span className="muted show-title">{show.title}</span>}
          <span className="tab-count">{plural(count, 'buyer', 'buyers')}</span>
        </button>
        {tab === 'pack' && (
          <div className="btns">
            <Link className="btn primary small" to={`/app/orders/slips/${show.showId}?only=pack`} data-testid={`print-pick-list-${show.showId}`}>
              <Icon name="print" size={16} />
              Print pick list
            </Link>
            <Link className="btn small" to={`/app/orders/shipping/${show.showId}`} data-testid={`labels-${show.showId}`}>
              <Icon name="truck" size={16} />
              Shipping labels
            </Link>
            <Link className="btn small ghost" to={`/app/orders/pick-list/${show.showId}`} title="How many of each product to pull for this show">
              <Icon name="clipboard" size={16} />
              Products to pull
            </Link>
          </div>
        )}
      </div>
      {open && (
        <div id={id}>
          {stuck && <StuckBanner stuck={stuck} onName={() => acts.onPick(stuck[0]!, similar(stuck[0]!), true)} />}
          {q.isPending ? (
            <p className="muted small">Loading…</p>
          ) : q.isError ? (
            <p className="form-error">Could not load this show’s orders.</p>
          ) : rows.length === 0 ? (
            <p className="muted small">{needle ? 'No buyer by that name in this show.' : 'Nobody left here.'}</p>
          ) : (
            <div className="card flush">
              <table className="table" data-testid="orders">
                <thead>
                  <tr>
                    <th>Buyer</th>
                    <th>Items</th>
                    <th className="num">Total</th>
                    <th>Status</th>
                    <th>
                      <span className="sr-only">Next step</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((b, i) => (
                    <Fragment key={b.username}>
                      {i === firstGiftOnly && (
                        <tr className="group-row">
                          <td colSpan={5}>Won a giveaway, bought nothing</td>
                        </tr>
                      )}
                      <BuyerRow b={b} showId={show.showId} tab={tab} similar={similar} acts={acts} />
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </section>
  )
}

/** Several orders wait on one item the shop does not know yet: name it once for every lot. */
function StuckBanner({ stuck, onName }: { stuck: Sale[]; onName: () => void }) {
  const n = new Set(stuck.map((s) => s.orderId)).size
  return (
    <section className="banner" aria-label="Orders waiting on one unknown item" data-testid="stuck-banner">
      <div className="banner-main">
        <span className="banner-icon" aria-hidden>
          <Icon name="help" size={20} />
        </span>
        <div>
          <b>{plural(n, 'order is', 'orders are')} waiting on one item the shop does not know yet.</b>
          <div className="small">The chat called it “{stuck[0]!.itemTitle} #…”. Name the product once and every one of these lots is settled.</div>
        </div>
      </div>
      <div className="btns">
        <button type="button" className="btn primary" onClick={onName} data-testid="name-it-once">
          Name it once
        </button>
      </div>
    </section>
  )
}

/** The small badge beside a buyer's name: copper, silver or gold for how much and how often they
 *  buy (both), or "new" on their first show. Never printed. */
function TierBadge({ tier, shows, bought }: { tier: BuyerTier | null; shows: number; bought: boolean }) {
  if (tier)
    return (
      <span className={`tier ${tier}`} title={`${TIER_LABEL[tier]} · ${plural(shows, 'show', 'shows')}`} data-testid={`tier-${tier}`}>
        <span className="sr-only">{TIER_LABEL[tier]}</span>
      </span>
    )
  if (bought && shows <= 1)
    return (
      <span className="tier-new" title="Their first show with the shop">
        new
      </span>
    )
  return null
}

/** Whatnot's giveaways for the buyer in this show, beside their wins: marked "Giveaway" in its own
 *  colour, with the product a person named, or that it still needs one. */
function GiveawayLines({ list }: { list: Giveaway[] }) {
  if (!list.length) return null
  return (
    <div className="item-lines">
      {list.map((g) => (
        <div key={g.id} className="item-line giveaway-line" data-testid={`giveaway-line-${g.id}`}>
          <div className="item-title">
            <span className="pill giveaway">
              <Icon name="gift" size={12} /> Giveaway
            </span>{' '}
            <b>{g.quantity} ×</b> {g.product ? nameOf(g.product) : <span className="unknown">needs a product</span>}
          </div>
          {g.cancelled && <div className="small muted">Whatnot cancelled it: take the product off to put it back in stock.</div>}
        </div>
      ))}
    </div>
  )
}

/** One buyer of one show: every win (their order) and every giveaway, one box. */
function BuyerRow({ b, showId, tab, similar, acts }: { b: BoardBuyer; showId: number; tab: BoardTab; similar: (s: Sale) => Sale[]; acts: Acts }) {
  const { can } = useAuth()
  const [open, setOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const o = b.order
  const canReview = can('review.act')
  const waiting = b.giveaways.find((g) => !g.product && !g.cancelled)
  const slip = `/app/orders/slips/${showId}?buyer=${encodeURIComponent(b.username)}`
  const mark = async (ready: boolean) => {
    setError(null)
    try {
      await api('POST', `/api/orders/shows/${showId}/ready`, { buyer: b.username, ready })
      await acts.onDone(ready ? `${b.username}'s box is marked ready to pack.` : `${b.username} is back in Need review.`)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not save that. Try again.')
    }
  }
  const takeOff = async (g: Giveaway) => {
    setError(null)
    try {
      await api('DELETE', `/api/giveaways/${g.id}/product`)
      await acts.onDone(`${g.product ? nameOf(g.product) : 'The product'} is off ${b.username}'s giveaway and back in stock.`)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not save that. Try again.')
    }
  }
  // What this buyer's row adds to the order's own menu: giveaways, the manager's mark, the slip.
  const extra: ReactNode[] = []
  if (canReview)
    for (const g of b.giveaways) {
      if (!g.cancelled)
        extra.push(
          <button key={`gv-${g.id}`} type="button" role="menuitem" onClick={() => acts.onGiveaway(g)} data-testid={`name-giveaway-${g.id}`}>
            <Icon name="gift" size={16} />
            {g.product ? 'Change the giveaway’s product' : 'Name the giveaway’s product'}
          </button>,
        )
      if (g.product)
        extra.push(
          <button key={`gv-off-${g.id}`} type="button" role="menuitem" onClick={() => void takeOff(g)}>
            <Icon name="minus" size={16} />
            Take the product off the giveaway
          </button>,
        )
    }
  if (canReview && b.mayMarkReady)
    extra.push(
      <button key="ready" type="button" role="menuitem" onClick={() => void mark(true)} data-testid={`mark-ready-${b.username}`}>
        <Icon name="check" size={16} />
        Mark ready to pack
      </button>,
    )
  if (canReview && b.readyMarked && b.tab === 'pack')
    extra.push(
      <button key="unready" type="button" role="menuitem" onClick={() => void mark(false)} data-testid={`unmark-ready-${b.username}`}>
        <Icon name="undo" size={16} />
        Back to Need review
      </button>,
    )
  extra.push(
    <Link key="slip" role="menuitem" to={slip}>
      <Icon name="print" size={16} />
      Print this buyer’s slip
    </Link>,
  )
  // A giveaway with no product is the row's one verb when the order itself has none.
  const lead =
    tab === 'review' && waiting && canReview ? (
      <button type="button" className="btn small tinted" onClick={() => acts.onGiveaway(waiting)} data-testid="name-giveaway">
        Name the giveaway
      </button>
    ) : tab === 'pack' ? (
      <Link className="btn small ghost" to={slip} data-testid={`print-slip-${b.username}`} aria-label={`Print ${b.username}'s slip`}>
        <Icon name="print" size={16} />
        Slip
      </Link>
    ) : null
  return (
    <>
      <tr data-testid={`order-${b.username}`} className={(open ? 'is-open' : '') + (b.giveawayOnly ? ' gift-only' : '') || undefined}>
        <td className="c-buyer">
          {o && (
            <button type="button" className="disclose" aria-expanded={open} aria-controls={`details-${o.id}`} aria-label={`Details for ${b.username}`} onClick={() => setOpen((v) => !v)} data-testid={`details-toggle-${b.username}`}>
              <Icon name="chevron" size={16} />
            </button>
          )}
          {o ? (
            <Link to={`/app/orders/${o.id}`} className="buyer" title={b.username}>
              {b.username}
            </Link>
          ) : (
            <span className="buyer" title={b.username}>
              {b.username}
            </span>
          )}
          <TierBadge tier={b.tier} shows={b.shows} bought={!!o} />
          <BuyerFlagChip name={b.username} />
        </td>
        <td className="c-items">
          {o && <Items o={o} compact />}
          <GiveawayLines list={b.giveaways} />
        </td>
        <td className="num c-total">{o ? <b>{money(o.totalCents)}</b> : <span className="muted small">no charge</span>}</td>
        <td className="c-status">
          {o ? (
            <Status o={o} />
          ) : (
            <>
              <div className="status-line">
                <span className="pill giveaway">Giveaway only</span>
              </div>
              <div className="signals">
                {waiting ? <span className="small unknown">needs a product</span> : null}
                {b.parcel && <ParcelPill state={b.parcel} />}
              </div>
            </>
          )}
          {b.readyMarked && (
            <div className="small sub muted" data-testid={`marked-ready-${b.username}`}>
              marked ready to pack by a manager
            </div>
          )}
          {b.cancelled && (
            <div className="small sub muted" data-testid={`cancelled-${b.username}`}>
              cancelled on Whatnot
            </div>
          )}
        </td>
        <td className="c-action">
          {o ? (
            <Actions o={o} onPick={(s) => acts.onPick(s, similar(s))} onDecline={acts.onDecline} onChange={acts.onChange} onRemoved={acts.onDone} lead={lead} extra={extra} held={tab === 'review'} />
          ) : (
            <div className="acts">
              {lead}
              <Menu label={`More for ${b.username}`}>{extra}</Menu>
            </div>
          )}
          {error && <div className="form-error small">{error}</div>}
        </td>
      </tr>
      {o && open && (
        <tr className="order-details" id={`details-${o.id}`} data-testid={`details-${b.username}`}>
          <td colSpan={5}>
            <Details o={o} />
          </td>
        </tr>
      )}
    </>
  )
}

/** How many products the row shows before "+N more"; the details show them all. */
const ROW_PRODUCTS = 2
const ROW_LOTS = 3

/** "10 × $1 Beauty Pulls" with the lot numbers under it, one line per product. On the row
 *  (`compact`) a long title wraps to two lines at most and only the first products show. */
function Items({ o, compact = false }: { o: Order; compact?: boolean }) {
  const groups = new Map<string, { title: string; qty: number; lots: number[]; unknown: boolean; notOurs: boolean; left: boolean }>()
  for (const s of (o.sales ?? []).filter((s) => STANDING.includes(s.status))) {
    // One line per product: a win with two products ("two for one") counts under both.
    const parts = (s.products ?? []).length > 0 ? s.products!.map((p) => ({ title: `${p.sku.productName}${p.sku.name ? ' ' + p.sku.name : ''}`, qty: p.quantity })) : [{ title: titleOf(s), qty: s.quantity }]
    for (const part of parts) {
      const g = groups.get(part.title) ?? { title: part.title, qty: 0, lots: [], unknown: !s.sku && !s.notOurs, notOurs: s.notOurs === true, left: false }
      // Settled with no product only when every win on the line is.
      g.notOurs = g.notOurs && s.notOurs === true
      g.left = g.left || s.leftUnnamed === true
      g.qty += part.qty
      if (s.lot != null) g.lots.push(s.lot)
      groups.set(part.title, g)
    }
  }
  const all = [...groups.values()]
  const shown = compact ? all.slice(0, ROW_PRODUCTS) : all
  return (
    <div className="item-lines">
      {shown.map((g) => (
        <div key={g.title} className="item-line">
          <div className={compact ? 'item-title' : undefined}>
            <b>{g.qty} ×</b> {g.unknown ? <span className="unknown">“{g.title}”</span> : g.title}
          </div>
          {g.notOurs && (
            <div className="small sub muted" data-testid="not-ours">
              {g.left ? LEFT_UNNAMED_LABEL : NOT_OURS_LABEL}
            </div>
          )}
          {g.lots.length > 0 && <Lots lots={g.lots} max={compact ? ROW_LOTS : undefined} />}
        </div>
      ))}
      {all.length > shown.length && <div className="small faint">+{plural(all.length - shown.length, 'more product', 'more products')}</div>}
    </div>
  )
}

/** The lot numbers of one product. The row shows the first `max` and "+N more"; the details under
 *  it pass no `max`, so a packer can read every lot there. */
function Lots({ lots, max }: { lots: number[]; max?: number }) {
  const shown = max === undefined ? lots : lots.slice(0, max)
  return (
    <span className="lots">
      {shown.map((n, i) => (
        <span className="lot" key={i}>
          #{n}
        </span>
      ))}
      {lots.length > shown.length && <span className="lot more">+{lots.length - shown.length} more</span>}
    </span>
  )
}

/** When the order's state began, said beside it: "won 9:05 PM", "since 5:38 PM", "by Ana · 10:01 AM". */
function stateTime(o: Order): string | null {
  const won = wonAt(o)
  if (o.status === 'paid' && won > 0) return `won ${time(new Date(won).toISOString())}`
  if (o.status === 'picking' && o.pickedAt) return `since ${time(o.pickedAt)}`
  if (o.status === 'packed') return (o.packedBy ? `by ${o.packedBy}` : 'packed') + (o.packedAt ? ` · ${time(o.packedAt)}` : '')
  return null
}

/** The order at a glance: its state on one line, then one short mark each for what needs an eye
 *  (paid but waiting for its product, a declined card, where the parcel is). The sentences, item
 *  codes and tracking numbers are in the details under the row. */
function Status({ o }: { o: Order }) {
  const unk = unnamed(o)
  const dec = declined(o)
  const when = stateTime(o)
  return (
    <>
      <div className="status-line">
        <span className={`status ${o.status}`}>{orderStatusLabel(o.status)}</span>
        {when && <span className="small faint">{when}</span>}
      </div>
      <div className="signals">
        {o.status === 'open' && (o.toPick ?? 0) > 0 && (
          <span className="status paid" data-testid={`to-pick-${o.buyer}`}>
            paid on Whatnot
          </span>
        )}
        {o.status === 'open' && <span className="small unknown">{unk.length ? unknownWords(unk) : 'older sale, waiting for the payment file'}</span>}
        {dec.length > 0 && (
          <span className="status payment_failed" data-testid={`declined-${o.buyer}`}>
            {saleStatusLabel('payment_failed')}
            {dec.length > 1 ? ` · ${dec.length}` : ''}
          </span>
        )}
        {o.parcel ? (
          <span data-testid={`parcel-${o.buyer}`}>
            <ParcelPill state={o.parcel.state} />
          </span>
        ) : (
          o.tracking && <span className="muted small">{o.tracking}</span>
        )}
      </div>
      {(o.sales ?? []).some((s) => s.review?.reason === 'lot_won_twice') && (
        <div className="small sub unknown" data-testid={`lot-won-twice-${o.buyer}`}>
          a lot here has two winners, see Review
        </div>
      )}
      {(o.sales ?? []).some((s) => s.review?.reason === 'decline_seen') && (
        <div className="small sub unknown" data-testid={`decline-seen-${o.buyer}`}>
          card declined on the stream, waiting for Whatnot
        </div>
      )}
    </>
  )
}

/** Where the buyer's parcel is, as Whatnot last said: one coloured word. */
function ParcelPill({ state }: { state: NonNullable<Order['parcel']>['state'] }) {
  return (
    <span className={`pill parcel ${state}`}>
      <Icon name="truck" size={13} /> {parcelStateLabel(state)}
    </span>
  )
}

/** Each parcel's carrier and tracking number, and when Whatnot last said where it is. */
function Parcel({ o }: { o: Order }) {
  const p = o.parcel!
  const many = p.parcels.length > 1
  return (
    <div className="small" data-testid={`tracking-${o.buyer}`}>
      <ParcelPill state={p.state} />
      <span className="faint"> {fmtWhen(p.changedAt)}</span>
      {p.parcels.map((x, i) => (
        <div key={i} className="muted">
          {many ? `${packageStatusLabel(x.status)} · ` : ''}
          {[x.carrier, x.tracking].filter(Boolean).join(' ') || (many ? 'no tracking number yet' : '')}
        </div>
      ))}
    </div>
  )
}

/** Under the row when it is opened: every product and lot, the payment, each parcel, and the way
 *  to the order's own page. Built from the list's own data: opening it asks the server nothing. */
function Details({ o }: { o: Order }) {
  const unk = unnamed(o)
  const dec = declined(o)
  const won = wonAt(o)
  return (
    <div className="order-details-grid">
      <div className="item-section">
        <h4>Items</h4>
        <Items o={o} />
      </div>
      <div className="item-section">
        <h4>Payment</h4>
        <div className="small">
          {(o.toPick ?? 0) > 0 ? `paid on Whatnot · ${plural(o.toPick ?? 0, 'product', 'products')} to pick` : orderStatusLabel(o.status)}
        </div>
        {o.status === 'open' && unk.length > 0 && <div className="small unknown">{unknownWords(unk)}</div>}
        {won > 0 && <div className="small faint">won {time(new Date(won).toISOString())}</div>}
        {dec.length > 0 && (
          <div className="small">
            <span className="status payment_failed">{saleStatusLabel('payment_failed')}</span> {dec.map((s) => `${s.quantity}× ${s.sku ? s.sku.code : s.item || '?'}`).join(', ')}
          </div>
        )}
      </div>
      <div className="item-section">
        <h4>Shipping</h4>
        {o.parcel ? <Parcel o={o} /> : <div className="small muted">{o.tracking || 'Nothing from Whatnot about a parcel yet.'}</div>}
      </div>
      <Link to={`/app/orders/${o.id}`} className="btn ghost small details-open">
        Open the order
      </Link>
    </div>
  )
}

/** One verb for the order's next step, and a menu for the rest. `lead`: the row's own verb, shown
 *  when the order has none (a giveaway to name, the slip to print); `extra`: the row's own menu
 *  entries (giveaways, the manager's mark, the slip). `held`: the box waits in Need review, so
 *  its fulfilment step is not offered (the menu keeps the step back). */
function Actions({ o, onPick, onDecline, onChange, onRemoved, lead = null, extra = [], held = false }: { o: Order; onPick: (sale: Sale) => void; onDecline: (sale: Sale) => void; onChange: (c: ProductChange) => void; onRemoved: (msg: string) => void | Promise<void>; lead?: ReactNode; extra?: ReactNode[]; held?: boolean }) {
  const { can } = useAuth()
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const move = useMutation({
    mutationFn: (to: OrderStatus) => api('POST', `/api/orders/${o.id}/status`, { status: to }),
    // The order's status shows on the detail, the pick list, shipping and the nav badge too.
    onSuccess: () => invalidateSales(qc),
    onError: (e) => setError(e instanceof ApiError ? e.message : 'failed'),
  })
  const canReview = can('review.act')
  const canFulfil = can('orders.fulfill')
  // Picking, packing and shipping need every lot paid; a decline mid-fulfilment takes that away.
  const next = o.verified ? NEXT[o.status] : undefined
  const back = o.verified ? ORDER_STEP_BACK[o.status] : undefined
  const unk = unknown(o)
  const dec = declined(o).filter((s) => s.review?.reason === 'payment_declined')
  let primary: ReactNode = null
  let declineInMenu = dec.length > 0 && canReview
  if (o.status === 'open' && unk.length > 0 && canReview) {
    primary = (
      <button type="button" className="btn small tinted" onClick={() => onPick(unk[0]!)} data-testid="name-product">
        Name the product
      </button>
    )
  } else if (next && canFulfil && !held) {
    primary = (
      <button type="button" className="btn small tinted" onClick={() => move.mutate(next.to)} disabled={move.isPending}>
        {next.label}
      </button>
    )
  } else if (declineInMenu) {
    primary = (
      <button type="button" className="btn small" onClick={() => onDecline(dec[0]!)} data-testid="resolve-decline">
        Resolve decline
      </button>
    )
    declineInMenu = false
  }
  const more: ReactNode[] = [
    <Link key="open" role="menuitem" to={`/app/orders/${o.id}`}>
      <Icon name="print" size={16} />
      Open the order and slip
    </Link>,
  ]
  if (declineInMenu)
    more.push(
      <button key="decline" type="button" role="menuitem" onClick={() => onDecline(dec[0]!)}>
        <Icon name="card" size={16} />
        Resolve the declined payment
      </button>,
    )
  // A sale that stands, has a product and has not shipped can get another one. One menu
  // entry per such sale: a buyer's order often holds several lots.
  if (canReview && o.status !== 'shipped' && o.status !== 'cancelled')
    for (const s of o.sales ?? []) {
      if (!s.sku && s.status === 'detected' && s.saleMode === 'winners_pick') {
        // Still waiting for the winner's pick: a "two for one" nobody typed in chat.
        const two = (s.productsAllowed ?? 1) > 1
        more.push(
          <button
            key={`allow-${s.id}`}
            type="button"
            role="menuitem"
            onClick={async () => {
              try {
                await api('PATCH', `/api/orders/sales/${s.id}/allowed`, { products: two ? 1 : 2 })
                await onRemoved(two ? `${o.buyer}'s ${lotText(s)} takes one product again.` : `${o.buyer}'s ${lotText(s)} is a two for one: it takes two of the winner's spots.`)
              } catch (e) {
                setError(e instanceof ApiError ? e.message : 'Could not save that. Try again.')
              }
            }}
          >
            <Icon name={two ? 'minus' : 'plus'} size={16} />
            {two ? `Back to one product for ${lotText(s)}` : `Two for one: ${lotText(s)} takes 2 products`}
          </button>,
        )
      }
      if (!s.sku || (s.status !== 'paid' && s.status !== 'reserved')) continue
      const several = (s.products ?? []).length > 1
      // One product: change it. Several ("two for one"): change or take off each by name.
      for (const p of several ? s.products! : [undefined])
        more.push(
          <button key={`change-${s.id}-${p?.rowId ?? 0}`} type="button" role="menuitem" onClick={() => onChange({ sale: s, product: p })} data-testid={`change-product-${s.id}`}>
            <Icon name="undo" size={16} />
            {p ? `Change ${p.sku.productName} of ${lotText(s)}` : `Change the product of ${lotText(s)}`}
          </button>,
        )
      if (several)
        for (const p of s.products!)
          more.push(
            <button
              key={`off-${s.id}-${p.rowId}`}
              type="button"
              role="menuitem"
              onClick={async () => {
                try {
                  await api('DELETE', `/api/orders/sales/${s.id}/products/${p.rowId}`)
                  await onRemoved(`${p.sku.productName} is off ${o.buyer}'s ${lotText(s)} and back in stock.`)
                } catch (e) {
                  setError(e instanceof ApiError ? e.message : 'Could not take it off. Try again.')
                }
              }}
            >
              <Icon name="minus" size={16} />
              Take {p.sku.productName} off {lotText(s)}
            </button>,
          )
      more.push(
        <button key={`add-${s.id}`} type="button" role="menuitem" onClick={() => onChange({ sale: s, add: true })} data-testid={`add-product-${s.id}`}>
          <Icon name="plus" size={16} />
          Add a product to {lotText(s)}
        </button>,
      )
    }
  if (o.status === 'open' && canReview)
    more.push(
      <Link key="review" role="menuitem" to={`/app/review/${o.showId}`}>
        <Icon name="review" size={16} />
        Open Review for this show
      </Link>,
    )
  if (back && canFulfil)
    more.push(
      <hr key="hr" />,
      <button key="back" type="button" role="menuitem" onClick={() => move.mutate(back.to)}>
        <Icon name="undo" size={16} />
        {back.label}
      </button>,
    )
  more.splice(1, 0, ...extra)
  return (
    <>
      <div className="acts">
        {primary}
        {lead}
        <Menu label={`More for ${o.buyer}`}>{more}</Menu>
      </div>
      {error && <div className="form-error small">{error}</div>}
    </>
  )
}

/** A button and the menu under it: the row's "more" dots by default, or any `trigger` given. */
function Menu({ label, children, trigger, className = 'icon-btn sm', testId }: { label: string; children: ReactNode; trigger?: ReactNode; className?: string; testId?: string }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])
  return (
    <div className="menu-wrap" ref={ref}>
      <button type="button" className={className} aria-label={trigger ? undefined : label} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)} data-testid={testId}>
        {trigger ?? <Icon name="more" size={18} />}
      </button>
      {open && (
        <div className="menu" role="menu" aria-label={label} onClick={() => setOpen(false)}>
          {children}
        </div>
      )}
    </div>
  )
}

export function OrderDetail() {
  const { id = '' } = useParams()
  const { can } = useAuth()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['order', id], queryFn: () => apiGet<{ order: Order }>(`/api/orders/${id}`) })
  const [tracking, setTracking] = useState('')
  const [notes, setNotes] = useState<string | null>(null)
  const [packedBy, setPackedBy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [changing, setChanging] = useState<ProductChange | null>(null)
  const [changed, setChanged] = useState<string | null>(null)
  const status = useMutation({
    mutationFn: (to: OrderStatus) => api('POST', `/api/orders/${id}/status`, { status: to, tracking: tracking || undefined, packedBy: packedBy ?? undefined }),
    onSuccess: () => invalidateSales(qc),
    onError: (e) => setError(e instanceof ApiError ? e.message : 'failed'),
  })
  const save = useMutation({
    mutationFn: () => api('PATCH', `/api/orders/${id}`, { notes, tracking, ...(packedBy !== null ? { packedBy } : {}) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['order', id] }),
  })
  if (q.isPending) return <p className="muted">Loading…</p>
  if (q.isError) return <p className="form-error">Could not load the order.</p>
  const o = q.data.order
  const next = NEXT[o.status]
  return (
    <section>
      <p>
        <Link to="/app/orders">← Orders</Link>
      </p>
      <div className="product-head">
        <h2>
          {o.buyer} · {o.showDate}
          <BuyerFlagChip name={o.buyer} />
        </h2>
        <span className={`status ${o.status}`}>{orderStatusLabel(o.status)}</span>
      </div>
      <p className="small no-print">
        <Link to={`/app/buyers/${encodeURIComponent(o.buyer)}`}>About {o.buyer}: everything they have bought, and the team's notes</Link>
      </p>
      <PackingSlip o={o} />
      <OrderProducts o={o} onChange={setChanging} />
      {changed && (
        <p className="card small no-print" role="status" data-testid="order-changed">
          {changed}
        </p>
      )}
      {changing && (
        <ChangeProduct
          sale={changing.sale}
          buyer={o.buyer}
          showDate={o.showDate}
          product={changing.product}
          add={changing.add}
          onClose={() => setChanging(null)}
          onDone={async (msg) => {
            await invalidateSales(qc)
            setChanging(null)
            setChanged(msg)
          }}
        />
      )}
      {o.parcel && (
        <div className="card no-print" data-testid="order-parcel">
          <b>Where the parcel is</b>
          <Parcel o={o} />
        </div>
      )}
      {can('orders.fulfill') && (
        <div className="card no-print">
          <label className="field">
            <span>Tracking number</span>
            <input value={tracking || o.tracking || ''} onChange={(e) => setTracking(e.target.value)} />
          </label>
          <label className="field">
            <span>Packed by (name or initials)</span>
            <input value={packedBy ?? o.packedBy ?? ''} onChange={(e) => setPackedBy(e.target.value)} maxLength={80} />
          </label>
          <label className="field">
            <span>Notes</span>
            <textarea value={notes ?? o.notes ?? ''} onChange={(e) => setNotes(e.target.value)} rows={2} />
          </label>
          <div className="row-actions">
            <button className="btn" onClick={() => save.mutate()} disabled={save.isPending}>
              Save
            </button>
            {next && (
              <button className="btn primary" onClick={() => status.mutate(next.to)} disabled={status.isPending} data-testid="advance">
                {next.label}
              </button>
            )}
            {o.status === 'picking' && (
              <button className="btn" onClick={() => status.mutate('paid')}>
                Back to queue
              </button>
            )}
          </div>
          {error && <p className="form-error">{error}</p>}
        </div>
      )}
      <BuyerHistory buyer={o.buyer} current={o.id} />
    </section>
  )
}

/** On the order page: each product of each standing sale, with the way to change it. Never printed. */
function OrderProducts({ o, onChange }: { o: Order; onChange: (c: ProductChange) => void }) {
  const { can } = useAuth()
  if (!can('review.act') || o.status === 'shipped' || o.status === 'cancelled') return null
  const sales = (o.sales ?? []).filter((s) => s.sku && (s.status === 'paid' || s.status === 'reserved'))
  if (sales.length === 0) return null
  return (
    <div className="card no-print" data-testid="order-products">
      <b>Products on this order</b>
      <ul style={{ listStyle: 'none', padding: 0, margin: '8px 0', display: 'grid', gap: 6 }}>
        {sales.map((s) =>
          (s.products && s.products.length > 0 ? s.products : [undefined]).map((p) => {
            const sku = p?.sku ?? s.sku!
            const name = `${sku.productName} ${sku.name}`.trim()
            return (
              <li key={`${s.id}-${p?.rowId ?? 0}`} className="row-actions">
                <span>
                  {lotText(s)} · <b>{p ? `${p.quantity} × ` : ''}{name}</b>
                </span>
                <button type="button" className="btn small" onClick={() => onChange({ sale: s, product: p })} aria-label={`Change ${name} of ${lotText(s)}`}>
                  Change the product
                </button>
              </li>
            )
          }),
        )}
      </ul>
      <span className="muted small">The old product goes back to stock and the new one leaves it. The price the buyer paid does not change.</span>
    </div>
  )
}

const NOT_SHIPPING: SaleStatus[] = ['cancelled', 'payment_failed']

/** One empty tick box per unit, so a packer can tick each physical item like on the paper sheet. */
function Ticks({ n }: { n: number }) {
  const boxes = Math.min(n, 24)
  return (
    <span className="ticks" aria-label={`${n} to pack`}>
      {Array.from({ length: boxes }, (_, i) => (
        <span className="tick" data-testid="tick" key={i} />
      ))}
      {n > boxes && <span className="muted small"> ×{n}</span>}
    </span>
  )
}

/**
 * The shop's per-buyer packing sheet: header "Username: <buyer> - <N> Items", a row per
 * auction with a tick per unit, gift lines, and the "Ready to ship / Gift(s) included /
 * Packed by / date" footer. Whatnot's auction number is not in chat, so the Auction column
 * shows the auction type (or "Listing", as on the sheet) and the chat line it came from.
 */
function PackingSlip({ o }: { o: Order }) {
  const shipping = (o.sales ?? []).filter((s) => !NOT_SHIPPING.includes(s.status))
  const dropped = (o.sales ?? []).filter((s) => NOT_SHIPPING.includes(s.status))
  const gifts = o.gifts ?? []
  const date = fmtLongDate(o.showDate)
  const count = o.items + gifts.length // the sheet counts gift lines among the items
  const extras = shipping.reduce((n, s) => n + s.giftCount, 0) // "Gift N" lots: free gifts in the box
  const label = (s: Sale) => `${lotLabel(s.auctionType) || 'Listing'}${s.seq ? ` · line ${s.seq}` : ''}`
  return (
    <div className="card slip" data-testid="packing-slip">
      <div className="slip-head">
        <div className="slip-title" data-testid="slip-title">
          Username: <b>{o.buyer}</b> – {count} Item{count === 1 ? '' : 's'}
          {extras > 0 && ` + ${extras} Gift${extras === 1 ? '' : 's'}`}
        </div>
        <div className="muted small">
          Auction date {date} · order #{o.id}
          {o.status === 'open' && <b className="no-print"> · an item still needs a decision in Review</b>}
        </div>
      </div>
      <table className="table slip-table">
        <thead>
          <tr>
            <th>Auction date</th>
            <th>Auction</th>
            <th>Username</th>
            <th>Product checklist</th>
            <th>Gift</th>
            <th className="no-print">Payment</th>
            <th className="slip-notes">Notes</th>
          </tr>
        </thead>
        <tbody>
          {shipping.map((s) => (
            <tr key={s.id} data-testid={`slip-sale-${s.id}`}>
              <td className="muted">{o.showDate}</td>
              <td>{label(s)}</td>
              <td>{o.buyer}</td>
              <td>
                <Ticks n={s.quantity} /> {s.sku ? `${s.sku.productName} ${s.sku.name}`.trim() : s.item}
                {s.sku ? <code className="sku">{s.sku.code}</code> : !s.notOurs && <span className="badge warn">unmatched</span>}
                {s.quantity > 1 && <span className="muted small"> × {s.quantity}</span>}
              </td>
              <td>{s.giftCount ? `Gift ${s.giftCount}` : ''}</td>
              <td className="no-print">
                <span className={`status ${s.notOurs ? 'paid' : s.status}`}>{saleLabel(s)}</span>
                {s.externalOrderId && <span className="muted small"> · #{s.externalOrderId}</span>}
              </td>
              <td className="slip-notes" />
            </tr>
          ))}
          {gifts.map((g, i) => (
            <tr key={`gift-${i}`} className="gift" data-testid="slip-gift">
              <td className="muted">{o.showDate}</td>
              <td>Gift{g.seq ? ` · line ${g.seq}` : ''}</td>
              <td>{o.buyer}</td>
              <td>
                <Ticks n={1} /> {g.item || 'Gift'}
              </td>
              <td>✓</td>
              <td className="no-print muted small">gift · no charge</td>
              <td className="slip-notes" />
            </tr>
          ))}
        </tbody>
      </table>
      {dropped.length > 0 && (
        <p className="muted small no-print">
          Not shipping: {dropped.map((s) => `${s.sku ? s.sku.productName : s.item} (${s.status === 'payment_failed' ? 'payment declined' : s.status})`).join(', ')}
        </p>
      )}
      {o.notes && (
        <p className="slip-order-notes">
          <b>Notes:</b> {o.notes}
        </p>
      )}
      <div className="slip-foot" data-testid="slip-foot">
        <span>
          <span className={'tick' + (o.status === 'shipped' ? ' on' : '')} /> Ready to ship
        </span>
        <span>
          <span className={'tick' + (gifts.length || extras ? ' on' : '')} /> Gift(s) included (if applicable)
        </span>
        <span>
          Packed by: {o.packedBy ? <b>{o.packedBy}</b> : <span className="blank" />}
        </span>
        <span>
          Date: {o.packedAt ? new Date(o.packedAt).toLocaleDateString() : <span className="blank" />}
        </span>
      </div>
      <p className="muted small no-print">
        {o.items} item{o.items === 1 ? '' : 's'} · {money(o.totalCents)}
      </p>
      <button className="btn small" onClick={() => window.print()}>
        Print packing slip
      </button>
    </div>
  )
}

function BuyerHistory({ buyer, current }: { buyer: string; current: number }) {
  const q = useQuery({ queryKey: ['buyer', buyer], queryFn: () => apiGet<{ orders: Order[] }>(`/api/orders/buyers/${encodeURIComponent(buyer)}`) })
  const others = (q.data?.orders ?? []).filter((o) => o.id !== current)
  if (!others.length) return null
  return (
    <>
      <h3>{buyer} in other shows</h3>
      <table className="table small">
        <thead>
          <tr>
            <th>Show</th>
            <th className="num">Items</th>
            <th>Total</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {others.map((o) => (
            <tr key={o.id}>
              <td>
                <Link to={`/app/orders/${o.id}`}>{o.showDate}</Link>
              </td>
              <td className="num">{o.items}</td>
              <td>{money(o.totalCents)}</td>
              <td>
                <span className={`status ${o.status}`}>{orderStatusLabel(o.status)}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

/** The way to "Giveaways to assign", with how many wait; hidden until Whatnot lists one. */
function GiveawaysLink() {
  const q = useQuery({ queryKey: ['giveaways'], queryFn: () => apiGet<{ giveaways: unknown[]; waiting: number }>('/api/giveaways') })
  if (!q.data?.giveaways?.length) return null
  return (
    <Link className="btn ghost" to="/app/orders/giveaways" data-testid="giveaways-link">
      <Icon name="gift" size={16} />
      Giveaways
      {q.data.waiting > 0 && <span className="tab-count">{q.data.waiting}</span>}
    </Link>
  )
}

export function PickList() {
  const { showId = '' } = useParams()
  const q = useQuery({ queryKey: ['pick-list', showId], queryFn: () => apiGet<{ items: PickItem[]; toPick?: ToPick[] }>(`/api/orders/shows/${showId}/pick-list`) })
  const toPick = q.data?.toPick ?? []
  return (
    <section>
      <p>
        <Link to={`/app/orders?view=pack&show=${showId}`}>← Orders</Link>
      </p>
      <h2>Pick list</h2>
      <p className="muted small">Quantities to pull for every paid item of this show that has not shipped, product by product.</p>
      {q.data && (
        <table className="table" data-testid="pick-list">
          <thead>
            <tr>
              <th>SKU</th>
              <th>Product</th>
              <th className="num">Quantity</th>
              <th className="num">Orders</th>
            </tr>
          </thead>
          <tbody>
            {q.data.items.map((i) => (
              <tr key={i.skuId}>
                <td>
                  <code>{i.code}</code>
                </td>
                <td>
                  {i.product} {i.variant}
                </td>
                <td className="num">
                  <b>{i.quantity}</b>
                </td>
                <td className="num">{i.orders}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {toPick.length > 0 && (
        <>
          <h3>Paid on Whatnot, product to pick</h3>
          <p className="muted small">
            {plural(toPick.length, 'win is', 'wins are')} paid on Whatnot with no product named yet: the winner picks it. Name each one in Review to add it to the list above.
          </p>
          <table className="table" data-testid="to-pick">
            <thead>
              <tr>
                <th>Buyer</th>
                <th>Lot</th>
                <th>Winner's pick</th>
                <th className="num">Price</th>
              </tr>
            </thead>
            <tbody>
              {toPick.map((w) => (
                <tr key={w.saleId}>
                  <td>{w.buyer}</td>
                  <td>{w.lot}</td>
                  <td>{w.pick ? (w.pick.state === 'product' ? `${w.pick.spot ?? ''} ${w.pick.name ?? ''}`.trim() : pickStateWords(w.pick.state)) : 'not read yet'}</td>
                  <td className="num">{w.priceCents == null ? '' : money(w.priceCents * w.quantity)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <button className="btn small" onClick={() => window.print()}>
        Print
      </button>
    </section>
  )
}
