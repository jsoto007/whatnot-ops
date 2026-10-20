import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router'
import { AuthProvider } from './AuthProvider'
import { useAuth } from './useAuth'
import { RequireAuth } from './guards'

const me = {
  user: { id: 1, email: 'o@x.test', name: 'O', role: 'manager', disabled: false, createdAt: '', lastLoginAt: null },
  organization: { id: 1, name: 'Shop', slug: 'shop' },
  permissions: ['shows.view', 'team.view'],
}

function Probe() {
  const { me, can } = useAuth()
  return <p>{me ? `${me.user.name}:${can('team.view')}:${can('team.manage')}` : 'anon'}</p>
}

function mount(path = '/app') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<p>login page</p>} />
            <Route path="/app" element={<RequireAuth />}>
              <Route index element={<Probe />} />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('signed-in user sees the page and can() follows the permission list', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(me), { status: 200 })))
  mount()
  expect(await screen.findByText('O:true:false')).toBeInTheDocument()
})

test('anonymous user is sent to the login page', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: 'not signed in', signupsOpen: true }), { status: 401 })))
  mount()
  expect(await screen.findByText('login page')).toBeInTheDocument()
})
