import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { Field, Form } from '../components/Form'
import { ApiError, api, apiGet, type Me } from '../lib/api'

export function AcceptInvite() {
  const { token = '' } = useParams()
  const { setMe } = useAuth()
  const nav = useNavigate()
  const info = useQuery({
    queryKey: ['invite', token],
    queryFn: () => apiGet<{ email: string; role: string; organization: string }>('/api/auth/invites/' + token),
    retry: false,
  })
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  if (info.isPending) return <p className="muted">Checking your invite…</p>
  if (info.isError) {
    return <section className="card auth-card"><h2>Invite not valid</h2><p>{info.error instanceof ApiError ? info.error.message : 'Try again.'}</p></section>
  }
  return (
    <section className="card auth-card">
      <h2>Join {info.data.organization}</h2>
      <p className="muted">
        {info.data.email} · role: <b>{info.data.role}</b>
      </p>
      <Form
        submitLabel="Create account"
        onSubmit={async () => {
          setMe(await api<Me>('POST', '/api/auth/invites/accept', { token, name, password }))
          nav('/app', { replace: true })
        }}
      >
        <Field label="Your name">
          <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" required />
        </Field>
        <Field label="Password (10+ characters)">
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" minLength={10} required />
        </Field>
      </Form>
    </section>
  )
}
