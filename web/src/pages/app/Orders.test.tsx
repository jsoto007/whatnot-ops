import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router'
import { AuthContext, type AuthState } from '../../auth/context'
import type { BoardBuyer, BoardTab, Giveaway, Order, Product, Sale, Sku } from '../../lib/api'
import { unknownWords } from '../../lib/labels'
import { Orders } from './Orders'

const auth = (permissions: string[]): AuthState => ({
  me: {
    user: { id: 1, email: 'm@x.test', name: 'Mia', role: 'manager', disabled: false, createdAt: '', lastLoginAt: null },
    organization: { id: 1, name: 'Demo Card Shop', slug: 'demo', hostUsername: 'host_shop' },
    permissions,
  },
  loading: false,
  signupsOpen: false,
  signupAdminOnly: false,
  setupCodeRequired: false,
  can: (p) => permissions.includes(p),
  setMe: () => {},
  logout: async () => {},
})

let nextId = 100
const sale = (over: Partial<Sale> & { buyer: string; orderId: number }): Sale => ({
  id: nextId++,
  showId: 3,
  showDate: '2026-11-07',
  sku: null,
  item: '$1 BEAUTY PULLS #520',
  itemTitle: '$1 BEAUTY PULLS',
  lot: 520,
  rememberName: false,
  quantity: 1,
  priceCents: 700,
  auctionType: '',
  status: 'detected',
  statusReason: null,
  flagged: false,
  giftCount: 0,
  externalOrderId: null,
  paidAt: null,
  seq: 12,
  rawText: null,
  source: 'player',
  wonAt: '2026-11-08T03:12:00Z',
  createdAt: '2026-11-08T03:12:00Z',
  review: null,
  ...over,
})
const order = (over: Partial<Order> & { id: number; buyer: string; sales: Sale[] }): Order => ({
  showId: 3,
  showDate: '2026-11-07',
  status: 'open',
  verified: false,
  items: over.sales.reduce((n, s) => n + s.quantity, 0),
  totalCents: over.sales.reduce((n, s) => n + (s.priceCents ?? 0) * s.quantity, 0),
  notes: null,
  tracking: null,
  pickedAt: null,
  packedAt: null,
  packedBy: null,
  shippedAt: null,
  createdAt: '2026-11-08T03:12:00Z',
  gifts: null,
  ...over,
})
const RED = { id: 7, code: 'BP-1', name: '', productName: '$1 Beauty Pulls' }
const orders: Order[] = [
  order({ id: 1, buyer: 'moonlove3', sales: [sale({ buyer: 'moonlove3', orderId: 1, review: { id: 11, reason: 'unmatched_item' } })] }),
  order({
    id: 2,
    buyer: 'taravat08',
    sales: [
      sale({ buyer: 'taravat08', orderId: 2, item: '$1 BEAUTY PULLS #521', lot: 521, wonAt: '2026-11-08T03:13:00Z', review: { id: 12, reason: 'unmatched_item' } }),
      sale({ buyer: 'taravat08', orderId: 2, item: '$1 BEAUTY PULLS #527', lot: 527, wonAt: '2026-11-08T03:20:00Z', review: { id: 13, reason: 'unmatched_item' } }),
    ],
  }),
  order({
    id: 4,
    buyer: 'amyshopsalot',
    status: 'paid',
    verified: true,
    sales: [sale({ buyer: 'amyshopsalot', orderId: 4, sku: RED, item: '$1 BEAUTY PULLS #522', lot: 522, status: 'paid', paidAt: '2026-11-08T03:14:00Z', wonAt: '2026-11-08T03:14:00Z', quantity: 2, review: { id: 17, reason: 'lot_won_twice' } })],
  }),
  order({
    id: 5,
    buyer: 'kels.finds',
    status: 'picking',
    verified: true,
    pickedAt: '2026-11-08T20:10:00Z',
    sales: [sale({ buyer: 'kels.finds', orderId: 5, sku: RED, item: '$1 BEAUTY PULLS #530', lot: 530, status: 'paid', paidAt: '2026-11-08T03:30:00Z', wonAt: '2026-11-08T03:30:00Z', review: { id: 16, reason: 'decline_seen' } })],
  }),
  order({
    id: 7,
    buyer: 'pinkpony_22',
    status: 'picking',
    verified: false,
    pickedAt: '2026-11-08T20:12:00Z',
    sales: [sale({ buyer: 'pinkpony_22', orderId: 7, sku: RED, item: '$1 BEAUTY PULLS #533', lot: 533, status: 'payment_failed', wonAt: '2026-11-08T03:31:00Z', review: { id: 15, reason: 'payment_declined' } })],
  }),
  order({
    id: 6,
    buyer: 'scrollstoppers',
    status: 'cancelled',
    sales: [sale({ buyer: 'scrollstoppers', orderId: 6, sku: RED, item: '$1 BEAUTY PULLS #531', lot: 531, status: 'payment_failed', priceCents: 7900, review: { id: 14, reason: 'payment_declined' } })],
  }),
]
const sku = (id: number, productId: number, code: string, name: string): Sku => ({ id, productId, code, name, priceCents: 100, weightOz: null, onHand: 412, reserved: 0, available: 412, short: 0, lowStockAt: 0, low: false, active: true, aliases: [] })
const products: Product[] = [
  { id: 1, name: '$1 Beauty Pulls', notes: null, active: true, skus: [sku(7, 1, 'BP-1', '')] },
  { id: 2, name: 'Beauty Blind Bag', notes: null, active: true, skus: [sku(8, 2, 'BB-01', '')] },
]

