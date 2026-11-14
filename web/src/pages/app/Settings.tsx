import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '../../auth/useAuth'
import { Field, Form } from '../../components/Form'
import { api, apiGet, type AiStatus, type AuditEntry, type Organization, type SaleMode } from '../../lib/api'
import { SALE_MODE_LABEL, saleModeHelp } from '../../lib/labels'
import { Laptops } from './settings/Laptops'
import { WhatnotConnection } from './settings/WhatnotConnection'

export function Settings() {
  const { me, can } = useAuth()
  return (
    <section>
      <h2>Settings</h2>
      <p className="muted">{me?.organization.name}</p>
      {can('settings.manage') && <HostUsername />}
      {can('settings.manage') && <HowYouSell />}

      {can('whatnot.view') && <WhatnotConnection manage={can('whatnot.manage')} />}

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

const SALE_MODES = Object.keys(SALE_MODE_LABEL) as SaleMode[]

/** The shop's usual way of selling. A new show starts this way; each show can be changed on its own page. */
export function HowYouSell() {
  const { me, setMe } = useAuth()
  const [mode, setMode] = useState<SaleMode>(me?.organization.saleMode ?? 'winners_pick')
  return (
    <>
      <h3>How you sell</h3>
      <Form
        className="stack-form"
        submitLabel="Save how you sell"
        onSubmit={async () => {
          const r = await api<{ organization: Organization }>('PATCH', '/api/team/organization', { saleMode: mode })
          if (me) setMe({ ...me, organization: r.organization })
        }}
      >
        <div role="radiogroup" aria-label="How you usually sell" data-testid="sale-mode">
          {SALE_MODES.map((m) => (
            <label className="field check choice" key={m}>
              <input type="radio" name="sale-mode" value={m} checked={mode === m} onChange={() => setMode(m)} />
              <span>
                <b>{SALE_MODE_LABEL[m]}</b> <span className="muted">{saleModeHelp(m)}</span>
              </span>
            </label>
          ))}
        </div>
        <span className="muted small">New shows start this way. A show that is sold differently can be changed on its own page.</span>
      </Form>
    </>
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
