import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Home } from './Home'

function renderHome() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <Home />
    </QueryClientProvider>,
  )
}

test('shows the API health once it loads', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify({ status: 'ok', db: true }), { status: 200 })),
  )
  renderHome()
  expect(await screen.findByTestId('api-status')).toHaveTextContent('ok (db ok)')
})

test('shows unreachable when the API fails', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 503 })))
  renderHome()
  expect(await screen.findByText('unreachable')).toBeInTheDocument()
})
