import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { AuthContext, type AuthState } from '../../auth/context'
import type { Giveaway, Product, Sku } from '../../lib/api'
import { Giveaways } from './Giveaways'

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
  { id: 2, name: 'Blue bundle', notes: null, active: true, skus: [sku(8, 2, 'BLU-1')] } as unknown as Product,
]
const give = (over: Partial<Giveaway> & { id: number; buyer: string }): Giveaway => ({ title: 'Mystery gift', quantity: 1, showId: 3, showDate: '2026-11-07', wonAt: '2026-11-08T03:12:00Z', cancelled: false, product: null, ...over })
const RED = { id: 7, code: 'RED', name: '', productName: 'Red bundle' }

function mount(list: Giveaway[], permissions = ['orders.view', 'review.act']) {
  const calls: { method: string; url: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method && init.method !== 'GET') {
        calls.push({ method: init.method, url, body: init.body ? JSON.parse(String(init.body)) : null })
        return new Response(JSON.stringify({ changed: true }), { status: 200 })
      }
      if (url === '/api/giveaways') return new Response(JSON.stringify({ giveaways: list, waiting: list.filter((g) => !g.product && !g.cancelled).length }), { status: 200 })
      if (url === '/api/products') return new Response(JSON.stringify({ products }), { status: 200 })
      if (url === '/api/auth/csrf') return new Response(JSON.stringify({ token: 't' }), { status: 200 })
      return new Response('{}', { status: 404 })
    }),
  )
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <AuthContext.Provider value={auth(permissions)}>
        <MemoryRouter initialEntries={['/app/orders/giveaways']}>
          <Routes>
            <Route path="/app/orders/giveaways" element={<Giveaways />} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

test('a waiting giveaway is named from its row; a named one can be changed or taken off', async () => {
  const calls = mount([give({ id: 11, buyer: 'made_up_gia' }), give({ id: 12, buyer: 'made_up_hal', product: RED })])
  expect(await screen.findByTestId('giveaways-waiting')).toHaveTextContent('1 giveaway waiting for a product.')
  const gia = screen.getByTestId('giveaway-made_up_gia')
  expect(gia).toHaveTextContent('Waiting for a product')
  fireEvent.click(within(gia).getByRole('button', { name: 'Name the product' }))
  const save = await screen.findByTestId('giveaway-save')
  expect(save).toBeDisabled() // nothing chosen yet
  const box = screen.getByRole('combobox', { name: "Product for made_up_gia's giveaway" })
  fireEvent.focus(box)
  fireEvent.change(box, { target: { value: 'blue' } })
  fireEvent.click(await screen.findByRole('option', { name: /Blue bundle/ }))
  fireEvent.click(save)
  await waitFor(() => expect(calls).toEqual([{ method: 'PUT', url: '/api/giveaways/11/product', body: { skuId: 8 } }]))
  expect(await screen.findByRole('status')).toHaveTextContent("made_up_gia's giveaway is 1 × Blue bundle.")
  const hal = screen.getByTestId('giveaway-made_up_hal')
  expect(hal).toHaveTextContent('Red bundle')
  expect(within(hal).getByRole('button', { name: 'Change' })).toBeInTheDocument()
  fireEvent.click(within(hal).getByRole('button', { name: 'Take it off' }))
  await waitFor(() => expect(calls[1]).toEqual({ method: 'DELETE', url: '/api/giveaways/12/product', body: null }))
})

test('a giveaway Whatnot cancelled only offers to take its product off', async () => {
  mount([give({ id: 13, buyer: 'made_up_ivy', product: RED, cancelled: true })])
  const row = await screen.findByTestId('giveaway-made_up_ivy')
  expect(row).toHaveTextContent('Whatnot cancelled this giveaway')
  expect(within(row).queryByRole('button', { name: 'Change' })).toBeNull()
  expect(within(row).getByRole('button', { name: 'Take it off' })).toBeInTheDocument()
  expect(screen.getByTestId('giveaways-waiting')).toHaveTextContent('Every giveaway has its product.')
})

test('without the review permission the list is read only; with none listed it says so', async () => {
  mount([give({ id: 11, buyer: 'made_up_gia' })], ['orders.view'])
  const row = await screen.findByTestId('giveaway-made_up_gia')
  expect(within(row).queryByRole('button')).toBeNull()
})

test('an empty list says where giveaways come from', async () => {
  mount([])
  expect(await screen.findByText('No giveaways to assign')).toBeInTheDocument()
})
