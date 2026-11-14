import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, test, vi } from 'vitest'
import type { LaptopDataPreview } from '../lib/api'
import { DeleteLaptopData } from './DeleteLaptopData'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const preview = (over: Partial<LaptopDataPreview> = {}): LaptopDataPreview => ({ messages: 5840, sales: 201, standing: 176, inPackage: 1, moved: 0, orders: 125, reviews: 213, keptFromWhatnot: 0, blockers: [], ...over })

function dialog(p: LaptopDataPreview, onDone = vi.fn()) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/auth/csrf') return json({ token: 't' })
    if (init?.method === 'DELETE') return json({ showId: null, date: '2026-11-07', messages: p.messages, sales: p.sales, cancelled: p.standing, inPackage: p.inPackage, moved: p.moved, orders: p.orders, reviews: p.reviews, kept: false })
    return json(p)
  })
  vi.stubGlobal('fetch', fetch)
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <DeleteLaptopData showId="602" label="Saturday, November 7" onClose={() => {}} onDone={onDone} />
    </QueryClientProvider>,
  )
  return { fetch, onDone }
}

afterEach(() => vi.unstubAllGlobals())

test('says what goes and what stays, and deletes only once "delete" is typed', async () => {
  const { fetch, onDone } = dialog(preview())
  const box = await screen.findByTestId('laptop-data-preview')
  expect(box.textContent).toContain('5840 chat lines')
  expect(box.textContent).toContain('201 sales made from the chat; the 176 still standing are closed and their stock goes back on the shelf')
  expect(box.textContent).toContain('1 sale was already picked, packed or shipped: that stock is not counted back')
  expect(box.textContent).toContain('125 orders and 213 questions in Review')
  expect(box.textContent).toContain('Kept: everything from Whatnot itself')
  const button = screen.getByTestId('laptop-data-delete')
  expect(button).toBeDisabled()
  fireEvent.change(screen.getByTestId('laptop-data-confirm'), { target: { value: 'delet' } })
  expect(button).toBeDisabled()
  fireEvent.change(screen.getByTestId('laptop-data-confirm'), { target: { value: 'Delete ' } })
  expect(button).toBeEnabled()
  fireEvent.click(button)
  await waitFor(() => expect(onDone).toHaveBeenCalledWith(expect.objectContaining({ showId: null, sales: 201 })))
  const del = fetch.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'DELETE')!
  expect(del[0]).toBe('/api/shows/602/laptop-data')
  expect(JSON.parse(String((del[1] as RequestInit).body))).toEqual({ confirm: 'delete' })
})

test('a reason it cannot be done now is shown, and nothing can be deleted', async () => {
  dialog(preview({ blockers: ['A laptop sent chat for this show in the last 10 minutes, so it may still be recording.'] }))
  expect(await screen.findByTestId('laptop-data-blocker')).toHaveTextContent('may still be recording')
  expect(screen.queryByTestId('laptop-data-confirm')).toBeNull()
  expect(screen.getByTestId('laptop-data-delete')).toBeDisabled()
})

test("sales Whatnot vouches for are kept, and wins the other laptop also saw move", async () => {
  dialog(preview({ keptFromWhatnot: 3, inPackage: 0, moved: 2 }))
  const box = await screen.findByTestId('laptop-data-preview')
  expect(box.textContent).toContain("3 sales Whatnot's orders made or vouched for (the show stays for them)")
  expect(box.textContent).toContain("2 sales move to the other laptop's recording of this show, which saw the same win. Nothing changes in stock.")
  expect(screen.getByTestId('laptop-data-by-hand')).toHaveTextContent('must have its product named by hand')
  expect(box.textContent).not.toContain('already picked')
})