/** The server's tab rule (`order_board.tab_of`) for these fixtures: anything open, cancelled or
 *  with a question that holds the box waits in Need review. */
const HOLDS = ['unmatched_item', 'payment_declined', 'decline_seen', 'lot_won_twice']
const tabOf = (o: Order): BoardTab =>
  o.status === 'shipped' ? 'shipped' : o.status === 'open' || o.status === 'cancelled' || (o.sales ?? []).some((s) => s.review && HOLDS.includes(s.review.reason)) ? 'review' : 'pack'
const buyerOf = (o: Order, over: Partial<BoardBuyer> = {}): BoardBuyer => ({
  username: o.buyer,
  tab: tabOf(o),
  orderId: o.id,
  order: o,
  giveaways: [],
  giveawayOnly: false,
  cancelled: o.status === 'cancelled',
  hideable: o.status === 'cancelled',
  parcel: null,
  readyMarked: false,
  mayMarkReady: tabOf(o) === 'review' && o.status !== 'cancelled',
  tier: null,
  shows: 2,
  ...over,
})
const giveaway = (id: number, buyer: string, product: Giveaway['product'] = null): Giveaway => ({ id, buyer, title: 'Mystery gift', quantity: 1, showId: 3, showDate: '2026-11-07', wonAt: '2026-11-08T03:40:00Z', cancelled: false, product })

function mount(permissions = ['orders.view', 'orders.fulfill', 'review.act', 'inventory.view'], path = '/app/orders?view=review', list: Order[] = orders, extra: BoardBuyer[] = []) {
  const posts: { url: string; body: unknown }[] = []
  const buyers = [...list.map((o) => buyerOf(o)), ...extra]
  const showsOf = [...new Set(buyers.map((b) => b.order?.showId ?? 3))].sort((a, b) => b - a)
  const onShow = (id: number) => buyers.filter((b) => (b.order?.showId ?? 3) === id)
  const tally = (bs: BoardBuyer[]) => ({ review: bs.filter((b) => b.tab === 'review').length, pack: bs.filter((b) => b.tab === 'pack').length, shipped: bs.filter((b) => b.tab === 'shipped').length, cancelled: bs.filter((b) => b.tab === 'review' && b.hideable).length })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        posts.push({ url, body: JSON.parse(String(init.body)) })
        const also = (JSON.parse(String(init.body)) as { applyToSimilar?: boolean }).applyToSimilar ? 2 : 0
        return new Response(JSON.stringify({ alsoResolved: also, changed: true }), { status: 200 })
      }
      if (url === '/api/orders/board')
        return new Response(JSON.stringify({ shows: showsOf.map((id) => ({ showId: id, showDate: id === 3 ? '2026-11-07' : '2026-11-05', title: id === 3 ? 'Friday pulls' : null, ...tally(onShow(id)) })) }), { status: 200 })
      const one = /^\/api\/orders\/shows\/(\d+)\/board$/.exec(url)
      if (one) {
        const id = Number(one[1])
        return new Response(JSON.stringify({ showId: id, showDate: '2026-11-07', title: null, counts: tally(onShow(id)), buyers: onShow(id) }), { status: 200 })
      }
      if (url === '/api/giveaways') return new Response(JSON.stringify({ giveaways: [], waiting: 0 }), { status: 200 })
      if (url === '/api/products') return new Response(JSON.stringify({ products }), { status: 200 })
      if (url === '/api/auth/csrf') return new Response(JSON.stringify({ token: 't' }), { status: 200 })
      return new Response('{}', { status: 404 })
    }),
  )
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <AuthContext.Provider value={auth(permissions)}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/app/orders" element={<Orders />} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
  return posts
}
const fetched = () => (globalThis.fetch as unknown as { mock: { calls: [string, RequestInit?][] } }).mock.calls.map(([url]) => url)

async function pickBeautyPulls(dialog: HTMLElement) {
  const box = within(dialog).getByRole('combobox')
  fireEvent.focus(box)
  fireEvent.change(box, { target: { value: 'beauty pulls' } })
  await waitFor(() => expect(within(dialog).getAllByRole('option')).toHaveLength(1))
  fireEvent.click(within(dialog).getByRole('option'))
}

