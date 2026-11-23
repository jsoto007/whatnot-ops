import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AuthContext, type AuthState } from '../../auth/context'
import type { Me } from '../../lib/api'
import { Team } from './Team'

const me: Me = {
  user: { id: 1, email: 'owner@shop.test', name: 'Olive Owner', role: 'owner', disabled: false, createdAt: '', lastLoginAt: null },
  organization: { id: 1, name: 'Demo Card Shop', slug: 'demo', hostUsername: null },
  permissions: ['team.view', 'team.invite', 'team.manage'],
}

function mount(inviteDelivery: 'email' | 'link' | 'none', onInvite?: () => Response) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/auth/csrf') return new Response(JSON.stringify({ token: 't' }), { status: 200 })
      if (url === '/api/team' && (init?.method ?? 'GET') === 'GET') {
        const team = { users: [me.user], invites: [], invitableRoles: ['viewer'], inviteDelivery }
        return new Response(JSON.stringify(team), { status: 200 })
      }
      if (url === '/api/team/invites' && init?.method === 'POST' && onInvite) return onInvite()
      return new Response('{}', { status: 404 })
    }),
  )
  const auth: AuthState = {
    me,
    loading: false,
    signupsOpen: false,
    signupAdminOnly: false,
    setupCodeRequired: false,
    can: (p) => me.permissions.includes(p),
    setMe: vi.fn(),
    logout: async () => {},
  }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <AuthContext.Provider value={auth}>
        <Team />
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

test('with no way to reach anyone, the page says so instead of offering the invite form', async () => {
  mount('none')
  expect(await screen.findByTestId('invites-need-email')).toHaveTextContent('email isn’t set up')
  expect(screen.queryByRole('button', { name: 'Send invite' })).toBeNull()
})

test('a failed invite email shows the server’s plain words', async () => {
  const message = 'the invite email could not be sent, so nothing was saved; try again in a moment'
  mount('email', () => new Response(JSON.stringify({ error: message }), { status: 502 }))
  fireEvent.change(await screen.findByLabelText('Email'), { target: { value: 'mia@shop.test' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send invite' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(message)
  expect(screen.queryByTestId('invite-result')).toBeNull()
})
