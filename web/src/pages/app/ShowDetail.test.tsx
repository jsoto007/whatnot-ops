import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router'
import { AuthContext, type AuthState } from '../../auth/context'
import type { ChatMessage, Sale, SaleMode, Show } from '../../lib/api'
import { CHAT_SEARCH_MS, ShowDetail } from './ShowDetail'

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
  giftCount: 0,
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
        { seq: 30, username: 'jess_pulls', body: 'can I get the Mega bundle instead? $60', capturedAt: '2026-10-25T01:38:35Z', role: 'buyer', after: true, namesLot: false },
        { seq: 31, username: 'host_shop', body: 'jess_pulls sure, swapping you to Mega', capturedAt: '2026-10-25T01:39:05Z', role: 'host', after: true, namesLot: false },
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

test('Chat search asks the server once per pause in typing, and keeps the last results up meanwhile', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  try {
    const line = (id: number, body: string): ChatMessage => ({ id, seq: id, username: 'u', body, rawText: 'u ' + body, capturedAt: '2026-10-25T01:38:05Z' })
    const reply = (messages: ChatMessage[]) => new Response(JSON.stringify({ total: messages.length, messages }), { status: 200 })
    const asked: string[] = []
    let answer: (r: Response) => void = () => {}
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        if (!url.includes('/messages')) return Promise.resolve(new Response('{}', { status: 404 }))
        const q = new URL(url, 'http://ops.test').searchParams.get('q') ?? ''
        asked.push(q)
        if (!q) return Promise.resolve(reply([line(1, 'hello from the show')]))
        return new Promise<Response>((r) => (answer = r)) // held until the test answers
      }),
    )
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <AuthContext.Provider value={auth}>
          <MemoryRouter initialEntries={['/app/shows/3?tab=messages']}>
            <Routes>
              <Route path="/app/shows/:id" element={<ShowDetail />} />
            </Routes>
          </MemoryRouter>
        </AuthContext.Provider>
      </QueryClientProvider>,
    )
    await act(() => vi.advanceTimersByTimeAsync(10))
    expect(screen.getByText('hello from the show')).toBeInTheDocument()
    expect(asked).toEqual([''])

    // Nine keystrokes, faster than the pause: nothing sent yet.
    const box = screen.getByLabelText('Search messages')
    let typed = ''
    for (const ch of 'jsmith_88') {
      typed += ch
      fireEvent.change(box, { target: { value: typed } })
      await act(() => vi.advanceTimersByTimeAsync(100))
    }
    expect(asked).toEqual([''])
    await act(() => vi.advanceTimersByTimeAsync(CHAT_SEARCH_MS))
    expect(asked).toEqual(['', 'jsmith_88'])
    expect(qc.getQueryCache().findAll({ queryKey: ['messages'] })).toHaveLength(2)
    // While the answer is on its way the last results stay up, not an empty list.
    expect(screen.getByText('hello from the show')).toBeInTheDocument()

    answer(reply([line(2, 'jsmith_88 I will take it')]))
    await act(() => vi.advanceTimersByTimeAsync(10))
    expect(screen.getByText('jsmith_88 I will take it')).toBeInTheDocument()
    expect(screen.queryByText('hello from the show')).toBeNull()
  } finally {
    vi.useRealTimers()
  }
})

test('Parse now says so when the show is already being parsed (409)', async () => {
  const busy = 'This show is being parsed right now. Try again in a few seconds.'
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/parse') && init?.method === 'POST') return new Response(JSON.stringify({ error: busy }), { status: 409 })
      if (url.endsWith('/sales')) return new Response(JSON.stringify({ sales: [] }), { status: 200 })
      return new Response('{}', { status: url.endsWith('/csrf') ? 200 : 404 })
    }),
  )
  const manager: AuthState = { ...auth, can: (p) => p === 'shows.view' || p === 'review.act' }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <AuthContext.Provider value={manager}>
        <MemoryRouter initialEntries={['/app/shows/3']}>
          <Routes>
            <Route path="/app/shows/:id" element={<ShowDetail />} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
  fireEvent.click(await screen.findByTestId('parse-now'))
  expect(await screen.findByTestId('parse-error')).toHaveTextContent(busy)
})

test('a manager lays out the shelves; the page lists them by spot', async () => {
  const saved: unknown[] = []
  const layout = [{ spot: 'B4', skuId: 4, code: 'HOLO', name: 'Holo slab Charizard' }]
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/auth/csrf') return new Response(JSON.stringify({ token: 't' }), { status: 200 })
      if (url.endsWith('/shelves') && init?.method === 'POST') {
        saved.push(JSON.parse(String(init.body)))
        return new Response(JSON.stringify({ shelves: layout }), { status: 200 })
      }
      if (url.endsWith('/shelves')) return new Response(JSON.stringify({ shelves: [] }), { status: 200 })
      if (url === '/api/shows/3') {
        const show = { id: 3, key: 'k', date: '2026-11-12', startedAt: '2026-11-13T05:29:00Z', updatedAt: '2026-11-13T06:29:00Z', sourceUrl: null, messageCount: 9, title: null, recorded: true }
        return new Response(JSON.stringify({ show, stats: {} }), { status: 200 })
      }
      return new Response('{}', { status: 404 })
    }),
  )
  const manager = { ...auth, can: () => true }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <AuthContext.Provider value={manager}>
        <MemoryRouter initialEntries={['/app/shows/3?tab=shelves']}>
          <Routes>
            <Route path="/app/shows/:id" element={<ShowDetail />} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
  expect(await screen.findByText('No shelves laid out for this show.')).toBeInTheDocument()
  // The header reads in the shop's zone, like every other time on the page (live show, 2026-11-13).
  expect(await screen.findByText(/^Started 11:29\sPM/)).toBeInTheDocument()
  fireEvent.change(screen.getByRole('textbox', { name: 'Shelf spots' }), { target: { value: ' b4  HOLO \n\n' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save shelves' }))
  expect(await screen.findByText('Holo slab Charizard')).toBeInTheDocument()
  expect(saved).toEqual([{ shelves: [{ spot: 'b4', code: 'HOLO' }] }])
  expect(screen.getByRole('textbox', { name: 'Shelf spots' })).toHaveValue('B4 HOLO')
})

test('the shelves form waits for the saved layout: a failed read offers no save that would wipe it', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => (url.endsWith('/shelves') ? new Response(JSON.stringify({ error: 'Something went wrong.' }), { status: 500 }) : new Response('{}', { status: 404 }))),
  )
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <AuthContext.Provider value={{ ...auth, can: () => true }}>
        <MemoryRouter initialEntries={['/app/shows/3?tab=shelves']}>
          <Routes>
            <Route path="/app/shows/:id" element={<ShowDetail />} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
  expect(await screen.findByText('Something went wrong.')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Save shelves' })).toBeNull()
})

