import { useQuery } from '@tanstack/react-query'
import { getHealth } from '../lib/api'

export function Home() {
  const health = useQuery({ queryKey: ['health'], queryFn: getHealth, retry: false })
  return (
    <section className="card" aria-labelledby="home-title">
      <h2 id="home-title">Phase 0 scaffold</h2>
      <p>
        API:{' '}
        {health.isPending ? (
          <span>checking…</span>
        ) : health.isError ? (
          <span className="status-bad">unreachable</span>
        ) : (
          <span className={health.data.db ? 'status-ok' : 'status-bad'} data-testid="api-status">
            {health.data.status} (db {health.data.db ? 'ok' : 'down'})
          </span>
        )}
      </p>
    </section>
  )
}
