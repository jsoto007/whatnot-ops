import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { Field, Form } from '../components/Form'
import { LicenceLink } from '../components/Licence'
import { api, type Me } from '../lib/api'
import { LICENCE_ACCEPT_LEAD } from '../lib/labels'

export function Signup() {
  const { setMe, signupsOpen, signupAdminOnly, setupCodeRequired, loading } = useAuth()
  const [setupCode, setSetupCode] = useState('')
  const nav = useNavigate()
  const [organization, setOrganization] = useState('')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [acceptLicence, setAcceptLicence] = useState(false)
  if (!loading && !signupsOpen) {
    return (
      <section className="card auth-card" data-testid="signup-closed">
        <h2>This shop is already set up</h2>
        <p className="muted">Ask the shop's owner to invite you from the Team page. The invite email has a link to create your account.</p>
        <p className="muted">
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
      </section>
    )
  }
  return (
    <section className="card auth-card">
      <h2>Create your shop</h2>
      <p className="muted">
        {signupAdminOnly
          ? "Use the shop's admin email (the one set on the server). You become the owner and can invite the team."
          : 'You become the owner and can invite the team.'}
      </p>
      <Form
        submitLabel="Create shop"
        onSubmit={async () => {
          setMe(await api<Me>('POST', '/api/auth/signup', { organization, name, email, password, acceptLicence, ...(setupCodeRequired ? { setupCode } : {}) }))
          nav('/app', { replace: true })
        }}
      >
        <Field label="Shop name">
          <input value={organization} onChange={(e) => setOrganization(e.target.value)} required />
        </Field>
        <Field label="Your name">
          <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" required />
        </Field>
        <Field label="Email">
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
        </Field>
        {setupCodeRequired && (
          <Field label="Setup code">
            <input value={setupCode} onChange={(e) => setSetupCode(e.target.value)} autoComplete="off" spellCheck={false} required />
          </Field>
        )}
        <Field label="Password (10+ characters)">
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" minLength={10} required />
        </Field>
        {/* Clickwrap (LICENSE.md §2.2): never ticked for them; the server refuses without it. */}
        <label className="field check licence-check">
          <input type="checkbox" checked={acceptLicence} onChange={(e) => setAcceptLicence(e.target.checked)} required />
          <span>
            {LICENCE_ACCEPT_LEAD} <LicenceLink />
          </span>
        </label>
      </Form>
      <p className="muted">
        Already have an account? <Link to="/login">Sign in</Link>
      </p>
    </section>
  )
}
