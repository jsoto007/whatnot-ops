import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { AuthContext, type AuthState } from '../../auth/context'
import { BuyerFlagChip } from '../../components/BuyerFlag'
import type { BuyerList, BuyerProfile, Order } from '../../lib/api'
import { BuyerPage, Buyers } from './Buyers'

const figures = { items: 0, paidCents: 0, shows: 0, firstShow: null, lastShow: null, cancelled: 0, failed: 0 }
const list: BuyerList = {
  buyers: [
    { ...figures, username: 'kat_new', names: ['kat.c'], flag: 'watch', hasNote: true, items: 3, paidCents: 2500, shows: 2, firstShow: '2026-10-20', lastShow: '2026-11-03', failed: 2, cancelled: 1 },
    { ...figures, username: 'lani808', names: [], flag: 'vip', hasNote: false, items: 12, paidCents: 123456, shows: 5, lastShow: '2026-11-08' },
    { ...figures, username: 'zoe', names: [], flag: null, hasNote: false },
  ],
  total: 240,
  counts: { all: 240, vip: 1, watch: 1, noted: 1 },
  limit: 100,
  offset: 0,
}
const profile: BuyerProfile = {
  ...figures,
  username: 'kat_new',
  names: ['kat.c'],
  known: true,
  followed: true,
  flag: 'watch',
  note: 'Two payments failed in October.',
  notedBy: 'Olive Owner',
  notedAt: '2026-11-04T15:00:00+00:00',
  items: 3,
  paidCents: 2500,
  shows: 2,
  firstShow: '2026-10-20',
  lastShow: '2026-11-03',
  failed: 2,
  cancelled: 1,
  gifts: 1,
  problems: [{ saleId: 9, showId: 4, showDate: '2026-10-27', item: 'CHOOSE ANY PRODUCT #73', quantity: 2, priceCents: 3000, status: 'payment_failed' }],
}
const order = (id: number, showDate: string, buyer: string): Order => ({ id, showId: id, showDate, buyer, status: 'shipped', verified: true, items: 1, totalCents: 400, notes: null, tracking: null, pickedAt: null, packedAt: null, packedBy: null, shippedAt: null, createdAt: '2026-10-20T00:00:00Z', sales: null, gifts: null })

const auth = (permissions: string[]): AuthState => ({ me: null, loading: false, can: (p: string) => permissions.includes(p), refresh: async () => {}, logout: async () => {} }) as unknown as AuthState

const page = (ui: React.ReactNode, at = '/app/buyers', permissions = ['buyers.view', 'buyers.manage']) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AuthContext.Provider value={auth(permissions)}>
        <MemoryRouter initialEntries={[at]}>
          <Routes>
            <Route path="/app/buyers" element={ui} />
            <Route path="/app/buyers/:name" element={ui} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => vi.unstubAllGlobals())

test('the list: one row per person, in words, with the team flag and the names they used', async () => {
  const fetch = vi.fn(async () => json(list))
  vi.stubGlobal('fetch', fetch)
  page(<Buyers />)
  const kat = await screen.findByTestId('buyer-kat_new')
  expect(kat.textContent).toContain('kat_newWatch listNotealso kat.c')
  expect(kat.textContent).toContain('$25.00')
  expect(kat.textContent).toContain('2 payments failed · 1 cancelled')
  expect(screen.getByTestId('buyer-lani808').textContent).toContain('lani808VIP')
  expect(screen.getByTestId('buyer-lani808').textContent).toContain('$1,234.56')
  expect(screen.getByTestId('buyer-zoe').textContent).toContain('——') // nothing bought, nothing lost
  expect(screen.getByRole('link', { name: 'kat_new' })).toHaveAttribute('href', '/app/buyers/kat_new')
  expect(screen.getByRole('button', { name: /Watch list/ }).textContent).toBe('Watch list 1')
  expect(screen.getByText('1–3 of 240')).toBeInTheDocument()
  expect(document.body.textContent).not.toMatch(/payment_failed|vip\b|hasNote/)

  // Filter, order and page go to the server; the search waits for the typing to pause.
  fireEvent.click(screen.getByRole('button', { name: /Watch list/ }))
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/buyers?sort=spent&flag=watch', expect.anything()))
  fireEvent.change(screen.getByLabelText('Order'), { target: { value: 'problems' } })
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/buyers?sort=problems&flag=watch', expect.anything()))
  fireEvent.change(screen.getByLabelText('Find a buyer'), { target: { value: 'ka' } })
  fireEvent.change(screen.getByLabelText('Find a buyer'), { target: { value: 'kat' } })
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/buyers?sort=problems&q=kat&flag=watch', expect.anything()))
  expect(fetch).not.toHaveBeenCalledWith('/api/buyers?sort=problems&q=ka&flag=watch', expect.anything())
})

