import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { AuthContext, type AuthState } from '../auth/context'
import type { Me } from '../lib/api'
import { Signup } from '../pages/Signup'
import { LicenceBanner } from './Licence'

const baseMe: Me = {
  user: { id: 1, email: 'o@x.test', name: 'Olive Owner', role: 'owner', disabled: false, createdAt: '', lastLoginAt: null },
  organization: { id: 1, name: 'Demo Card Shop', slug: 'demo', hostUsername: null },
  permissions: ['shows.view', 'licence.accept'],
}

function authWith(me: Me | null, extra: Partial<AuthState> = {}): AuthState {
  return {
    me,
    loading: false,
    signupsOpen: true,
    signupAdminOnly: false,
    setupCodeRequired: false,
    can: (p) => !!me && me.permissions.includes(p),
    setMe: vi.fn(),
    logout: async () => {},
    ...extra,
  }
}

function mount(ui: React.ReactNode, auth: AuthState) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <AuthContext.Provider value={auth}>
        <MemoryRouter>{ui}</MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
}

/** fetch for api(): the CSRF token, then `reply` for the write; every write is recorded. */
function stubFetch(reply: (body: unknown) => Response) {
  const writes: { url: string; body: unknown; csrf: string | undefined }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/auth/csrf') return new Response(JSON.stringify({ token: 't'.repeat(43) }), { status: 200 })
      const body = init?.body ? JSON.parse(String(init.body)) : undefined
      writes.push({ url, body, csrf: (init?.headers as Record<string, string>)['X-CSRF-Token'] })
      return reply(body)
    }),
  )
  return writes
}

afterEach(() => vi.unstubAllGlobals())

// ---------- the owner's banner ----------

test('says nothing when the server does not ask this person (staff, or already accepted)', () => {
  const { container } = mount(<LicenceBanner />, authWith({ ...baseMe, licence: { version: '1.0', needed: false } }))
  expect(container).toBeEmptyDOMElement()
})

test('says nothing to a server that does not send the licence status yet', () => {
  const { container } = mount(<LicenceBanner />, authWith(baseMe))
  expect(container).toBeEmptyDOMElement()
})

test('asks the owner, links the terms, and accepting the version shown records it', async () => {
  const accepted: Me = { ...baseMe, licence: { version: '1.0', needed: false } }
  const writes = stubFetch(() => new Response(JSON.stringify(accepted), { status: 200 }))
  const auth = authWith({ ...baseMe, licence: { version: '1.0', needed: true } })
  mount(<LicenceBanner />, auth)
  const banner = screen.getByTestId('licence-banner')
  expect(banner).toHaveTextContent('Please read and accept the Sales Ops License and Data Terms.')
  expect(banner).toHaveTextContent('Version 1.0')
  const read = screen.getByRole('link', { name: 'Read the terms' })
  expect(read).toHaveAttribute('href', '/LICENSE.md')
  expect(read).toHaveAttribute('target', '_blank')
  expect(read).toHaveAttribute('rel', 'noopener noreferrer')

  fireEvent.click(screen.getByRole('button', { name: 'I have read and accept the Sales Ops License and Data Terms' }))
  await waitFor(() => expect(auth.setMe).toHaveBeenCalledWith(accepted))
  expect(writes).toEqual([{ url: '/api/auth/licence', body: { version: '1.0' }, csrf: 't'.repeat(43) }])
})

test("shows the server's words when accepting fails, and keeps asking", async () => {
  stubFetch(() => new Response('{"error":"these terms have been updated; reload the page to read them"}', { status: 409 }))
  const auth = authWith({ ...baseMe, licence: { version: '1.0', needed: true } })
  mount(<LicenceBanner />, auth)
  fireEvent.click(screen.getByTestId('accept-licence'))
  expect(await screen.findByRole('alert')).toHaveTextContent('these terms have been updated; reload the page to read them')
  expect(auth.setMe).not.toHaveBeenCalled()
  expect(screen.getByTestId('licence-banner')).toBeInTheDocument()
})

// ---------- sign-up ----------

test('sign-up has the licence box, unticked and required, linking the terms', () => {
  mount(<Signup />, authWith(null))
  const box = screen.getByRole('checkbox', { name: 'I have read and accept the Sales Ops License and Data Terms' })
  expect(box).not.toBeChecked()
  expect(box).toBeRequired()
  const link = screen.getByRole('link', { name: 'Sales Ops License and Data Terms' })
  expect(link).toHaveAttribute('href', '/LICENSE.md')
  expect(link).toHaveAttribute('target', '_blank')
})

test('sign-up sends the tick with the form', async () => {
  const writes = stubFetch(() => new Response(JSON.stringify({ ...baseMe, licence: { version: '1.0', needed: false } }), { status: 201 }))
  const auth = authWith(null)
  mount(<Signup />, auth)
  fireEvent.change(screen.getByLabelText('Shop name'), { target: { value: 'My Shop' } })
  fireEvent.change(screen.getByLabelText('Your name'), { target: { value: 'Olive' } })
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'o@shop.test' } })
  fireEvent.change(screen.getByLabelText('Password (10+ characters)'), { target: { value: 'correct-horse-battery' } })
  fireEvent.click(screen.getByRole('checkbox', { name: /accept the Sales Ops License/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Create shop' }))
  await waitFor(() => expect(auth.setMe).toHaveBeenCalled())
  expect(writes).toHaveLength(1)
  expect(writes[0]!.url).toBe('/api/auth/signup')
  expect(writes[0]!.body).toMatchObject({ organization: 'My Shop', email: 'o@shop.test', acceptLicence: true })
})
