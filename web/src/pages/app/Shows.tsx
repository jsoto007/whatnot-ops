import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { apiGet, type Show } from '../../lib/api'

const fmtDate = (iso: string) => new Date(iso + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
const fmtTime = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })

export function Shows() {
  const q = useQuery({ queryKey: ['shows'], queryFn: () => apiGet<{ shows: Show[] }>('/api/shows') })
  return (
    <section>
      <div className="page-head">
        <div>
          <h2>Shows</h2>
          <div className="sub">Every show the extension has captured, newest first</div>
        </div>
      </div>
      {q.isPending ? (
        <p className="muted">Loading…</p>
      ) : q.isError ? (
        <p className="form-error">Could not load shows.</p>
      ) : q.data.shows.length === 0 ? (
        <div className="empty">
          <b>No shows yet</b>
          Start the Chat Scraper extension on a live show and it will appear here.
        </div>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Show</th>
              <th>Started</th>
              <th>Last update</th>
              <th className="num">Messages</th>
            </tr>
          </thead>
          <tbody>
            {q.data.shows.map((s) => (
              <tr key={s.id}>
                <td>
                  <Link to={`/app/shows/${s.id}`}>{fmtDate(s.date)}</Link>
                </td>
                <td>{fmtTime(s.startedAt)}</td>
                <td>{new Date(s.updatedAt).toLocaleString()}</td>
                <td className="num">{s.messageCount}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

