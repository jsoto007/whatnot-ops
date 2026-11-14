import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Icon } from '../../../components/Icon'
import { ApiError, api, apiGet, type WhatnotStatus } from '../../../lib/api'
import { fmtWhen } from '../../../lib/format'
import { whatnotProblemLabel, whatnotSyncLabel } from '../../../lib/labels'

/** Settings → Whatnot: link the shop to its Whatnot seller account and see that it is working.
 * The access token itself is never typed here or shown: the developer sets it on the server. */
export function WhatnotConnection({ manage }: { manage: boolean }) {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['whatnot'], queryFn: () => apiGet<WhatnotStatus>('/api/whatnot') })
  const [copied, setCopied] = useState(false)
  const act = useMutation({
    mutationFn: (what: 'connect' | 'disconnect' | 'check') => api<WhatnotStatus>('POST', `/api/whatnot/${what}`),
    onSuccess: (s) => {
      qc.setQueryData(['whatnot'], s)
      void qc.invalidateQueries({ queryKey: ['nav-counts'] })
    },
  })
  const mode = useMutation({
    mutationFn: (ordersMode: 'shadow' | 'auto') => api<WhatnotStatus>('PATCH', '/api/whatnot/settings', { ordersMode }),
    onSuccess: (s) => qc.setQueryData(['whatnot'], s),
  })
  const s = q.data
  if (!s) return null
  const failed = act.error ?? mode.error
  const error = failed instanceof ApiError ? failed.message : failed ? 'That did not work. Try again.' : null
  const url = s.webhooks.url

  return (
    <section className="card whatnot-conn" aria-labelledby="whatnot-title" data-testid="whatnot-connection">
      <div className="dcard-head">
        <h2 id="whatnot-title">Whatnot</h2>
        <span className="muted small">Orders, payments and shipments come straight from Whatnot once the shop is connected.</span>
      </div>

      {!s.configured ? (
        <p className="muted" data-testid="whatnot-state">
          Not set up yet. Your developer adds the shop's Whatnot access token to the server first; then the owner connects it here.
        </p>
      ) : !s.connected ? (
        <>
          <p data-testid="whatnot-state">Not connected.{s.username ? ` It was connected as @${s.username} before.` : ''}</p>
          {manage ? (
            <button type="button" className="btn primary" disabled={act.isPending} onClick={() => act.mutate('connect')} data-testid="whatnot-connect">
              Connect to Whatnot
            </button>
          ) : (
            <p className="muted small">The shop's owner can connect it.</p>
          )}
        </>
      ) : (
        <>
          <p data-testid="whatnot-state">
            {s.problem ? (
              <span className="form-error">
                <Icon name="help" size={16} /> {whatnotProblemLabel(s.problem)}
              </span>
            ) : (
              <>
                Connected as <b>@{s.username}</b>
              </>
            )}
            {s.lastCheckedAt && <span className="muted small"> · checked {fmtWhen(s.lastCheckedAt)}</span>}
          </p>
          <div className="btns">
            <button type="button" className="btn" disabled={act.isPending} onClick={() => act.mutate('check')} data-testid="whatnot-check">
              Check now
            </button>
            {manage && (
              <button
                type="button"
                className="btn ghost"
                disabled={act.isPending}
                onClick={() => {
                  if (window.confirm('Disconnect from Whatnot? Orders and shipments stop arriving on their own until you connect again.')) act.mutate('disconnect')
                }}
                data-testid="whatnot-disconnect"
              >
                Disconnect
              </button>
            )}
          </div>

          <h3>Orders</h3>
          <p className="muted small">Whatnot's orders say what was sold, to whom, and whether it was paid.</p>
          <div className="radio-list" role="radiogroup" aria-label="How Whatnot's orders are used" data-testid="whatnot-mode">
            {(
              [
                ['shadow', 'Compare only', "Each show's Payments tab shows what Whatnot's orders would change. A person presses Apply."],
                ['auto', 'Apply as they arrive', 'A paid order the app missed becomes a sale, a failed or cancelled one closes its sale and returns the stock. A declined card seen on the stream is flagged in Review and left to its order: many are paid a minute later.'],
              ] as const
            ).map(([value, label, hint]) => (
              <label key={value} className="radio-row">
                <input type="radio" name="whatnot-mode" value={value} checked={s.ordersMode === value} disabled={!manage || mode.isPending} onChange={() => mode.mutate(value)} />
                <span>
                  <b>{label}</b>
                  <span className="muted small"> {hint}</span>
                </span>
              </label>
            ))}
          </div>
          {!manage && <p className="muted small">The shop's owner can change this.</p>}

          <h3>Live updates</h3>
          {!s.webhooks.configured ? (
            <p className="muted small" data-testid="whatnot-webhooks">
              Not set up yet. Until your developer adds the signing secret, the app asks Whatnot for news on a timer instead of hearing it at once.
            </p>
          ) : (
            <p className="small" data-testid="whatnot-webhooks">
              {s.webhooks.lastEventAt ? <>Last update from Whatnot {fmtWhen(s.webhooks.lastEventAt)}.</> : <>Nothing received from Whatnot yet.</>}
              {s.webhooks.waiting ? ` ${s.webhooks.waiting} waiting to be read.` : ''}
            </p>
          )}
          {url && manage && (
            <p className="muted small">
              In Seller Hub → Seller API → Webhooks, this is the address to add:{' '}
              <code data-testid="whatnot-webhook-url">{url}</code>{' '}
              <button
                type="button"
                className="btn ghost sm"
                onClick={() => {
                  void navigator.clipboard?.writeText(url).then(() => setCopied(true))
                }}
              >
                {copied ? 'Copied' : 'Copy'}
              </button>
            </p>
          )}

          {s.sync.length > 0 && (
            <table className="table small" data-testid="whatnot-sync">
              <thead>
                <tr>
                  <th>What</th>
                  <th>Last read from Whatnot</th>
                </tr>
              </thead>
              <tbody>
                {s.sync.map((r) => (
                  <tr key={r.resource}>
                    <td>{whatnotSyncLabel(r.resource)}</td>
                    <td>
                      {r.lastOkAt ? fmtWhen(r.lastOkAt) : 'not yet'}
                      {r.failing && <span className="form-error"> · the last try did not work</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
      {error && <p className="form-error small">{error}</p>}
    </section>
  )
}

/** One line on every page when the connection needs a person (a rejected token, a missing permission). */
export function WhatnotBanner({ show }: { show: boolean }) {
  if (!show) return null
  return (
    <section className="banner" role="alert" data-testid="whatnot-banner">
      <div className="banner-main">
        <span className="banner-icon" aria-hidden>
          <Icon name="help" size={20} />
        </span>
        <div>
          <b>The Whatnot connection stopped working.</b>
          <div className="small">Orders and shipments are not arriving on their own. The account owner needs to make a new access token; Settings has the details.</div>
        </div>
      </div>
    </section>
  )
}