test('every row names its state and the one verb that moves it on', async () => {
  mount()
  const moon = await screen.findByTestId('order-moonlove3')
  expect(moon).toHaveTextContent('Needs review')
  expect(moon).toHaveTextContent('item not recognised')
  expect(moon).toHaveTextContent('1 × “$1 BEAUTY PULLS”')
  expect(within(moon).getByRole('button', { name: 'Name the product' })).toBeInTheDocument()
  const tara = screen.getByTestId('order-taravat08')
  expect(tara).toHaveTextContent('2 × “$1 BEAUTY PULLS”')
  expect(tara).toHaveTextContent('#521')
  expect(tara).toHaveTextContent('2 items not recognised')
  const amy = screen.getByTestId('order-amyshopsalot')
  expect(amy).toHaveTextContent('Ready to pick')
  expect(amy).toHaveTextContent('2 × $1 Beauty Pulls')
  // Held in Need review (two winners on a lot, a card declined on the stream): the box is not
  // moved on from here, whatever its own state.
  expect(within(amy).queryByRole('button', { name: 'Start picking' })).toBeNull()
  expect(within(screen.getByTestId('order-kels.finds')).queryByRole('button', { name: 'Mark packed' })).toBeNull()
  // the stream showed this buyer's card declined; the sale stands until Whatnot's order says
  expect(screen.getByTestId('decline-seen-kels.finds')).toHaveTextContent('card declined on the stream, waiting for Whatnot')
  expect(screen.queryByTestId('decline-seen-amyshopsalot')).toBeNull()
  // a lot the banner showed won by two buyers: said on the row, settled in Review
  expect(screen.getByTestId('lot-won-twice-amyshopsalot')).toHaveTextContent('a lot here has two winners, see Review')
  expect(within(screen.getByTestId('order-scrollstoppers')).getByRole('button', { name: 'Resolve decline' })).toBeInTheDocument()
  // one show, every buyer of it waiting here, oldest win first; nothing from another tab
  const rows = screen.getAllByTestId(/^order-/).map((r) => r.getAttribute('data-testid'))
  expect(rows).toEqual(['order-moonlove3', 'order-scrollstoppers', 'order-amyshopsalot', 'order-taravat08', 'order-kels.finds', 'order-pinkpony_22'])
  expect(screen.getByTestId('tab-review')).toHaveTextContent('Need review6')
  expect(screen.getByTestId('tab-pack')).toHaveTextContent('Ready to pack0')
  // a decline while picking: the order is no longer fully paid, so no step forward is offered
  const pink = screen.getByTestId('order-pinkpony_22')
  expect(pink).toHaveTextContent('Picking')
  expect(pink).toHaveTextContent('payment declined')
  expect(within(pink).queryByRole('button', { name: 'Mark packed' })).toBeNull()
  expect(within(pink).getByRole('button', { name: 'Resolve decline' })).toBeInTheDocument()
})

test('naming the product is for that buyer’s lot alone unless the person widens it', async () => {
  const posts = mount()
  fireEvent.click(within(await screen.findByTestId('order-moonlove3')).getByRole('button', { name: 'Name the product' }))
  const dialog = await screen.findByRole('dialog')
  expect(dialog).toHaveTextContent('Which product is “$1 BEAUTY PULLS #520”?')
  expect(dialog).toHaveTextContent('moonlove3 · lot #520')
  expect(within(dialog).getByRole('combobox', { name: 'Product for moonlove3’s lot #520'.replace('’', "'") })).toBeInTheDocument()
  // "… #520" names one lot of one show: it is never learned, so the box is not offered
  expect(within(dialog).queryByRole('checkbox', { name: /Remember this name/ })).toBeNull()
  const widen = within(dialog).getByTestId('apply-similar')
  expect(widen).not.toBeChecked()
  expect(dialog).toHaveTextContent('Also use it for the 2 other lots titled “$1 BEAUTY PULLS #…” in this show')
  const use = within(dialog).getByTestId('use-product')
  expect(use).toBeDisabled()
  await pickBeautyPulls(dialog)
  expect(use).toBeEnabled()
  expect(use).toHaveTextContent('Use it for this lot')
  expect(within(dialog).getByTestId('stock-note')).toHaveTextContent('412 available · 1 unit will be sold from it')
  fireEvent.click(use)
  await waitFor(() => expect(posts).toHaveLength(1))
  expect(posts[0]).toEqual({ url: '/api/review/11/resolve', body: { action: 'assign_sku', skuId: 7, remember: false, applyToSimilar: false } })
  expect(await screen.findByRole('status')).toHaveTextContent("$1 Beauty Pulls set for moonlove3's lot #520.")
  expect(screen.queryByRole('dialog')).toBeNull()
})

test('the banner names the item once for every lot with that title, and says so', async () => {
  const posts = mount()
  const banner = await screen.findByTestId('stuck-banner')
  expect(banner).toHaveTextContent('2 orders are waiting on one item the shop does not know yet.')
  expect(banner).toHaveTextContent('The chat called it “$1 BEAUTY PULLS #…”. Name the product once and every one of these lots is settled.')
  fireEvent.click(within(banner).getByTestId('name-it-once'))
  const dialog = await screen.findByRole('dialog')
  // the oldest lot is the one being named; the others follow because the box is ticked
  expect(dialog).toHaveTextContent('Which product is “$1 BEAUTY PULLS #520”?')
  expect(within(dialog).getByTestId('apply-similar')).toBeChecked()
  await pickBeautyPulls(dialog)
  const use = within(dialog).getByTestId('use-product')
  expect(use).toHaveTextContent('Use it for 3 lots')
  expect(within(dialog).getByTestId('stock-note')).toHaveTextContent('3 units will be sold from it')
  fireEvent.click(use)
  await waitFor(() => expect(posts).toHaveLength(1))
  expect(posts[0]).toEqual({ url: '/api/review/11/resolve', body: { action: 'assign_sku', skuId: 7, remember: false, applyToSimilar: true, applyTo: [12, 13] } })
  expect(await screen.findByRole('status')).toHaveTextContent("set for moonlove3's lot #520 and 2 other lots.")
})

test('the next step posts the status; the menu offers the one step back', async () => {
  // The same two orders with nothing in doubt: in Ready to pack.
  const settle = (o: Order): Order => ({ ...o, sales: (o.sales ?? []).map((x) => ({ ...x, review: null })) })
  const posts = mount(undefined, '/app/orders?view=pack', [settle(orders[2]!), settle(orders[3]!)])
  fireEvent.click(within(await screen.findByTestId('order-amyshopsalot')).getByRole('button', { name: 'Start picking' }))
  await waitFor(() => expect(posts).toHaveLength(1))
  expect(posts[0]).toEqual({ url: '/api/orders/4/status', body: { status: 'picking' } })
  const kels = screen.getByTestId('order-kels.finds')
  fireEvent.click(within(kels).getByRole('button', { name: 'More for kels.finds' }))
  const menu = within(kels).getByRole('menu')
  expect(within(menu).getByRole('menuitem', { name: 'Open the order and slip' })).toHaveAttribute('href', '/app/orders/5')
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Back to Ready to pick' }))
  await waitFor(() => expect(posts).toHaveLength(2))
  expect(posts[1]).toEqual({ url: '/api/orders/5/status', body: { status: 'paid' } })
  expect(within(kels).queryByRole('menu')).toBeNull()
})

