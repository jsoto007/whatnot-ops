import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router'
import { AuthContext, type AuthState } from '../../auth/context'
import type { Sale } from '../../lib/api'
import { ShowDetail } from './ShowDetail'

const auth: AuthState = {
  me: {
    user: { id: 1, email: 'v@x.test', name: 'Vera Viewer', role: 'viewer', disabled: false, createdAt: '', lastLoginAt: null },
    organization: { id: 1, name: 'Demo Card Shop', slug: 'demo', hostUsername: 'host_shop' },
    permissions: ['shows.view'],
  },
  loading: false,
  signupsOpen: false,
  signupAdminOnly: false,
  setupCodeRequired: false,
  can: (p) => auth.me!.permissions.includes(p),
  setMe: () => {},
  logout: async () => {},
}

const sale = (over: Partial<Sale>): Sale => ({
  id: 1,
  showId: 3,
  showDate: '2026-10-24',
  buyer: 'jess_pulls',
  sku: { id: 4, code: 'HOLO', name: '', productName: 'Holo slab' },
  item: 'Holo slab',
  quantity: 1,
  priceCents: 1500,
  auctionType: '',
  status: 'reserved',
  statusReason: null,
  flagged: false,
  orderId: null,
  externalOrderId: null,
  paidAt: null,
  seq: 29,
  rawText: 'jess_pulls won the auction for Holo slab for $15.00',
  source: 'player',
  wonAt: '2026-10-25T01:38:05Z',
  chat: [],
  createdAt: '2026-10-25T01:38:06Z',
  ...over,
})

function mount(sales: Sale[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => (url.endsWith('/sales') ? new Response(JSON.stringify({ sales }), { status: 200 }) : new Response('{}', { status: 404 }))),
  )
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <AuthContext.Provider value={auth}>
        <MemoryRouter initialEntries={['/app/shows/3']}>
          <Routes>
            <Route path="/app/shows/:id" element={<ShowDetail />} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
}

test('a sale shows when it was won, in Central time, and opens what the winner said', async () => {
  mount([
    sale({
      chat: [
        { seq: 30, username: 'jess_pulls', body: 'can I get the Mega bundle instead? $60', capturedAt: '2026-10-25T01:38:35Z', role: 'buyer' },
        { seq: 31, username: 'host_shop', body: 'jess_pulls sure, swapping you to Mega', capturedAt: '2026-10-25T01:39:05Z', role: 'host' },
      ],
    }),
    sale({ id: 2, buyer: 'kat.c', seq: 40, wonAt: null, chat: [] }),
  ])
  expect(await screen.findByTestId('won-at-1')).toHaveTextContent('8:38:05 PM')
  expect(screen.getByTestId('won-at-2')).toHaveTextContent('—')
  expect(screen.queryByTestId('chat-toggle-2')).toBeNull()

  const toggle = screen.getByTestId('chat-toggle-1')
  expect(toggle).toHaveTextContent('2 lines')
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
  fireEvent.click(toggle)
  const panel = screen.getByTestId('sale-chat-1')
  expect(panel).toHaveTextContent('Recorded: Holo slab at $15.00')
  expect(within(panel).getByText('can I get the Mega bundle instead? $60')).toBeInTheDocument()
  expect(panel).toHaveTextContent('host_shop · host')
  expect(panel.querySelector('.chat-line.hit')).toHaveTextContent('jess_pulls · winner')

  fireEvent.click(toggle)
  expect(screen.queryByTestId('sale-chat-1')).toBeNull()
})
