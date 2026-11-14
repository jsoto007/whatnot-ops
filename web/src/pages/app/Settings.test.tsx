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
  await waitFor(() => expect(patches).toEqual([{ saleMode: 'listing', autoPick: false, trustedMods: [] }]))
  // The rest of the app reads the shop from here: the show page's "usual way" follows at once.
  await waitFor(() => expect(set.map((o) => o.saleMode)).toEqual(['listing']))
})

test("the owner switches on assigning each winner's pick by itself", async () => {
  const { patches } = mount()
  const box = screen.getByTestId('auto-pick')
  expect(box).not.toBeChecked() // off until the owner has compared a real show
  expect(screen.getByText(/when Whatnot has confirmed the win as paid/)).toBeInTheDocument()
  fireEvent.click(box)
  fireEvent.click(screen.getByRole('button', { name: 'Save how you sell' }))
  await waitFor(() => expect(patches).toEqual([{ saleMode: 'winners_pick', autoPick: true, trustedMods: [] }]))
})

test('the owner lists the moderators whose chat lines may set an offer', async () => {
  const { patches } = mount()
  fireEvent.change(screen.getByTestId('trusted-mods'), { target: { value: 'mod_one,  other.mod' } })
  expect(screen.getByText(/A viewer's line never does/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Save how you sell' }))
  await waitFor(() => expect(patches).toEqual([{ saleMode: 'winners_pick', autoPick: false, trustedMods: ['mod_one', 'other.mod'] }]))
})
