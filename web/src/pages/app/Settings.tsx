import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../../auth/useAuth'
import { Field, Form } from '../../components/Form'
import { api, apiGet, type AuditEntry, type Device } from '../../lib/api'

export function Settings() {
  const { me, can } = useAuth()
  const qc = useQueryClient()
  const devices = useQuery({ queryKey: ['devices'], queryFn: () => apiGet<{ devices: Device[] }>('/api/devices') })
  const [name, setName] = useState('')
  const [newKey, setNewKey] = useState<{ name: string; key: string } | null>(null)
  const revoke = useMutation({
    mutationFn: (id: number) => api('DELETE', `/api/devices/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['devices'] }),
  })
  return (
    <section>
      <h2>Settings</h2>
      <p className="muted">
        {me?.organization.name} · <code>{me?.organization.slug}</code>
      </p>

      <h3>Laptops (extension device keys)</h3>
      <p className="muted small">Each laptop running Chat Scraper needs its own key. Keys are shown once.</p>
      {devices.data && devices.data.devices.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Key</th>
              <th>Last seen</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {devices.data.devices.map((d) => (
              <tr key={d.id} className={d.revoked ? 'disabled' : ''}>
                <td>{d.name}</td>
                <td>
                  <code>{d.keyPrefix}…</code>
                </td>
                <td>{d.lastSeenAt ? new Date(d.lastSeenAt).toLocaleString() : 'never'}</td>
                <td>
                  {d.revoked ? (
                    'revoked'
                  ) : (
                    <button className="btn small" onClick={() => revoke.mutate(d.id)}>
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Form
        className="inline-form"
        submitLabel="Create key"
        onSubmit={async () => {
          const r = await api<{ device: Device; key: string }>('POST', '/api/devices', { name })
          setNewKey({ name: r.device.name, key: r.key })
          setName('')
          await qc.invalidateQueries({ queryKey: ['devices'] })
        }}
      >
        <Field label="Laptop name">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Front laptop" required />
        </Field>
      </Form>
      {newKey && (
        <p className="card" data-testid="new-key">
          Key for <b>{newKey.name}</b> (copy it now, it will not be shown again): <code>{newKey.key}</code>
        </p>
      )}

      {can('audit.view') && <Audit />}
    </section>
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
              <th>Action</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {q.data.entries.map((e) => (
              <tr key={e.id}>
                <td>{new Date(e.at).toLocaleString()}</td>
                <td>{e.actor || e.actorType}</td>
                <td>
                  <code>{e.action}</code>
                </td>
                <td>{e.details ? JSON.stringify(e.details) : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}