/** The show page for a show that says how it sells; the server remembers each choice. */
function mountShow(show: Partial<Show>, opts: { can?: (p: string) => boolean; usual?: SaleMode; path?: string } = {}) {
  const usual = opts.usual ?? 'winners_pick'
  const posts: unknown[] = []
  let current = { id: 3, key: 'k', date: '2026-11-12', startedAt: '2026-11-13T05:29:00Z', updatedAt: '2026-11-13T06:29:00Z', sourceUrl: null, messageCount: 9, title: null, recorded: true, ...show }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/auth/csrf') return new Response(JSON.stringify({ token: 't' }), { status: 200 })
      if (url === '/api/shows/3/mode' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as { mode: SaleMode | null }
        posts.push(body)
        current = { ...current, saleMode: body.mode ?? usual, saleModeSet: body.mode !== null }
        return new Response(JSON.stringify({ show: current }), { status: 200 })
      }
      if (url === '/api/shows/3') return new Response(JSON.stringify({ show: current, stats: {} }), { status: 200 })
      if (url.endsWith('/shelves')) return new Response(JSON.stringify({ shelves: [] }), { status: 200 })
      if (url.endsWith('/sales')) return new Response(JSON.stringify({ sales: [] }), { status: 200 })
      return new Response('{}', { status: 404 })
    }),
  )
  const who: AuthState = { ...auth, me: { ...auth.me!, organization: { ...auth.me!.organization, saleMode: usual } }, can: opts.can ?? (() => true) }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <AuthContext.Provider value={who}>
        <MemoryRouter initialEntries={[opts.path ?? '/app/shows/3']}>
          <Routes>
            <Route path="/app/shows/:id" element={<ShowDetail />} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
  return posts
}

test('a manager chooses how one show sells, and can send it back to the shop’s usual way', async () => {
  const posts = mountShow({ saleMode: 'winners_pick', saleModeSet: false })
  const select = await screen.findByRole('combobox', { name: 'Sold as' })
  expect(select).toHaveValue('usual')
  expect(within(select).getByRole('option', { name: 'The shop’s usual way: Winners pick in chat' })).toBeInTheDocument()
  expect(screen.getByTestId('show-mode')).toHaveTextContent('The winner types a shelf spot in chat, like A5')
  expect(screen.getByRole('tab', { name: 'Shelves' })).toBeInTheDocument()

  fireEvent.change(select, { target: { value: 'listing' } })
  await waitFor(() => expect(posts).toEqual([{ mode: 'listing' }]))
  await waitFor(() => expect(screen.getByRole('combobox', { name: 'Sold as' })).toHaveValue('listing'))
  expect(screen.getByTestId('show-mode')).toHaveTextContent('Each listing is one product.')
  // Shelf spots are how a winner picks in chat: this show has none.
  expect(screen.queryByRole('tab', { name: 'Shelves' })).toBeNull()

  fireEvent.change(screen.getByRole('combobox', { name: 'Sold as' }), { target: { value: 'usual' } })
  await waitFor(() => expect(posts).toEqual([{ mode: 'listing' }, { mode: null }]))
  await waitFor(() => expect(screen.getByRole('combobox', { name: 'Sold as' })).toHaveValue('usual'))
  expect(screen.getByRole('tab', { name: 'Shelves' })).toBeInTheDocument()
})

test('someone who cannot change it reads how the show sells', async () => {
  mountShow({ saleMode: 'listing', saleModeSet: true }, { can: (p) => p === 'shows.view' })
  expect(await screen.findByTestId('show-mode')).toHaveTextContent('Sold as: One product per listing')
  expect(screen.getByTestId('show-mode')).not.toHaveTextContent('usual way')
  expect(screen.queryByRole('combobox', { name: 'Sold as' })).toBeNull()
})

test('a show that follows the shop says so', async () => {
  mountShow({ saleMode: 'listing', saleModeSet: false }, { can: (p) => p === 'shows.view', usual: 'listing' })
  expect(await screen.findByTestId('show-mode')).toHaveTextContent('Sold as: One product per listing (the shop’s usual way)')
})

test('a link to the Shelves tab of a one-product show lands on Sales', async () => {
  mountShow({ saleMode: 'listing', saleModeSet: true }, { path: '/app/shows/3?tab=shelves' })
  await screen.findByTestId('show-mode')
  expect(screen.queryByTestId('shelves')).toBeNull()
  expect(screen.getByRole('tab', { name: 'Sales' })).toHaveAttribute('aria-selected', 'true')
})
