import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router'
import { apiGet, type ChatMessage, type Show } from '../../lib/api'

const fmtDate = (iso: string) => new Date(iso + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
const fmtTime = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })

export function Shows() {
  const q = useQuery({ queryKey: ['shows'], queryFn: () => apiGet<{ shows: Show[] }>('/api/shows') })
  return (
    <section>
      <h2>Shows</h2>
      {q.isPending ? (
        <p className="muted">Loading…</p>
      ) : q.isError ? (
        <p className="form-error">Could not load shows.</p>
      ) : q.data.shows.length === 0 ? (
        <p className="card muted">No shows yet. Start the Chat Scraper extension on a live show and it will appear here.</p>
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

export function ShowDetail() {
  const { id = '' } = useParams()
  const [q, setQ] = useState('')
  const show = useQuery({ queryKey: ['show', id], queryFn: () => apiGet<{ show: Show }>(`/api/shows/${id}`) })
  const msgs = useQuery({
    queryKey: ['messages', id, q],
    queryFn: () => apiGet<{ total: number; messages: ChatMessage[] }>(`/api/shows/${id}/messages?limit=500&q=${encodeURIComponent(q)}`),
  })
  return (
    <section>
      <p>
        <Link to="/app/shows">← Shows</Link>
      </p>
      <h2>{show.data ? fmtDate(show.data.show.date) + ' show' : 'Show'}</h2>
      <div className="toolbar">
        <input type="search" placeholder="Buyer name or word" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search messages" />
        <span className="muted">{msgs.data ? `${msgs.data.total} message${msgs.data.total === 1 ? '' : 's'}` : ''}</span>
      </div>
      {msgs.isPending ? (
        <p className="muted">Loading…</p>
      ) : msgs.isError ? (
        <p className="form-error">Could not load messages.</p>
      ) : (
        <div className="chat">
          {msgs.data.messages.map((m) => (
            <div className="chat-line" key={m.id}>
              <span className="t">{new Date(m.capturedAt).toLocaleTimeString()}</span>
              <span className="u">{m.username}</span>
              <span>{m.body}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
