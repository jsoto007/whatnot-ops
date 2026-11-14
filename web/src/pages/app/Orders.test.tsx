import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router'
import { AuthContext, type AuthState } from '../../auth/context'
import type { Order, Product, Sale, Show, Sku } from '../../lib/api'
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

const SHOW: Show = { id: 3, key: 'k', date: '2026-11-07', startedAt: '2026-11-08T02:00:00Z', updatedAt: '2026-11-08T02:00:00Z', sourceUrl: null, messageCount: 9 }

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

function mount(permissions = ['orders.view', 'orders.fulfill', 'review.act', 'inventory.view'], path = '/app/orders?view=all', list: Order[] = orders) {
  const posts: { url: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        posts.push({ url, body: JSON.parse(String(init.body)) })
        const also = (JSON.parse(String(init.body)) as { applyToSimilar?: boolean }).applyToSimilar ? 2 : 0
        return new Response(JSON.stringify({ alsoResolved: also }), { status: 200 })
      }
      if (url === '/api/shows') return new Response(JSON.stringify({ shows: [SHOW] }), { status: 200 })
      if (url.startsWith('/api/orders')) return new Response(JSON.stringify({ orders: list }), { status: 200 })
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
  expect(within(amy).getByRole('button', { name: 'Start picking' })).toBeInTheDocument()
  expect(within(screen.getByTestId('order-kels.finds')).getByRole('button', { name: 'Mark packed' })).toBeInTheDocument()
  // the stream showed this buyer's card declined; the sale stands until Whatnot's order says
  expect(screen.getByTestId('decline-seen-kels.finds')).toHaveTextContent('card declined on the stream, waiting for Whatnot')
  expect(screen.queryByTestId('decline-seen-amyshopsalot')).toBeNull()
  // a lot the banner showed won by two buyers: said on the row, settled in Review
  expect(screen.getByTestId('lot-won-twice-amyshopsalot')).toHaveTextContent('a lot here has two winners, see Review')
  expect(within(screen.getByTestId('order-scrollstoppers')).getByRole('button', { name: 'Resolve decline' })).toBeInTheDocument()
  // the oldest win comes first within a state; ready orders come before stuck ones
  const rows = screen.getAllByTestId(/^order-/).map((r) => r.getAttribute('data-testid'))
  expect(rows).toEqual(['order-amyshopsalot', 'order-kels.finds', 'order-pinkpony_22', 'order-moonlove3', 'order-taravat08', 'order-scrollstoppers'])
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
  const posts = mount()
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
  expect(within(screen.getByTestId('order-amyshopsalot')).getByRole('button', { name: 'Start picking' })).toBeInTheDocument()
})

test('"Print ready slips" opens the show’s slips for the orders that are ready to pick', async () => {
  mount()
  await screen.findByTestId('order-moonlove3')
  // one order is ready (amyshopsalot), so the button goes straight to its show
  const print = screen.getByTestId('print-ready')
  expect(print).toHaveTextContent('Print 1 ready slip')
  expect(print).toHaveAttribute('href', '/app/orders/slips/3?only=ready')
})

test('with ready orders in several shows, the button lists each show, newest first', async () => {
  const late = order({
    id: 9,
    buyer: 'late_bird',
    showId: 4,
    showDate: '2026-11-05',
    status: 'paid',
    verified: true,
    sales: [sale({ buyer: 'late_bird', orderId: 9, showId: 4, showDate: '2026-11-05', sku: RED, status: 'paid', wonAt: '2026-11-06T03:00:00Z' })],
  })
  mount(undefined, '/app/orders', [...orders, late])
  await screen.findByTestId('order-late_bird')
  fireEvent.click(screen.getByRole('button', { name: 'Print 2 ready slips' }))
  const menu = screen.getByRole('menu', { name: 'Print ready slips, by show' })
  const items = within(menu).getAllByRole('menuitem')
  expect(items.map((i) => i.getAttribute('href'))).toEqual(['/app/orders/slips/3?only=ready', '/app/orders/slips/4?only=ready'])
  expect(items[0]).toHaveTextContent('Nov 7, 2026 · 1 order')
  expect(items[1]).toHaveTextContent('Nov 5, 2026 · 1 order')
})

test('with nothing ready to pick the print button waits', async () => {
  mount(undefined, '/app/orders?view=all', orders.filter((o) => o.status !== 'paid'))
  await screen.findByTestId('order-moonlove3')
  const print = screen.getByTestId('print-ready')
  expect(print).toHaveTextContent('Print ready slips')
  expect(print).toBeDisabled()
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
  expect(a).toHaveTextContent('USPS 9400A')
  expect(screen.getByTestId('parcel-buyer_b')).toHaveTextContent('Delivered')
  // The two that need a person are in the warning colour.
  expect(within(screen.getByTestId('parcel-buyer_c')).getByText('Delivery failed')).toHaveClass('form-error')
  expect(within(screen.getByTestId('parcel-buyer_d')).getByText('Not with the carrier yet')).toHaveClass('form-error')
  expect(within(a).getByText('On its way')).not.toHaveClass('form-error')
  expect(screen.queryByTestId('parcel-buyer_e')).toBeNull()
  expect(screen.getByTestId('order-buyer_e')).toHaveTextContent('typed by hand 123')

  const filters = screen.getByTestId('parcel-filters')
  expect(within(filters).getAllByRole('button').map((b) => b.textContent?.replace(/\s+/g, ' ').trim())).toEqual(['All shipped 5', 'Not with the carrier yet 1', 'On its way 1', 'Delivery failed 1', 'Delivered 1'])
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
  const a = await screen.findByTestId('parcel-buyer_a')
  expect(a).toHaveTextContent('On its way')
  expect(a).toHaveTextContent('Delivered · USPS 9400A')
  expect(a).toHaveTextContent('In transit · USPS')
})

test('with nothing known about any parcel the Shipped tab is as before: no filters', async () => {
  mount(undefined, '/app/orders?view=shipped', [shippedOrder(25, 'buyer_e', null, { tracking: 'typed by hand 123' })])
  expect(await screen.findByTestId('order-buyer_e')).toHaveTextContent('typed by hand 123')
  expect(screen.queryByTestId('parcel-filters')).toBeNull()
})

test('the parcel filters belong to the Shipped tab only', async () => {
  mount(undefined, '/app/orders?view=all&parcel=failed', SHIPPED)
  await screen.findByTestId('order-buyer_a')
  expect(screen.queryByTestId('parcel-filters')).toBeNull()
  // A filter left in the address from the Shipped tab narrows nothing here.
  expect(screen.getAllByTestId(/^order-/)).toHaveLength(5)
})