test('a declined lot is settled from its row', async () => {
  const posts = mount()
  fireEvent.click(within(await screen.findByTestId('order-scrollstoppers')).getByRole('button', { name: 'Resolve decline' }))
  const dialog = await screen.findByRole('dialog')
  expect(dialog).toHaveTextContent('Payment declined')
  expect(dialog).toHaveTextContent('scrollstoppers · lot #531')
  fireEvent.click(within(dialog).getByTestId('paid-after-all'))
  await waitFor(() => expect(posts).toHaveLength(1))
  expect(posts[0]).toEqual({ url: '/api/review/14/resolve', body: { action: 'set_status', status: 'paid' } })
})

test('without the review permission the stuck rows and the banner offer nothing to name', async () => {
  mount(['orders.view', 'orders.fulfill'])
  const moon = await screen.findByTestId('order-moonlove3')
  expect(within(moon).queryByRole('button', { name: 'Name the product' })).toBeNull()
  expect(screen.queryByTestId('stuck-banner')).toBeNull()
  // nor can they mark a box ready to pack
  fireEvent.click(within(moon).getByRole('button', { name: 'More for moonlove3' }))
  expect(screen.queryByRole('menuitem', { name: 'Mark ready to pack' })).toBeNull()
})

test('Ready to pack prints the show’s pick list, each buyer’s slip, and leads to its labels', async () => {
  const ready = order({ id: 41, buyer: 'made_up_ready', status: 'paid', verified: true, sales: [sale({ buyer: 'made_up_ready', orderId: 41, sku: RED, status: 'paid', paidAt: '2026-11-08T03:14:00Z' })] })
  mount(undefined, '/app/orders?view=pack', [...orders, ready])
  const row = await screen.findByTestId('order-made_up_ready')
  expect(screen.getAllByTestId(/^order-/)).toHaveLength(1) // only the box ready to pack
  expect(screen.getByTestId('print-pick-list-3')).toHaveAttribute('href', '/app/orders/slips/3?only=pack')
  expect(screen.getByTestId('labels-3')).toHaveAttribute('href', '/app/orders/shipping/3')
  expect(within(row).getByTestId('print-slip-made_up_ready')).toHaveAttribute('href', '/app/orders/slips/3?buyer=made_up_ready')
  expect(within(row).getByRole('button', { name: 'Start picking' })).toBeInTheDocument()
})

test('each show is its own section; only the open one is read', async () => {
  const older = order({ id: 9, buyer: 'late_bird', showId: 2, showDate: '2026-11-05', status: 'paid', verified: true, sales: [sale({ buyer: 'late_bird', orderId: 9, showId: 2, showDate: '2026-11-05', sku: RED, status: 'paid' })] })
  const ready = order({ id: 41, buyer: 'made_up_ready', status: 'paid', verified: true, sales: [sale({ buyer: 'made_up_ready', orderId: 41, sku: RED, status: 'paid' })] })
  mount(undefined, '/app/orders?view=pack', [ready, older])
  await screen.findByTestId('order-made_up_ready')
  const toggles = screen.getAllByRole('button', { expanded: true }).concat(screen.getAllByRole('button', { expanded: false })).filter((b) => b.classList.contains('show-block-toggle'))
  expect(toggles.map((t) => [t.textContent, t.getAttribute('aria-expanded')])).toEqual([
    ['Sat, Nov 7, 2026Friday pulls1 buyer', 'true'],
    ['Thu, Nov 5, 20261 buyer', 'false'],
  ])
  expect(fetched()).not.toContain('/api/orders/shows/2/board')
  fireEvent.click(toggles[1]!)
  expect(await screen.findByTestId('order-late_bird')).toBeInTheDocument()
  expect(fetched()).toContain('/api/orders/shows/2/board')
})

test('links from before the three tabs land on the tab that holds those orders', async () => {
  mount(undefined, '/app/orders?view=queue')
  expect(await screen.findByTestId('tab-pack')).toHaveAttribute('aria-selected', 'true')
})

// ---------- Shipped: where each parcel is, as Whatnot last said (buyers are made up) ----------

const box = (status: string, tracking: string | null) => ({ status, carrier: 'USPS', tracking, changedAt: '2026-11-10T16:00:00Z', url: null })
const parcel = (state: NonNullable<Order['parcel']>['state'], parcels: ReturnType<typeof box>[]): Order['parcel'] => ({ state, changedAt: '2026-11-10T16:00:00Z', parcels })
const shippedOrder = (id: number, buyer: string, where: Order['parcel'], over: Partial<Omit<Order, 'sales'>> = {}) =>
  order({ id, buyer, status: 'shipped', verified: true, shippedAt: '2026-11-09T15:00:00Z', parcel: where, sales: [sale({ buyer, orderId: id, sku: RED, status: 'fulfilled' })], ...over })
const SHIPPED: Order[] = [
  shippedOrder(21, 'buyer_a', parcel('moving', [box('in_transit', '9400A')])),
  shippedOrder(22, 'buyer_b', parcel('delivered', [box('delivered', '9400B')])),
  shippedOrder(23, 'buyer_c', parcel('failed', [box('failed', '9400C')])),
  shippedOrder(24, 'buyer_d', parcel('late', [box('label_generated', '9400D')])),
  // Whatnot lists no parcel for this one: the tracking number someone typed is what there is.
  shippedOrder(25, 'buyer_e', null, { tracking: 'typed by hand 123' }),
]

