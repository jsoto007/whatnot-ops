import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { Field, Form } from '../components/Form'
import { api, type Me } from '../lib/api'

export function Forgot() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState<{ resetUrl?: string } | null>(null)
  return (
    <section className="card auth-card">
      <h2>Reset your password</h2>
      {sent ? (
        <p>
          If that address has an account, a reset link is on its way.
          {sent.resetUrl && (
            <>
              {' '}
              <a href={sent.resetUrl} data-testid="dev-reset-link">
                (dev: open the link)
              </a>
            </>
          )}
        </p>
      ) : (
        <Form submitLabel="Send reset link" onSubmit={async () => setSent(await api('POST', '/api/auth/password/forgot', { email }))}>
          <Field label="Email">
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
          </Field>
        </Form>
      )}
      <p className="muted">
        <Link to="/login">Back to sign in</Link>
      </p>
    </section>
  )
}

export function Reset() {
  const { token = '' } = useParams()
  const { setMe } = useAuth()
  const nav = useNavigate()
  const [password, setPassword] = useState('')
  return (
    <section className="card auth-card">
      <h2>Choose a new password</h2>
      <Form
        submitLabel="Save password"
        onSubmit={async () => {
          setMe(await api<Me>('POST', '/api/auth/password/reset', { token, password }))
          nav('/app', { replace: true })
        }}
      >
        <Field label="New password (10+ characters)">
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" minLength={10} required />
        </Field>
      </Form>
    </section>
  )
}
