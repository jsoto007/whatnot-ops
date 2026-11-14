import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AuthContext, type AuthState } from '../../auth/context'
import type { Organization } from '../../lib/api'
import { HowYouSell } from './Settings'

const org: Organization = { id: 1, name: 'Demo Card Shop', slug: 'demo', hostUsername: 'host_shop', saleMode: 'winners_pick' }

function mount() {
  const patches: unknown[] = []
  const set: Organization[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/auth/csrf') return new Response(JSON.stringify({ token: 't' }), { status: 200 })
      if (url === '/api/team/organization' && init?.method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as { saleMode: Organization['saleMode'] }
        patches.push(body)
        return new Response(JSON.stringify({ organization: { ...org, saleMode: body.saleMode } }), { status: 200 })
      }
      return new Response('{}', { status: 404 })
    }),
  )
  const auth: AuthState = {
    me: { user: { id: 1, email: 'o@x.test', name: 'Olive Owner', role: 'owner', disabled: false, createdAt: '', lastLoginAt: null }, organization: org, permissions: ['settings.manage'] },
    loading: false,
    signupsOpen: false,
    signupAdminOnly: false,
    setupCodeRequired: false,
    can: () => true,
    setMe: (me) => {
      if (me) set.push(me.organization)
    },
    logout: async () => {},
  }
  render(
    <AuthContext.Provider value={auth}>
      <HowYouSell />
    </AuthContext.Provider>,
  )
  return { patches, set }
}

afterEach(() => vi.unstubAllGlobals())

test('the owner sets how the shop usually sells', async () => {
  const { patches, set } = mount()
  const pick = screen.getByRole('radio', { name: /Winners pick in chat/ })
  const listing = screen.getByRole('radio', { name: /One product per listing/ })
  expect(pick).toBeChecked()
  expect(listing).not.toBeChecked()
  expect(screen.getByText(/A show that is sold differently can be changed on its own page/)).toBeInTheDocument()

  fireEvent.click(listing)
  fireEvent.click(screen.getByRole('button', { name: 'Save how you sell' }))
  await waitFor(() => expect(patches).toEqual([{ saleMode: 'listing' }]))
  // The rest of the app reads the shop from here: the show page's "usual way" follows at once.
  await waitFor(() => expect(set.map((o) => o.saleMode)).toEqual(['listing']))
})
