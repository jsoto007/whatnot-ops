import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { AuthContext, type AuthState } from '../../auth/context'
import type { Shipping as Doc, ShippingBuyer, ShippingPackage } from '../../lib/api'
import { Shipping } from './Shipping'

const auth = (permissions: string[]): AuthState => ({
  me: {
    user: { id: 1, email: 'm@x.test', name: 'Mia', role: 'manager', disabled: false, createdAt: '', lastLoginAt: null },
    organization: { id: 1, name: 'Demo', slug: 'demo', hostUsername: 'host_shop' },
    permissions,
  },
  loading: false,
  signupsOpen: false,
  signupAdminOnly: false,
  setupCodeRequired: false,
  can: (p) => permissions.includes(p),
  setMe: () => {},
  logout: async () => {},
})

const pack = (over: Partial<ShippingPackage> = {}): ShippingPackage => ({
  id: 11,
  status: 'created',
  items: 3,
  otherItems: 0,
  tracking: null,
  carrier: null,
  labelReady: false,
  dueAt: null,
  holds: [],
  pickup: false,
  url: 'https://www.whatnot.com/dashboard/shipments/1',
  labelRequestedAt: null,
  labelProblem: null,
  ...over,
})
const buyer = (name: string, state: ShippingBuyer['state'], packages: ShippingPackage[], over: Partial<ShippingBuyer> = {}): ShippingBuyer => ({
  buyer: name,
  items: packages.reduce((n, p) => n + p.items, 0) || 1,
  unplaced: 0,
  packages,
  state,
  orderId: 5,
  orderStatus: 'paid',
  ...over,
})
const doc = (buyers: ShippingBuyer[], over: Partial<Doc> = {}): Doc => {
  const totals: Doc['totals'] = { buyers: buyers.length }
  for (const b of buyers) totals[b.state] = (totals[b.state] ?? 0) + 1
  return { showId: 7, showDate: '2026-11-07', connected: true, matched: true, mode: 'shadow', show: { title: 'Friday night', status: 'ended', ended: true }, buyers, totals, toMarkShipped: 0, ...over }
}