test('the Shipped tab says where each parcel is, and narrows to the ones that need a look', async () => {
  mount(undefined, '/app/orders?view=shipped', SHIPPED)
  const a = await screen.findByTestId('parcel-buyer_a')
  expect(a).toHaveTextContent('On its way')
  // The row says where the parcel is; the tracking number is in the details under it.
  expect(a).not.toHaveTextContent('9400A')
  const toggle = screen.getByRole('button', { name: 'Details for buyer_a' })
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
  expect(screen.queryByTestId('details-buyer_a')).toBeNull()
  fireEvent.click(toggle)
  expect(toggle).toHaveAttribute('aria-expanded', 'true')
  const details = screen.getByTestId('details-buyer_a')
  expect(details).toHaveTextContent('USPS 9400A')
  expect(within(details).getByRole('link', { name: 'Open the order' })).toHaveAttribute('href', '/app/orders/21')
  fireEvent.click(toggle)
  expect(screen.queryByTestId('details-buyer_a')).toBeNull()
  expect(screen.getByTestId('parcel-buyer_b')).toHaveTextContent('Delivered')
  // The two that need a person are in the warning colour.
  expect(within(screen.getByTestId('parcel-buyer_c')).getByText('Delivery failed')).toHaveClass('pill', 'failed')
  expect(within(screen.getByTestId('parcel-buyer_d')).getByText('Not with the carrier yet')).toHaveClass('pill', 'late')
  expect(within(a).getByText('On its way')).toHaveClass('pill', 'moving')
  expect(screen.queryByTestId('parcel-buyer_e')).toBeNull()
  expect(screen.getByTestId('order-buyer_e')).toHaveTextContent('typed by hand 123')

  const filters = screen.getByTestId('parcel-filters')
  expect(within(filters).getAllByRole('button').map((b) => b.textContent)).toEqual(['All shipped', 'Not with the carrier yet', 'On its way', 'Delivery failed', 'Delivered'])
  expect(within(filters).getByRole('button', { name: /All shipped/ })).toHaveAttribute('aria-pressed', 'true')

  fireEvent.click(within(filters).getByRole('button', { name: /Delivery failed/ }))
  await waitFor(() => expect(screen.getAllByTestId(/^order-/).map((r) => r.getAttribute('data-testid'))).toEqual(['order-buyer_c']))
  fireEvent.click(within(filters).getByRole('button', { name: /Not with the carrier yet/ }))
  await waitFor(() => expect(screen.getAllByTestId(/^order-/).map((r) => r.getAttribute('data-testid'))).toEqual(['order-buyer_d']))
  fireEvent.click(within(filters).getByRole('button', { name: /All shipped/ }))
  await waitFor(() => expect(screen.getAllByTestId(/^order-/)).toHaveLength(5))
})

test('a buyer with two parcels reads each one', async () => {
  mount(undefined, '/app/orders?view=shipped', [shippedOrder(21, 'buyer_a', parcel('moving', [box('delivered', '9400A'), box('in_transit', null)]))])
  expect(await screen.findByTestId('parcel-buyer_a')).toHaveTextContent('On its way')
  fireEvent.click(screen.getByRole('button', { name: 'Details for buyer_a' }))
  const a = screen.getByTestId('details-buyer_a')
  expect(a).toHaveTextContent('Delivered · USPS 9400A')
  expect(a).toHaveTextContent('In transit · USPS')
})

test('every tab says where the parcel is: an order not marked shipped shows it too', async () => {
  // Still open here (its product was never named), yet Whatnot says the parcel is on its way.
  const open = order({ id: 31, buyer: 'buyer_f', status: 'open', parcel: parcel('moving', [box('in_transit', '9400F')]), sales: [sale({ buyer: 'buyer_f', orderId: 31 })] })
  const fresh = order({ id: 32, buyer: 'buyer_g', status: 'paid', verified: true, parcel: parcel('nolabel', [box('created', null)]), sales: [sale({ buyer: 'buyer_g', orderId: 32, sku: RED, status: 'paid' })] })
  mount(undefined, '/app/orders?view=review', [...SHIPPED, open, fresh])
  const f = await screen.findByTestId('parcel-buyer_f')
  expect(within(f).getByText('On its way')).toHaveClass('pill', 'moving')
  fireEvent.click(screen.getByRole('button', { name: 'Details for buyer_f' }))
  expect(screen.getByTestId('details-buyer_f')).toHaveTextContent('USPS 9400F')
  // the parcel filters are the Shipped tab's own
  expect(screen.queryByTestId('parcel-filters')).toBeNull()
  fireEvent.click(screen.getByTestId('tab-pack'))
  expect(within(await screen.findByTestId('parcel-buyer_g')).getByText('No label yet')).toHaveClass('pill', 'nolabel')
})

