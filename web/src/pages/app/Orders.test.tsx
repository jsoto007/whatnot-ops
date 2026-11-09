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
    sales: [sale({ buyer: 'amyshopsalot', orderId: 4, sku: RED, item: '$1 BEAUTY PULLS #522', lot: 522, status: 'paid', paidAt: '2026-11-08T03:14:00Z', wonAt: '2026-11-08T03:14:00Z', quantity: 2 })],
  }),
  order({
    id: 5,
    buyer: 'kels.finds',
    status: 'picking',
    verified: true,
    pickedAt: '2026-11-08T20:10:00Z',
    sales: [sale({ buyer: 'kels.finds', orderId: 5, sku: RED, item: '$1 BEAUTY PULLS #530', lot: 530, status: 'paid', paidAt: '2026-11-08T03:30:00Z', wonAt: '2026-11-08T03:30:00Z' })],
  }),
  order({
    id: 6,
    buyer: 'scrollstoppers',
    status: 'cancelled',
    sales: [sale({ buyer: 'scrollstoppers', orderId: 6, sku: RED, item: '$1 BEAUTY PULLS #531', lot: 531, status: 'payment_failed', priceCents: 7900, review: { id: 14, reason: 'payment_declined' } })],
  }),
]
const sku = (id: number, productId: number, code: string, name: string): Sku => ({ id, productId, code, name, priceCents: 100, onHand: 412, reserved: 0, available: 412, short: 0, lowStockAt: 0, low: false, active: true, aliases: [] })
const products: Product[] = [
  { id: 1, name: '$1 Beauty Pulls', notes: null, active: true, skus: [sku(7, 1, 'BP-1', '')] },
  { id: 2, name: 'Beauty Blind Bag', notes: null, active: true, skus: [sku(8, 2, 'BB-01', '')] },
]

function mount(permissions = ['orders.view', 'orders.fulfill', 'review.act', 'inventory.view'], path = '/app/orders?view=all') {
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
      if (url.startsWith('/api/orders')) return new Response(JSON.stringify({ orders }), { status: 200 })
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
  expect(within(screen.getByTestId('order-scrollstoppers')).getByRole('button', { name: 'Resolve decline' })).toBeInTheDocument()
  // the oldest win comes first within a state; ready orders come before stuck ones
  const rows = screen.getAllByTestId(/^order-/).map((r) => r.getAttribute('data-testid'))
  expect(rows).toEqual(['order-amyshopsalot', 'order-kels.finds', 'order-moonlove3', 'order-taravat08', 'order-scrollstoppers'])
})

test('naming the product is for that buyer’s lot alone unless the person widens it', async () => {
  const posts = mount()
  fireEvent.click(within(await screen.findByTestId('order-moonlove3')).getByRole('button', { name: 'Name the product' }))
  const dialog = await screen.findByRole('dialog')
  expect(dialog).toHaveTextContent('Which product is “$1 BEAUTY PULLS #520”?')
  expect(dialog).toHaveTextContent('moonlove3 · lot #520')
  expect(within(dialog).getByRole('combobox', { name: 'Product for moonlove3’s lot #520'.replace('’', "'") })).toBeInTheDocument()
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
  expect(posts[0]).toEqual({ url: '/api/review/11/resolve', body: { action: 'assign_sku', skuId: 7, remember: true, applyToSimilar: false } })
  expect(await screen.findByRole('status')).toHaveTextContent("$1 Beauty Pulls set for moonlove3's lot #520.")
  expect(screen.queryByRole('dialog')).toBeNull()
})

test('the banner names the item once for every lot with that title, and says so', async () => {
  const posts = mount()
  const banner = await screen.findByTestId('stuck-banner')
  expect(banner).toHaveTextContent('2 orders are waiting on one item the shop does not know yet.')
  expect(banner).toHaveTextContent('The chat called it “$1 BEAUTY PULLS #…”')
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
  expect(posts[0]).toEqual({ url: '/api/review/11/resolve', body: { action: 'assign_sku', skuId: 7, remember: true, applyToSimilar: true } })
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
