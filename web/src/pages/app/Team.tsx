import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../../auth/useAuth'
import { Field, Form } from '../../components/Form'
import { api, apiGet, type Invite, type Role, type User } from '../../lib/api'
import { roleLabel } from '../../lib/labels'

interface TeamData {
  users: User[]
  invites: Invite[]
  invitableRoles: Role[]
}

export function Team() {
  const { me, can } = useAuth()
  const qc = useQueryClient()
  const team = useQuery({ queryKey: ['team'], queryFn: () => apiGet<TeamData>('/api/team') })
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<Role>('fulfillment')
  const [lastInvite, setLastInvite] = useState<{ email: string; acceptUrl?: string; emailSent: boolean } | null>(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ['team'] })
  const update = useMutation({
    mutationFn: (v: { id: number; role?: Role; disabled?: boolean }) => api('PATCH', `/api/team/users/${v.id}`, v),
    onSuccess: refresh,
  })
  const revoke = useMutation({ mutationFn: (id: number) => api('DELETE', `/api/team/invites/${id}`), onSuccess: refresh })

  if (team.isPending) return <p className="muted">Loading…</p>
  if (team.isError) return <p className="form-error">Could not load the team.</p>
  const { users, invites, invitableRoles } = team.data
  return (
    <section>
      <h2>Team</h2>
      <table className="table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Email</th>
            <th>Role</th>
            <th>Last sign-in</th>
            {can('team.manage') && <th></th>}
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id} className={u.disabled ? 'disabled' : ''}>
              <td>
                {u.name}
                {u.isAdmin && (
                  <span className="badge live" title="The shop's protected owner: always an owner, cannot be changed here">
                    admin
                  </span>
                )}
              </td>
              <td>{u.email}</td>
              <td>
                {can('team.manage') && u.id !== me?.user.id && !u.isAdmin ? (
                  <select value={u.role} aria-label={`Role for ${u.email}`} onChange={(e) => update.mutate({ id: u.id, role: e.target.value as Role })}>
                    {(['owner', 'manager', 'fulfillment', 'viewer'] as Role[]).map((r) => (
                      <option key={r} value={r}>
                        {roleLabel(r)}
                      </option>
                    ))}
                  </select>
                ) : (
                  roleLabel(u.role)
                )}
              </td>
              <td>{u.lastLoginAt ? new Date(u.lastLoginAt).toLocaleString() : '—'}</td>
              {can('team.manage') && (
                <td>
                  {u.id !== me?.user.id && !u.isAdmin && (
                    <button className="btn small" onClick={() => update.mutate({ id: u.id, disabled: !u.disabled })}>
                      {u.disabled ? 'Enable' : 'Disable'}
                    </button>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {update.isError && <p className="form-error">{(update.error as Error).message}</p>}

      {can('team.invite') && (
        <>
          <h3>Invite someone</h3>
          <Form
            className="inline-form"
            submitLabel="Send invite"
            onSubmit={async () => {
              const r = await api<{ acceptUrl?: string; emailSent: boolean }>('POST', '/api/team/invites', { email, role })
              setLastInvite({ email, acceptUrl: r.acceptUrl, emailSent: r.emailSent })
              setEmail('')
              await refresh()
            }}
          >
            <Field label="Email">
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </Field>
            <Field label="Role">
              <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
                {invitableRoles.map((r) => (
                  <option key={r} value={r}>
                    {roleLabel(r)}
                  </option>
                ))}
              </select>
            </Field>
          </Form>
          {lastInvite && (
            <p className="card" data-testid="invite-result">
              {lastInvite.emailSent ? `Invite emailed to ${lastInvite.email}.` : `Email is not configured, so nothing was sent to ${lastInvite.email}.`}
              {lastInvite.acceptUrl && (
                <>
                  {' '}
                  Share this link: <code data-testid="accept-url">{lastInvite.acceptUrl}</code>
                </>
              )}
            </p>
          )}
          {invites.length > 0 && (
            <>
              <h3>Pending invites</h3>
              <table className="table">
                <thead>
                  <tr>
                    <th>Email</th>
                    <th>Role</th>
                    <th>Invited by</th>
                    <th>Expires</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {invites.map((i) => (
                    <tr key={i.id}>
                      <td>{i.email}</td>
                      <td>{roleLabel(i.role)}</td>
                      <td>{i.invitedBy ?? '—'}</td>
                      <td>{new Date(i.expiresAt).toLocaleDateString()}</td>
                      <td>
                        <button className="btn small" onClick={() => revoke.mutate(i.id)}>
                          Revoke
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
    </section>
  )
}
