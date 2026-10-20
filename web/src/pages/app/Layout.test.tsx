import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router'
import { AuthContext, type AuthState } from '../../auth/context'
import { AppLayout } from './Layout'

const auth: AuthState = {
  me: {
    user: { id: 1, email: 'o@x.test', name: 'Olive Owner', role: 'owner', disabled: false, createdAt: '', lastLoginAt: null },
    organization: { id: 1, name: 'Demo Card Shop', slug: 'demo', hostUsername: null },
    permissions: ['shows.view', 'orders.view', 'review.act', 'inventory.view', 'reports.view', 'team.view', 'devices.manage'],
  },
  loading: false,
  signupsOpen: false,
  signupAdminOnly: false,
  can: (p) => auth.me!.permissions.includes(p),
  setMe: () => {},
  logout: async () => {},
}

function mount(path = '/app', counts = { openReviews: 0, toFulfil: 0, live: false }) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(counts), { status: 200 })))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
    <AuthContext.Provider value={auth}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/app" element={<AppLayout />}>
            <Route index element={<p>dash</p>} />
            <Route path="orders" element={<p>orders page</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>
    </QueryClientProvider>,
  )
}

beforeEach(() => localStorage.clear())

test('shows every section the user may see, grouped, with icons', () => {
  mount()
  const nav = screen.getByRole('navigation', { name: 'Sections' })
  expect(nav).toHaveTextContent('Operate')
  expect(nav).toHaveTextContent('Catalog')
  expect(nav).toHaveTextContent('Admin')
  expect(nav.querySelectorAll('a').length).toBe(9)
  expect(nav.querySelectorAll('svg').length).toBe(9)
})

test('collapse toggle compacts the sidebar and is remembered', () => {
  const { container } = mount()
  fireEvent.click(screen.getByTestId('collapse-sidebar'))
  expect(container.querySelector('.app')).toHaveClass('collapsed')
  expect(localStorage.getItem('wn.sidebar')).toBe('collapsed')
  expect(screen.getByRole('link', { name: 'Orders' })).toHaveAttribute('title', 'Orders')
  fireEvent.click(screen.getByTestId('collapse-sidebar'))
  expect(container.querySelector('.app')).not.toHaveClass('collapsed')
})

test('starts compact when that was saved', () => {
  localStorage.setItem('wn.sidebar', 'collapsed')
  const { container } = mount()
  expect(container.querySelector('.app')).toHaveClass('collapsed')
})

test('mobile drawer opens from the top bar and closes after navigating or on Escape', () => {
  const { container } = mount()
  const sidebar = container.querySelector('.sidebar')!
  fireEvent.click(screen.getByTestId('open-menu'))
  expect(sidebar).toHaveClass('open')
  expect(document.body).toHaveClass('drawer-open')
  fireEvent.click(screen.getByRole('link', { name: 'Orders' }))
  expect(screen.getByText('orders page')).toBeInTheDocument()
  expect(sidebar).not.toHaveClass('open')
  fireEvent.click(screen.getByTestId('open-menu'))
  fireEvent.keyDown(window, { key: 'Escape' })
  expect(sidebar).not.toHaveClass('open')
  expect(document.body).not.toHaveClass('drawer-open')
})

test('badges: live show on Shows, counts on Orders and Review', async () => {
  mount('/app', { openReviews: 6, toFulfil: 7, live: true })
  expect(await screen.findByText('Live')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /Orders/ })).toHaveTextContent('Orders7')
  expect(screen.getByRole('link', { name: /Review/ })).toHaveTextContent('Review6')
})
