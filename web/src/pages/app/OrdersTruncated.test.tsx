import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { AuthContext, type AuthState } from '../../auth/context'
import type { Order } from '../../lib/api'
import { Orders } from './Orders'

const auth: AuthState = {
  me: {
    user: { id: 1, email: 'm@x.test', name: 'Mia', role: 'manager', disabled: false, createdAt: '', lastLoginAt: null },
    organization: { id: 1, name: 'Demo Card Shop', slug: 'demo', hostUsername: 'host_shop' },
    permissions: ['orders.view'],
  },
  loading: false,
  signupsOpen: false,
  signupAdminOnly: false,
  setupCodeRequired: false,
  can: (p) => p === 'orders.view',
  setMe: () => {},
  logout: async () => {},
}

const ORDER: Order = {
  id: 1,
  buyer: 'moonlove3',
  showId: 3,
  showDate: '2026-11-07',
  status: 'paid',
  verified: true,
  items: 0,
  totalCents: 0,
  notes: null,
  tracking: null,
  pickedAt: null,
  packedAt: null,
  packedBy: null,
  shippedAt: null,
  createdAt: '2026-11-08T03:12:00Z',
  gifts: null,
  sales: [],
}

function show(body: { orders: Order[]; truncated: boolean }) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => new Response(JSON.stringify(url.startsWith('/api/orders') ? body : { shows: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AuthContext.Provider value={auth}>
        <MemoryRouter initialEntries={['/app/orders']}>
          <Orders />
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

test('says so when the list was cut, and how to see the rest', async () => {
  show({ orders: [ORDER], truncated: true })
  const note = await screen.findByTestId('orders-truncated')
  expect(note.textContent).toContain('newest 1 orders')
  expect(note.textContent).toContain('pick a show')
})

test('says nothing when every order fits', async () => {
  show({ orders: [ORDER], truncated: false })
  await screen.findByTestId('order-moonlove3')
  expect(screen.queryByTestId('orders-truncated')).toBeNull()
})
