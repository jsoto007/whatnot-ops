import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { AuthContext, type AuthState } from '../../auth/context'
import type { WhatnotCatalog, WhatnotListing } from '../../lib/api'
import { WhatnotListings } from './WhatnotListings'

const listing = (over: Partial<WhatnotListing>): WhatnotListing => ({
  id: 1,
  title: 'Red bundle',
  options: '',
  skuCode: null,
  priceCents: null,
  onWhatnot: null,
  url: null,
  link: null,
  sku: null,
  listings: 0,
  offered: null,
  differs: false,
  ...over,
})
const catalog: WhatnotCatalog = {
  connected: true,
  readAt: new Date().toISOString(),
  notAllowed: false,
  listings: [
    listing({ id: 1, title: 'Red bundle', options: 'Large', skuCode: 'red', priceCents: 2500, onWhatnot: 7, url: 'https://www.whatnot.com/dashboard/inventory/1', link: 'code', sku: { id: 12, code: 'RED', name: 'Red bundle', canSell: 3 }, listings: 1, offered: 7, differs: true }),
    listing({ id: 2, title: 'CHOOSE ANY PRODUCT', onWhatnot: 500 }),
    listing({ id: 3, title: 'Old sticker', skuCode: 'STK', onWhatnot: 4, link: 'off' }),
    listing({ id: 4, title: 'Blue', skuCode: 'BLUE', onWhatnot: 8, link: 'person', sku: { id: 13, code: 'BLUE', name: 'Blue', canSell: 0 }, listings: 2, offered: 16, differs: true }),
  ],
  counts: { all: 4, linked: 2, undecided: 1, differs: 1 },
}
const products = { products: [{ id: 1, name: 'Holo slab', notes: null, active: true, skus: [{ id: 44, code: 'CHAR', name: 'Charizard', priceCents: null, weightOz: null, onHand: 1, reserved: 0, available: 1, short: 0, lowStockAt: 0, low: false, active: true, aliases: [] }] }] }

const auth = (permissions: string[]): AuthState => ({ me: null, loading: false, can: (p: string) => permissions.includes(p), setMe: () => {}, logout: async () => {} }) as unknown as AuthState
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

function mount(data: WhatnotCatalog, permissions = ['inventory.view', 'inventory.write']) {
  const calls: { url: string; method: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      if (method !== 'GET') calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined })
      if (url.endsWith('/api/auth/csrf')) return json({ token: 't' })
      if (url.endsWith('/api/products')) return json(products)
      return json(data)
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AuthContext.Provider value={auth(permissions)}>
        <MemoryRouter>
          <WhatnotListings />
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

test('each listing beside its product: both counts, how it was linked, and why a count is not set, in words', async () => {
  mount(catalog)
  const red = await screen.findByTestId('listing-red')
  expect(red.textContent).toContain('Red bundleLarge · Whatnot SKU red · $25.00')
  expect(red.textContent).toContain('7Red bundle REDmatched by its code3differs')
  expect(screen.getByRole('link', { name: 'Red bundle' })).toHaveAttribute('href', 'https://www.whatnot.com/dashboard/inventory/1')
  expect(screen.getByTestId('listing-CHOOSE ANY PRODUCT').textContent).toContain('500Not linked yet')
  expect(screen.getByTestId('listing-STK').textContent).toContain('Not one of ours')
  const blue = screen.getByTestId('listing-BLUE').textContent ?? ''
  expect(blue).toContain('linked by the team · one of 2 listings for this product, 16 offered in all')
  expect(blue).toContain('0differs') // oversold here: the shop can sell none, Whatnot offers 16
  expect(screen.getByTestId('catalog-state').textContent).toContain('It never changes anything on Whatnot')
  expect(document.body.textContent).not.toMatch(/undecided|canSell|offered:/)
  // The chips count and filter.
  expect(screen.getByRole('button', { name: /Not linked yet/ }).textContent).toBe('Not linked yet 1')
  fireEvent.click(screen.getByRole('button', { name: /Counts differ/ }))
  expect(screen.getByTestId('listings').querySelectorAll('tbody tr')).toHaveLength(2)
})

test('linking a listing to a product, saying it is none of ours, and reading again', async () => {
  const calls = mount(catalog)
  const row = await screen.findByTestId('listing-CHOOSE ANY PRODUCT')
  fireEvent.click(row.querySelector('button')!) // "Link to a product"
  const box = await screen.findByRole('combobox', { name: 'Product for CHOOSE ANY PRODUCT' })
  fireEvent.focus(box)
  fireEvent.change(box, { target: { value: 'chari' } })
  await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(1))
  fireEvent.click(screen.getByRole('option'))
  await waitFor(() => expect(calls).toContainEqual({ url: '/api/whatnot/catalog/2/link', method: 'PATCH', body: { skuId: 44 } }))
  fireEvent.click(screen.getByTestId('listing-red').querySelectorAll('button')[1]!) // "Unlink"
  await waitFor(() => expect(calls).toContainEqual({ url: '/api/whatnot/catalog/1/link', method: 'PATCH', body: { skuId: null } }))
  fireEvent.click(screen.getByRole('button', { name: 'Read from Whatnot now' }))
  await waitFor(() => expect(calls.map((c) => c.url)).toContain('/api/whatnot/catalog/read'))
})

test('a viewer reads it and changes nothing', async () => {
  mount(catalog, ['inventory.view'])
  await screen.findByTestId('listing-red')
  expect(screen.queryByRole('button', { name: 'Read from Whatnot now' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Link to a product' })).toBeNull()
})

test.each([
  [{ ...catalog, connected: false, listings: [] }, 'Not connected to Whatnot'],
  [{ ...catalog, notAllowed: true, listings: [] }, 'The access token may not read the shop’s Whatnot catalog'],
  [{ ...catalog, readAt: null, listings: [] }, 'Not read from Whatnot yet'],
] as [WhatnotCatalog, string][])('state %#', async (data, words) => {
  mount(data)
  expect(await screen.findByText(new RegExp(words))).toBeInTheDocument()
})
