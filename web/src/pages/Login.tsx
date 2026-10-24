import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { Field, Form } from '../components/Form'
import { api, type Me } from '../lib/api'

export function Login() {
  const { setMe } = useAuth()
  const nav = useNavigate()
  const loc = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  return (
    <section className="card auth-card">
      <h2>Sign in</h2>
      <Form
        submitLabel="Sign in"
        onSubmit={async () => {
          setMe(await api<Me>('POST', '/api/auth/login', { email, password }))
          nav((loc.state as { from?: string } | null)?.from ?? '/app', { replace: true })
        }}
      >
        <Field label="Email">
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
        </Field>
        <Field label="Password">
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
        </Field>
      </Form>
      <p className="muted">
        <Link to="/forgot">Forgot your password?</Link>
      </p>
    </section>
  )
}
