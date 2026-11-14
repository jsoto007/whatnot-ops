import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App } from './App'
import { ApiError } from './lib/api'
import './styles/global.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A page opened is read again (laptops and Whatnot change the data outside this tab), but
      // coming back to the window only re-reads what is older than 15 s: a host switching
      // between the Whatnot tab and this one does not re-pull every list each time.
      refetchOnWindowFocus: (query) => Date.now() - query.state.dataUpdatedAt > 15_000,
      // Never retry a 4xx (including 429): it will not change. Network / 5xx: at most 2 more tries.
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false
        return failureCount < 2
      },
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