test('the row shows the first lots of a product; its details show every one', async () => {
  // Eight wins of one product (made-up buyer): the row reads the first three and "+5 more".
  const lots = [601, 602, 603, 604, 605, 606, 607, 608]
  const many = order({ id: 40, buyer: 'made_up_many', status: 'paid', verified: true, sales: lots.map((lot, i) => sale({ id: 900 + i, buyer: 'made_up_many', orderId: 40, sku: RED, item: `$1 BEAUTY PULLS #${lot}`, lot, status: 'paid', paidAt: '2026-11-08T03:50:00Z', wonAt: '2026-11-08T03:50:00Z' })) })
  mount(undefined, '/app/orders?view=pack', [many])
  const row = await screen.findByTestId('order-made_up_many')
  expect(row).toHaveTextContent('8 × $1 Beauty Pulls')
  expect(row).toHaveTextContent('#603')
  expect(row).not.toHaveTextContent('#604')
  expect(row).toHaveTextContent('+5 more')
  fireEvent.click(screen.getByRole('button', { name: 'Details for made_up_many' }))
  const details = screen.getByTestId('details-made_up_many')
  for (const lot of lots) expect(details).toHaveTextContent(`#${lot}`)
  expect(details).not.toHaveTextContent('more')
})

test("in a winner's-pick show an unnamed item waits for the winner, then reads did not pick", () => {
  const waiting = sale({ buyer: 'made_up_ann', orderId: 8, saleMode: 'winners_pick', review: { id: 18, reason: 'unmatched_item' } })
  const noPick = sale({ buyer: 'made_up_ann', orderId: 8, saleMode: 'winners_pick', review: { id: 19, reason: 'unmatched_item', pick: 'none' } })
  const other = sale({ buyer: 'made_up_ann', orderId: 8, review: { id: 20, reason: 'unmatched_item' } })
  expect(unknownWords([waiting])).toBe('waiting for pick')
  expect(unknownWords([waiting, waiting])).toBe('2 waiting for pick')
  expect(unknownWords([noPick])).toBe('did not pick')
  expect(unknownWords([noPick, noPick])).toBe('2 did not pick')
  expect(unknownWords([other])).toBe('item not recognised')
  expect(unknownWords([waiting, noPick, other])).toBe('3 items not recognised')
  // where each listing is one product, an unnamed win is a listing nobody linked yet
  const listing = sale({ buyer: 'made_up_ann', orderId: 8, saleMode: 'listing', review: { id: 21, reason: 'unmatched_item' } })
  expect(unknownWords([listing])).toBe('listing not linked')
  expect(unknownWords([listing, listing])).toBe('2 listings not linked')
  expect(unknownWords([listing, waiting])).toBe('2 items not recognised')
})

test('a sale that stands can get another product from its row menu', async () => {
  mount()
  const amy = await screen.findByTestId('order-amyshopsalot')
  fireEvent.click(within(amy).getByRole('button', { name: 'More for amyshopsalot' }))
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Change the product of lot #522' }))
  const save = await screen.findByTestId('change-product-save')
  expect(save).toBeDisabled() // nothing chosen yet
  const box = screen.getByRole('combobox', { name: /New product for amyshopsalot/ })
  fireEvent.focus(box)
  fireEvent.change(box, { target: { value: 'blind' } })
  fireEvent.click(await screen.findByRole('option', { name: /Blind Bag/ }))
  fireEvent.click(save)
  await waitFor(() => {
    const calls = (globalThis.fetch as unknown as { mock: { calls: [string, RequestInit?][] } }).mock.calls
    const patch = calls.find(([url, init]) => init?.method === 'PATCH' && /\/api\/orders\/sales\/\d+\/product$/.test(url))
    expect(patch && JSON.parse(String(patch[1]!.body))).toEqual({ skuId: 8 })
  })
  // an unnamed or declined sale offers no such entry
  fireEvent.click(within(screen.getByTestId('order-moonlove3')).getByRole('button', { name: 'More for moonlove3' }))
  expect(screen.queryByRole('menuitem', { name: /Change the product/ })).toBeNull()
})

