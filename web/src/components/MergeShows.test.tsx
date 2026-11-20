import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, test, vi } from 'vitest'
import type { ShowMergePreview } from '../lib/api'
import { MergeShows } from './MergeShows'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const preview = (over: Partial<ShowMergePreview> = {}): ShowMergePreview => ({ keeper: 3863, folded: [3862], laptops: [], sales: 86, orders: 44, joined: 8, reviews: 97, lots: 0, blockers: [], ...over })

function dialog(p: ShowMergePreview, onDone = vi.fn()) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/auth/csrf') return json({ token: 't' })
    if (init?.method === 'POST') return json({ showId: 3863, shows: 1, sales: p.sales, orders: p.orders, joined: p.joined })
    return json(p)
  })
  vi.stubGlobal('fetch', fetch)
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MergeShows showId="3862" label="Sunday, November 15" onClose={() => {}} onDone={onDone} />
    </QueryClientProvider>,
  )
  return { fetch, onDone }
}

afterEach(() => vi.unstubAllGlobals())

test('says what moves into the laptop recording, and merges on confirm', async () => {
  const { fetch, onDone } = dialog(preview())
  const box = await screen.findByTestId('show-merge-preview')
  expect(box.textContent).toContain('86 sales and 44 orders')
  expect(box.textContent).toContain("8 buyers' two orders become one each: one slip, one box")
  expect(box.textContent).toContain('97 questions in Review')
  expect(box.textContent).toContain('Nothing changes in stock')
  expect(box.textContent).not.toMatch(/made|keeper|folded/)
  fireEvent.click(screen.getByTestId('show-merge-confirm'))
  await waitFor(() => expect(onDone).toHaveBeenCalledWith(expect.objectContaining({ showId: 3863, sales: 86 })))
  const post = fetch.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'POST')!
  expect(post[0]).toBe('/api/shows/3862/merge')
  expect(JSON.parse(String((post[1] as RequestInit).body))).toEqual({ confirm: 'merge' })
})

test('a box being packed stops it, and says what to do', async () => {
  dialog(preview({ blockers: ['1 buyer has a box being picked, packed or shipped in one of these shows and more items in the other (kat.c).'] }))
  expect(await screen.findByTestId('show-merge-blocker')).toHaveTextContent('box being picked')
  expect(screen.getByTestId('show-merge-confirm')).toBeDisabled()
})

test('two laptop recordings are not merged', async () => {
  dialog(preview({ folded: [], laptops: [3864] }))
  expect(await screen.findByTestId('show-merge-preview')).toHaveTextContent('recorded by a laptop, so they stay apart')
  expect(screen.getByTestId('show-merge-confirm')).toBeDisabled()
})
