import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '../../auth/useAuth'
import { Field, Form } from '../../components/Form'
import { api, apiGet, type AiStatus, type AuditEntry, type Organization } from '../../lib/api'
import { Laptops } from './settings/Laptops'

export function Settings() {
  const { me, can } = useAuth()
  return (
    <section>
      <h2>Settings</h2>
      <p className="muted">{me?.organization.name}</p>
      {can('settings.manage') && <HostUsername />}

      <Laptops manage={can('devices.manage')} />

      {can('review.act') && <Ai />}
      {can('audit.view') && <Audit />}
    </section>
  )
}

function HostUsername() {
  const { me, setMe } = useAuth()
  const [value, setValue] = useState(me?.organization.hostUsername ?? '')
  return (
    <Form
      className="inline-form"
      submitLabel="Save"
      onSubmit={async () => {
        const r = await api<{ organization: Organization }>('PATCH', '/api/team/organization', { hostUsername: value })
        if (me) setMe({ ...me, organization: r.organization })
      }}
    >
      <Field label="Your Whatnot username (the host)">
        <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="myshoplive" data-testid="host-username" />
      </Field>
      <span className="muted small">Chat lines from this account count as confirmations ("sold to…", "cancelled…").</span>
    </Form>
  )
}

function Ai() {
  const q = useQuery({ queryKey: ['ai-status'], queryFn: () => apiGet<AiStatus>('/api/review/ai-status') })
  if (!q.data) return null
  const s = q.data
  return (
    <>
      <h3>AI supervisor</h3>
      <p className="muted small" data-testid="ai-status">
        {s.enabled ? (
          <>
            Provider <b>{s.provider}</b> · {s.callsToday} calls today{s.failedToday ? `, ${s.failedToday} failed` : ''} · {s.openProposals} open suggestions
            {s.lastFailedAt && <span className="form-error"> · last failed {new Date(s.lastFailedAt).toLocaleString()}</span>}
          </>
        ) : (
          <>Off. AI suggestions and show checks are not turned on for this shop.</>
        )}
      </p>
    </>
  )
}

function Audit() {
  const q = useQuery({ queryKey: ['audit'], queryFn: () => apiGet<{ entries: AuditEntry[] }>('/api/audit?limit=100') })
  return (
    <>
      <h3>Activity</h3>
      {q.data && (
        <table className="table small">
          <thead>
            <tr>
              <th>When</th>
              <th>Who</th>
              <th>What happened</th>
            </tr>
          </thead>
          <tbody>
            {q.data.entries.map((e) => (
              <tr key={e.id}>
                <td>{new Date(e.at).toLocaleString()}</td>
                <td>{e.who}</td>
                <td>{e.summary}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}
