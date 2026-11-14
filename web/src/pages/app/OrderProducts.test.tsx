import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { AuthContext, type AuthState } from '../../auth/context'
import type { Order, Product, Sale, Sku } from '../../lib/api'
import { OrderDetail } from './Orders'

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
const sku = (id: number, productId: number, code: string): Sku => ({ id, productId, code, name: '', priceCents: 100, weightOz: null, onHand: 9, reserved: 0, available: 9, short: 0, lowStockAt: 0, low: false, active: true, aliases: [] })
const products: Product[] = [
  { id: 1, name: 'Red bundle', notes: null, active: true, skus: [sku(7, 1, 'RED')] } as unknown as Product,
  { id: 2, name: 'Blue bundle', notes: null, active: true, skus: [sku(8, 2, 'BLUE')] } as unknown as Product,
]
const RED = { id: 7, code: 'RED', name: '', productName: 'Red bundle' }
// Only what the page reads. The buyer is made up.
const paid = { id: 51, showId: 3, showDate: '2026-11-07', buyer: 'made_up_kat', sku: RED, item: 'Red special', itemTitle: 'Red special', lot: null, quantity: 1, priceCents: 1200, auctionType: '', status: 'paid', statusReason: null, flagged: false, giftCount: 0, saleMode: 'listing', review: null, products: [{ rowId: 61, quantity: 1, shareCents: 1200, sku: RED }] } as unknown as Sale
const order = (over: Partial<Order> = {}): Order => ({ id: 30, showId: 3, showDate: '2026-11-07', buyer: 'made_up_kat', status: 'paid', verified: true, items: 1, totalCents: 1200, notes: null, tracking: null, pickedAt: null, packedAt: null, packedBy: null, shippedAt: null, createdAt: '2026-11-08T03:12:00Z', gifts: null, sales: [paid], ...over }) as Order

function mount(o: Order, permissions = ['orders.view', 'review.act']) {
  const calls: { method: string; url: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method && init.method !== 'GET') {
        calls.push({ method: init.method, url, body: init.body ? JSON.parse(String(init.body)) : null })
        return new Response(JSON.stringify({ changed: true, picked: false }), { status: 200 })
      }
      if (url === '/api/orders/30') return new Response(JSON.stringify({ order: o }), { status: 200 })
      if (url === '/api/products') return new Response(JSON.stringify({ products }), { status: 200 })
      if (url === '/api/auth/csrf') return new Response(JSON.stringify({ token: 't' }), { status: 200 })
      return new Response('{}', { status: 404 })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AuthContext.Provider value={auth(permissions)}>
        <MemoryRouter initialEntries={['/app/orders/30']}>
          <Routes>
            <Route path="/app/orders/:id" element={<OrderDetail />} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

test("the order page lists the order's products, and each can be changed there", async () => {
  const calls = mount(order())
  const card = await screen.findByTestId('order-products')
  expect(card).toHaveTextContent('1 × Red bundle')
  fireEvent.click(within(card).getByRole('button', { name: /Change Red bundle/ }))
  const box = await screen.findByRole('combobox', { name: /New product for made_up_kat/ })
  fireEvent.focus(box)
  fireEvent.change(box, { target: { value: 'blue' } })
  fireEvent.click(await screen.findByRole('option', { name: /Blue bundle/ }))
  fireEvent.click(screen.getByTestId('change-product-save'))
  await waitFor(() => expect(calls).toEqual([{ method: 'PATCH', url: '/api/orders/sales/51/product', body: { skuId: 8, rowId: 61 } }]))
  expect(await screen.findByTestId('order-changed')).toHaveTextContent('is now Blue bundle')
})

test('a shipped order, or someone who cannot settle Review, gets no way to change a product', async () => {
  mount(order({ status: 'shipped' }))
  await screen.findByText(/made_up_kat · 2026-11-07/)
  expect(screen.queryByTestId('order-products')).toBeNull()
})

test('without the review permission the products are not offered for change', async () => {
  mount(order(), ['orders.view'])
  await screen.findByText(/made_up_kat · 2026-11-07/)
  expect(screen.queryByTestId('order-products')).toBeNull()
})