test('a win with two products lists both, and each can be changed or taken off; one more can be added', async () => {
  const bag = { id: 8, code: 'BB-01', name: '', productName: 'Beauty Blind Bag' }
  const two = sale({ buyer: 'made_up_two', orderId: 30, sku: RED, item: '$1 BEAUTY PULLS #540', lot: 540, status: 'paid', paidAt: '2026-11-08T03:50:00Z', wonAt: '2026-11-08T03:50:00Z', products: [{ rowId: 61, quantity: 1, shareCents: 350, sku: RED }, { rowId: 62, quantity: 2, shareCents: 350, sku: bag }] })
  orders.push(order({ id: 30, buyer: 'made_up_two', status: 'paid', verified: true, sales: [two] }))
  try {
    mount(undefined, '/app/orders?view=pack')
    const row = await screen.findByTestId('order-made_up_two')
    expect(row).toHaveTextContent('1 × $1 Beauty Pulls')
    expect(row).toHaveTextContent('2 × Beauty Blind Bag')
    fireEvent.click(within(row).getByRole('button', { name: 'More for made_up_two' }))
    expect(await screen.findByRole('menuitem', { name: 'Change $1 Beauty Pulls of lot #540' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Change Beauty Blind Bag of lot #540' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Add a product to lot #540' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('menuitem', { name: 'Take Beauty Blind Bag off lot #540' }))
    await waitFor(() => {
      const calls = (globalThis.fetch as unknown as { mock: { calls: [string, RequestInit?][] } }).mock.calls
      expect(calls.some(([url, init]) => init?.method === 'DELETE' && url === `/api/orders/sales/${two.id}/products/62`)).toBe(true)
    })
  } finally {
    orders.pop()
  }
})

test("a win still waiting for its winner's pick can be made a two for one, and back", async () => {
  const waiting = sale({ buyer: 'made_up_bo', orderId: 31, saleMode: 'winners_pick', item: 'CHOOSE ANY PRODUCT #6', lot: 6, review: { id: 41, reason: 'unmatched_item' } })
  const already = sale({ buyer: 'made_up_cy', orderId: 32, saleMode: 'winners_pick', item: 'CHOOSE ANY PRODUCT #7', lot: 7, productsAllowed: 2, review: { id: 42, reason: 'unmatched_item' } })
  orders.push(order({ id: 31, buyer: 'made_up_bo', sales: [waiting] }), order({ id: 32, buyer: 'made_up_cy', sales: [already] }))
  try {
    mount()
    const sent = () => {
      const calls = (globalThis.fetch as unknown as { mock: { calls: [string, RequestInit?][] } }).mock.calls
      return calls.filter(([url, init]) => init?.method === 'PATCH' && /\/allowed$/.test(url)).map(([url, init]) => [url, JSON.parse(String(init!.body))])
    }
    fireEvent.click(within(await screen.findByTestId('order-made_up_bo')).getByRole('button', { name: 'More for made_up_bo' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Two for one: lot #6 takes 2 products' }))
    await waitFor(() => expect(sent()).toEqual([[`/api/orders/sales/${waiting.id}/allowed`, { products: 2 }]]))
    fireEvent.click(within(screen.getByTestId('order-made_up_cy')).getByRole('button', { name: 'More for made_up_cy' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Back to one product for lot #7' }))
    await waitFor(() => expect(sent()[1]).toEqual([`/api/orders/sales/${already.id}/allowed`, { products: 1 }]))
    // a listing show's unnamed win is one product: no such entry
    fireEvent.click(within(screen.getByTestId('order-moonlove3')).getByRole('button', { name: 'More for moonlove3' }))
    expect(screen.queryByRole('menuitem', { name: /Two for one/ })).toBeNull()
  } finally {
    orders.pop()
    orders.pop()
  }
})

test("a lot whose title says the winner chooses is never offered as 'name it once'", async () => {
  // Each winner of "CHOOSE ANY PRODUCT YOU WANT #N" picked their own product in chat.
  const choose = (id: number, buyer: string, lot: number) =>
    order({
      id,
      buyer,
      toPick: 1,
      sales: [sale({ buyer, orderId: id, item: `CHOOSE ANY PRODUCT YOU WANT #${lot}`, itemTitle: 'CHOOSE ANY PRODUCT YOU WANT', lot, choiceTitle: true, toPick: true, externalOrderId: `9${lot}`, review: { id: 100 + id, reason: 'unmatched_item' } })],
    })
  mount(undefined, '/app/orders?view=review', [choose(21, 'made_up_ann', 1), choose(22, 'made_up_bo', 2), choose(23, 'made_up_cy', 3)])
  const row = await screen.findByTestId('order-made_up_ann')
  expect(screen.queryByTestId('stuck-banner')).toBeNull()
  // The order is open, but it is paid: it waits for the winner's pick, not for money.
  expect(within(row).getByTestId('to-pick-made_up_ann')).toHaveTextContent('paid on Whatnot')
  fireEvent.click(within(row).getByRole('button', { name: 'Name the product' }))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).queryByTestId('apply-similar')).toBeNull()
})

test('a paid win on a listing that is not one of ours, its question ignored, reads so and lets the order be picked', async () => {
  const off = sale({ buyer: 'made_up_zed', orderId: 9, item: 'Mystery thing', itemTitle: 'Mystery thing', lot: null, externalOrderId: 'WN-9', notOurs: true })
  const named = sale({ buyer: 'made_up_zed', orderId: 9, sku: RED, item: 'Red bundle', itemTitle: 'Red bundle', lot: null, status: 'paid', paidAt: '2026-11-08T03:14:00Z', notOurs: false })
  mount(undefined, '/app/orders?view=pack', [order({ id: 9, buyer: 'made_up_zed', status: 'paid', verified: true, sales: [named, off] })])
  const zed = await screen.findByTestId('order-made_up_zed')
  expect(zed).toHaveTextContent('Ready to pick')
  expect(within(zed).getByTestId('not-ours')).toHaveTextContent('Paid on Whatnot · not one of our products')
  expect(zed).not.toHaveTextContent('“Mystery thing”') // not shown as an item nobody recognised
  expect(within(zed).getByRole('button', { name: 'Start picking' })).toBeInTheDocument()
  expect(within(zed).queryByRole('button', { name: 'Name the product' })).toBeNull()
})

// ---------- Giveaways, badges, a manager's mark, cancelled orders (buyers are made up) ----------

test("a buyer's giveaway sits with their wins, marked; one who only won a giveaway is apart, as an order", async () => {
  const both = order({ id: 50, buyer: 'made_up_both', status: 'paid', verified: true, sales: [sale({ buyer: 'made_up_both', orderId: 50, sku: RED, status: 'paid' })] })
  const giftOnly: BoardBuyer = { ...buyerOf(both), username: 'made_up_gift', orderId: null, order: null, giveawayOnly: true, giveaways: [giveaway(71, 'made_up_gift')], tab: 'review', shows: 0 }
  mount(undefined, '/app/orders?view=review', [], [buyerOf(both, { tab: 'review', giveaways: [giveaway(70, 'made_up_both')] }), giftOnly])
  const row = await screen.findByTestId('order-made_up_both')
  const line = within(row).getByTestId('giveaway-line-70')
  expect(line).toHaveTextContent('Giveaway 1 × needs a product')
  expect(within(line).getByText('Giveaway')).toHaveClass('pill', 'giveaway')
  expect(row).toHaveTextContent('1 × $1 Beauty Pulls') // the wins beside it
  // A box waiting for its giveaway's product is not moved on: naming it is the row's verb.
  expect(within(row).queryByRole('button', { name: 'Start picking' })).toBeNull()
  fireEvent.click(within(row).getByTestId('name-giveaway'))
  expect(await screen.findByRole('dialog', { name: /Name the giveaway's product/ })).toHaveTextContent('made_up_both')
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  // The buyer who bought nothing comes last, under its own heading, and reads as a giveaway.
  const rows = screen.getAllByRole('row').map((r) => r.textContent ?? '')
  expect(rows.findIndex((t) => t.includes('Won a giveaway, bought nothing'))).toBeGreaterThan(rows.findIndex((t) => t.includes('made_up_both')))
  const gift = screen.getByTestId('order-made_up_gift')
  expect(gift).toHaveTextContent('Giveaway only')
  expect(gift).toHaveTextContent('no charge')
  expect(within(gift).getByTestId('name-giveaway')).toBeInTheDocument()
})

test('new and returning buyers share the list; each carries a small badge', async () => {
  const mk = (id: number, buyer: string) => order({ id, buyer, status: 'paid', verified: true, sales: [sale({ buyer, orderId: id, sku: RED, status: 'paid' })] })
  mount(undefined, '/app/orders?view=pack', [], [buyerOf(mk(60, 'made_up_gold'), { tier: 'gold', shows: 12 }), buyerOf(mk(61, 'made_up_copper'), { tier: 'copper', shows: 2 }), buyerOf(mk(62, 'made_up_first'), { tier: null, shows: 1 })])
  const gold = await screen.findByTestId('order-made_up_gold')
  expect(within(gold).getByTestId('tier-gold')).toHaveAttribute('title', expect.stringContaining('12 shows'))
  expect(within(gold).getByTestId('tier-gold')).toHaveTextContent('Gold buyer')
  expect(within(screen.getByTestId('order-made_up_copper')).getByTestId('tier-copper')).toBeInTheDocument()
  expect(screen.getByTestId('order-made_up_first')).toHaveTextContent('new')
})

test('a manager marks a box ready to pack from Need review, and can take it back', async () => {
  const posts = mount(undefined, '/app/orders?view=review', [orders[0]!], [])
  const moon = await screen.findByTestId('order-moonlove3')
  fireEvent.click(within(moon).getByRole('button', { name: 'More for moonlove3' }))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Mark ready to pack' }))
  await waitFor(() => expect(posts).toEqual([{ url: '/api/orders/shows/3/ready', body: { buyer: 'moonlove3', ready: true } }]))
  expect(await screen.findByRole('status')).toHaveTextContent("moonlove3's box is marked ready to pack.")
})

test('a marked box says so in Ready to pack and offers the way back', async () => {
  const posts = mount(undefined, '/app/orders?view=pack', [], [buyerOf(orders[0]!, { tab: 'pack', readyMarked: true, mayMarkReady: false })])
  const moon = await screen.findByTestId('order-moonlove3')
  expect(within(moon).getByTestId('marked-ready-moonlove3')).toHaveTextContent('marked ready to pack by a manager')
  fireEvent.click(within(moon).getByRole('button', { name: 'More for moonlove3' }))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Back to Need review' }))
  await waitFor(() => expect(posts).toEqual([{ url: '/api/orders/shows/3/ready', body: { buyer: 'moonlove3', ready: false } }]))
})

test('cancelled orders wait in Need review, marked, and can be hidden', async () => {
  localStorage.removeItem('wn.ordersHideCancelled')
  mount()
  const scroll = await screen.findByTestId('order-scrollstoppers')
  expect(within(scroll).getByTestId('cancelled-scrollstoppers')).toHaveTextContent('cancelled on Whatnot')
  const hide = screen.getByTestId('hide-cancelled')
  expect(hide).toHaveTextContent('Hide cancelled orders 1')
  fireEvent.click(hide)
  expect(hide).toHaveAttribute('aria-pressed', 'true')
  await waitFor(() => expect(screen.queryByTestId('order-scrollstoppers')).toBeNull())
  expect(screen.getByTestId('tab-review')).toHaveTextContent('Need review5')
  expect(localStorage.getItem('wn.ordersHideCancelled')).toBe('yes')
  fireEvent.click(hide)
  expect(await screen.findByTestId('order-scrollstoppers')).toBeInTheDocument()
})

test('with no tab asked for, the page opens on the first one with someone in it', async () => {
  const ready = order({ id: 41, buyer: 'made_up_ready', status: 'paid', verified: true, sales: [sale({ buyer: 'made_up_ready', orderId: 41, sku: RED, status: 'paid' })] })
  mount(undefined, '/app/orders', [ready])
  expect(await screen.findByTestId('order-made_up_ready')).toBeInTheDocument()
  expect(screen.getByTestId('tab-pack')).toHaveAttribute('aria-selected', 'true')
})

test('"Hide cancelled orders" keeps a cancelled buyer whose giveaway still needs a product', async () => {
  localStorage.setItem('wn.ordersHideCancelled', 'yes')
  try {
    const gone = orders[5]! // scrollstoppers: cancelled, nothing else to do
    const stays = order({ id: 52, buyer: 'made_up_kept', status: 'cancelled', sales: [sale({ buyer: 'made_up_kept', orderId: 52, status: 'cancelled' })] })
    mount(undefined, '/app/orders?view=review', [gone], [buyerOf(stays, { hideable: false, giveaways: [giveaway(80, 'made_up_kept')] })])
    expect(await screen.findByTestId('order-made_up_kept')).toBeInTheDocument()
    expect(screen.queryByTestId('order-scrollstoppers')).toBeNull()
  } finally {
    localStorage.removeItem('wn.ordersHideCancelled')
  }
})