function open(first: Doc, permissions: string[], after?: Doc) {
  const calls: { method: string; url: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/auth/csrf') return new Response(JSON.stringify({ token: 't' }))
      const method = init?.method ?? 'GET'
      calls.push({ method, url, body: init?.body ? JSON.parse(init.body as string) : undefined })
      return new Response(JSON.stringify(method === 'GET' ? first : (after ?? first)), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AuthContext.Provider value={auth(permissions)}>
        <MemoryRouter initialEntries={['/app/orders/shipping/7']}>
          <Routes>
            <Route path="/app/orders/shipping/:showId" element={<Shipping />} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
  return calls
}

const ALL = ['orders.view', 'orders.fulfill', 'orders.labels']
afterEach(() => vi.unstubAllGlobals())

test('one row per buyer, each state in words, and no code on the page', async () => {
  open(
    doc([
      buyer('lani808', 'ready', [pack()]),
      buyer('split_sam', 'split', [pack({ id: 12, items: 1 }), pack({ id: 13, items: 1 })]),
      buyer('held_hana', 'hold', [pack({ id: 14, holds: ['order_pending_cancellation'] })]),
      buyer('done_dee', 'shipped', [pack({ id: 15, status: 'in_transit', tracking: '9400AAA', carrier: 'USPS', labelReady: true })], { orderStatus: 'shipped' }),
      buyer('later_lee', 'none', [], { unplaced: 1, orderId: null, orderStatus: null }),
    ]),
    ALL,
  )
  const table = await screen.findByTestId('shipping-buyers')
  expect(screen.getByTestId('shipping-state').textContent).toBe('Friday night · 5 buyers · 1 ready for a label · 1 with the carrier · 1 to combine · 1 on hold · 1 not packaged yet')
  expect(within(screen.getByTestId('ship-lani808')).getByTestId('ship-state').textContent).toBe('Ready for its label')
  const split = screen.getByTestId('ship-split_sam')
  expect(split.textContent).toContain('Two open packages: combine them in Seller Hub first')
  expect(within(split).getAllByTestId('ship-package')).toHaveLength(2)
  expect(screen.getByTestId('ship-held_hana').textContent).toContain('Do not pack: a cancellation is pending')
  expect(screen.getByTestId('ship-done_dee').textContent).toContain('In transit · USPS 9400AAA')
  expect(screen.getByTestId('ship-later_lee').textContent).toContain('Not in a package on Whatnot yet')
  for (const code of ['order_pending_cancellation', 'in_transit', 'label_generated', 'created']) expect(table.textContent).not.toContain(code)
  // Only the one ready buyer gets a label; never the split one.
  expect(screen.getByTestId('shipping-make-labels').textContent).toBe('Make 1 label')
})

test('making labels asks first, says what it closes, and posts the confirmation', async () => {
  const ready = doc([buyer('lani808', 'ready', [pack()]), buyer('kat.c', 'ready', [pack({ id: 12 })])])
  const calls = open(ready, ALL, { ...ready, requested: 2 })
  const confirm = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true)
  vi.stubGlobal('confirm', confirm)
  fireEvent.click(await screen.findByTestId('shipping-make-labels'))
  expect(confirm.mock.calls[0]![0]).toContain("closes each buyer's bundle")
  expect(confirm.mock.calls[0]![0]).toContain('cannot be undone')
  expect(calls.filter((c) => c.method === 'POST')).toEqual([]) // said no: nothing sent
  fireEvent.click(screen.getByTestId('shipping-make-labels'))
  await waitFor(() => expect(screen.getByTestId('shipping-note').textContent).toContain('Asked Whatnot for 2 labels'))
  expect(calls.find((c) => c.method === 'POST')).toEqual({ method: 'POST', url: '/api/shipping/shows/7/labels', body: { confirm: true } })
})

test('while the show is live labels cannot be made, and the page says why', async () => {
  open(doc([buyer('lani808', 'wait', [pack()])], { show: { title: 'Friday night', status: 'live', ended: false } }), ALL)
  expect((await screen.findByTestId('shipping-live')).textContent).toContain('Labels wait until it does')
  expect((screen.getByTestId('shipping-make-labels') as HTMLButtonElement).disabled).toBe(true)
})

test('a label that is ready prints from Whatnot; a refused one says why', async () => {
  open(
    doc([
      buyer('lani808', 'labelled', [pack({ status: 'label_generated', labelReady: true, tracking: '9400AAA', carrier: 'USPS', dueAt: '2026-11-10T06:00:00Z' })]),
      buyer('kat.c', 'ready', [pack({ id: 12, labelProblem: 'no_permission', labelRequestedAt: new Date().toISOString() })]),
      buyer('newbie', 'ready', [pack({ id: 13, labelRequestedAt: new Date().toISOString() })]),
    ]),
    ALL,
  )
  const row = await screen.findByTestId('ship-lani808')
  expect(within(row).getByTestId('ship-print').getAttribute('href')).toBe('/api/shipping/shipments/11/label.pdf')
  expect(row.textContent).toContain('Ship by')
  expect(screen.getByTestId('ship-kat.c').textContent).toContain('may not make labels yet')
  expect(screen.getByTestId('ship-newbie').textContent).toContain('Whatnot is making it')
  expect(within(screen.getByTestId('ship-kat.c')).queryByTestId('ship-print')).toBeNull()
})

test('a packer prints and marks shipped but cannot make labels; a viewer only looks', async () => {
  const d = doc([buyer('lani808', 'shipped', [pack({ status: 'dropped_off', labelReady: true })])], { toMarkShipped: 1 })
  const calls = open(d, ['orders.view', 'orders.fulfill'], { ...d, toMarkShipped: 0, marked: 1 })
  fireEvent.click(await screen.findByTestId('shipping-mark-shipped'))
  await waitFor(() => expect(screen.getByTestId('shipping-note').textContent).toBe('1 order marked shipped.'))
  expect(calls.some((c) => c.url === '/api/shipping/shows/7/mark-shipped')).toBe(true)
  expect(screen.queryByTestId('shipping-make-labels')).toBeNull()
})

test('a viewer sees packages and no buttons or print links', async () => {
  open(doc([buyer('lani808', 'labelled', [pack({ status: 'label_generated', labelReady: true })])], { toMarkShipped: 1 }), ['orders.view'])
  await screen.findByTestId('shipping-buyers')
  for (const id of ['shipping-refresh', 'shipping-make-labels', 'shipping-mark-shipped', 'ship-print']) expect(screen.queryByTestId(id)).toBeNull()
})

test('not connected, or not matched to a Whatnot show, says what to do', async () => {
  open({ showId: 7, connected: false, matched: false, buyers: [], totals: {} }, ALL)
  expect((await screen.findByTestId('shipping-state')).textContent).toContain('Not connected to Whatnot')
})