test('nobody by that name: the team can flag them anyway', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...list, buyers: [], total: 0, counts: { all: 0, vip: 0, watch: 0, noted: 0 } })))
  page(<Buyers />, '/app/buyers?q=newcomer')
  expect(await screen.findByRole('link', { name: 'Flag newcomer anyway' })).toHaveAttribute('href', '/app/buyers/newcomer')
})

test("a buyer's page: figures, orders under every name, what fell through; saving the flag and note", async () => {
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'PATCH') return json({ buyer: { ...profile, flag: 'vip', note: 'Paid up. Lovely.' } })
    if (String(_url).endsWith('/api/auth/csrf')) return json({ token: 't' })
    return json({ buyer: profile, orders: [order(2, '2026-11-03', 'kat_new'), order(1, '2026-10-20', 'kat.c')] })
  })
  vi.stubGlobal('fetch', fetch)
  page(<BuyerPage />, '/app/buyers/kat.c')
  expect(await screen.findByRole('heading', { name: /kat_new/ })).toHaveTextContent('kat_new Watch list')
  expect(screen.getByText('Has also bought as kat.c.')).toBeInTheDocument()
  const tiles = screen.getByTestId('buyer-figures').textContent ?? ''
  expect(tiles).toContain('$25.00Spent')
  expect(tiles).toContain('3Items')
  expect(tiles).toContain('2Shows · Oct 20 to Nov 3')
  expect(tiles).toContain('1Giveaway won')
  expect(tiles).toContain('32 payments failed · 1 cancelled')
  expect(screen.getByTestId('buyer-orders').textContent).toContain('as kat.c')
  expect(screen.getByTestId('buyer-problems').textContent).toContain('2 × CHOOSE ANY PRODUCT #73$30.00payment declined')
  expect(screen.getByText(/Note last changed by Olive Owner on Nov 4, 2026/)).toBeInTheDocument()

  const save = screen.getByRole('button', { name: 'Save' })
  expect(save).toBeDisabled() // nothing changed yet
  fireEvent.change(screen.getByLabelText('Flag'), { target: { value: 'vip' } })
  fireEvent.change(screen.getByLabelText('Note'), { target: { value: 'Paid up. Lovely.' } })
  fireEvent.click(save)
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith('/api/buyers/by-name/kat.c', expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ flag: 'vip', note: 'Paid up. Lovely.' }) })),
  )
  expect(await screen.findByRole('heading', { name: /kat_new/ })).toHaveTextContent('kat_new VIP')
  expect(await screen.findByText('Saved.')).toBeInTheDocument()
})

test('a viewer reads what the team says and cannot change it; an unknown name can still be flagged', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ buyer: profile, orders: [] })))
  const first = page(<BuyerPage />, '/app/buyers/kat_new', ['buyers.view'])
  expect(await screen.findByText('Two payments failed in October.')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
  expect(screen.queryByLabelText('Note')).toBeNull()
  first.unmount()

  vi.stubGlobal('fetch', vi.fn(async () => json({ buyer: { ...profile, ...figures, username: 'ghost', names: [], known: false, followed: false, flag: null, note: null, notedBy: null, notedAt: null, gifts: 0, problems: [] }, orders: [] })))
  page(<BuyerPage />, '/app/buyers/ghost')
  expect(await screen.findByText(/Nobody has won under this name yet/)).toBeInTheDocument()
  expect(screen.queryByTestId('buyer-figures')).toBeNull()
  expect(screen.getByLabelText('Flag')).toBeInTheDocument()
})

test('the chip beside a name: the flag in words, a note, or nothing; any spelling of the name', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ flags: { 'kat.c': { flag: 'watch', note: true }, lani808: { flag: 'vip', note: false }, zoe: { flag: null, note: true } } })))
  page(
    <>
      <span data-testid="a">
        <BuyerFlagChip name="Kat.C" />
      </span>
      <span data-testid="b">
        <BuyerFlagChip name="lani808" />
      </span>
      <span data-testid="c">
        <BuyerFlagChip name="zoe" />
      </span>
      <span data-testid="d">
        <BuyerFlagChip name="nobody" />
      </span>
      <span data-testid="e">
        <BuyerFlagChip name={null} />
      </span>
    </>,
  )
  await waitFor(() => expect(screen.getByTestId('a').textContent).toBe('Watch list · note'))
  expect(screen.getByTestId('b').textContent).toBe('VIP')
  expect(screen.getByTestId('c').textContent).toBe('Note')
  expect(screen.getByTestId('d').textContent).toBe('')
  expect(screen.getByTestId('e').textContent).toBe('')
  expect(screen.getByRole('link', { name: 'Watch list · note' })).toHaveAttribute('href', '/app/buyers/Kat.C')
})
