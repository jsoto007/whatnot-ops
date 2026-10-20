import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Icon } from '../../../components/Icon'
import { Sheet } from '../../../components/Sheet'
import { ApiError, api, apiGet, type Device } from '../../../lib/api'
import { fmtWhen } from '../../../lib/format'

interface ExtensionInfo {
  available: boolean
  version: string | null
  serverUrl: string
}
interface Pairing {
  pairingId: number
  name: string
  code: string
  expiresAt: string
}
interface PairingStatus {
  status: 'waiting' | 'connected' | 'expired'
  expiresAt: string
  device: Device | null
}

const ONLINE_MS = 3 * 60_000

/** Settings → Laptops: install the extension, connect each laptop with a code, see who is uploading. */
export function Laptops({ manage }: { manage: boolean }) {
  const qc = useQueryClient()
  const ext = useQuery({ queryKey: ['extension'], queryFn: () => apiGet<ExtensionInfo>('/api/extension') })
  const devices = useQuery({ queryKey: ['devices'], queryFn: () => apiGet<{ devices: Device[] }>('/api/devices'), enabled: manage })
  const [connecting, setConnecting] = useState(false)
  const revoke = useMutation({
    mutationFn: (id: number) => api('DELETE', `/api/devices/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['devices'] }),
  })
  const list = (devices.data?.devices ?? []).slice().sort((a, b) => Number(a.revoked) - Number(b.revoked) || (b.lastSeenAt ?? '').localeCompare(a.lastSeenAt ?? ''))

  return (
    <section className="card laptops" aria-labelledby="laptops-title">
      <div className="dcard-head">
        <h2 id="laptops-title">Laptops</h2>
        <span className="muted small">Chat Scraper captures the live chat on each laptop and uploads it here.</span>
      </div>

      <ol className="setup-steps">
        <li>
          <span className="step-n" aria-hidden="true">
            1
          </span>
          <div className="step-main">
            <b>Install the extension in Chrome</b>
            <p className="muted small">
              Download and unzip it. Open <code>chrome://extensions</code>, turn on <b>Developer mode</b>, click <b>Load unpacked</b> and pick the unzipped folder. It is already set to this web app.
            </p>
          </div>
          {ext.data?.available ? (
            <a className="btn" href="/api/extension/download" download data-testid="download-extension">
              <Icon name="download" size={16} /> Download v{ext.data.version}
            </a>
          ) : (
            <span className="muted small" data-testid="extension-missing">
              {ext.isPending ? 'Checking…' : 'Not built on this server yet'}
            </span>
          )}
        </li>
        <li>
          <span className="step-n" aria-hidden="true">
            2
          </span>
          <div className="step-main">
            <b>Connect it to the shop</b>
            <p className="muted small">Get a one-time code here and type it in the extension's Settings (toolbar icon → Settings).</p>
          </div>
          {manage ? (
            <button type="button" className="btn primary" onClick={() => setConnecting(true)}>
              <Icon name="plus" size={16} /> Connect a laptop
            </button>
          ) : (
            <span className="muted small">Ask an owner or manager for a code</span>
          )}
        </li>
      </ol>

      {manage && list.length > 0 && (
        <div className="table-scroll">
          <table className="table laptop-table" data-testid="devices">
            <thead>
              <tr>
                <th>Laptop</th>
                <th>Status</th>
                <th className="hide-sm">Key</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {list.map((d) => {
                const online = !d.revoked && d.lastSeenAt && Date.now() - Date.parse(d.lastSeenAt) < ONLINE_MS
                return (
                  <tr key={d.id} className={d.revoked ? 'disabled' : ''}>
                    <td>
                      <b>{d.name}</b>
                      <div className="muted small">added {fmtWhen(d.createdAt)}</div>
                    </td>
                    <td>
                      {d.revoked ? (
                        <span className="muted">Disconnected</span>
                      ) : (
                        <span className={'presence' + (online ? ' on' : '')}>{online ? 'Uploading now' : d.lastSeenAt ? `Seen ${fmtWhen(d.lastSeenAt)}` : 'Waiting for its first upload'}</span>
                      )}
                    </td>
                    <td className="hide-sm">
                      <code>{d.keyPrefix}…</code>
                    </td>
                    <td className="num">
                      {!d.revoked && (
                        <button
                          type="button"
                          className="btn small ghost"
                          onClick={() => {
                            if (confirm(`Disconnect ${d.name}? It stops uploading until it is connected again.`)) revoke.mutate(d.id)
                          }}
                        >
                          Disconnect
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {manage && <ManualKey />}
      {connecting && <ConnectLaptop onClose={() => (setConnecting(false), void qc.invalidateQueries({ queryKey: ['devices'] }))} />}
    </section>
  )
}

/** The pairing sheet: name the laptop, show the code, wait for the extension to use it. */
function ConnectLaptop({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [pairing, setPairing] = useState<Pairing | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const status = useQuery({
    queryKey: ['pairing', pairing?.pairingId],
    queryFn: () => apiGet<PairingStatus>(`/api/devices/pairings/${pairing!.pairingId}`),
    enabled: !!pairing,
    refetchInterval: (q) => (q.state.data?.status === 'waiting' || !q.state.data ? 2000 : false),
  })
  useEffect(() => {
    if (!pairing) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [pairing])
  const state = status.data?.status ?? 'waiting'
  useEffect(() => {
    if (state === 'connected') void qc.invalidateQueries({ queryKey: ['devices'] })
  }, [state, qc])

  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      setPairing(await api<Pairing>('POST', '/api/devices/pairings', { name: name.trim() || pairing?.name }))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not make a code. Try again.')
    } finally {
      setBusy(false)
    }
  }
  const left = pairing ? Math.max(0, Date.parse(pairing.expiresAt) - now) : 0
  const expired = state === 'expired' || (pairing && left === 0 && state !== 'connected')
  const mmss = `${Math.floor(left / 60000)}:${String(Math.floor((left % 60000) / 1000)).padStart(2, '0')}`

  return (
    <Sheet title="Connect a laptop" sub="One code connects one laptop. It works once, for 10 minutes." onClose={onClose}>
      {!pairing ? (
        <form
          className="pair-start"
          onSubmit={(e) => {
            e.preventDefault()
            void start()
          }}
        >
          <label className="field">
            <span>Which laptop is this?</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Front desk laptop" maxLength={120} autoFocus required />
          </label>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="btn primary block" disabled={busy || !name.trim()}>
            {busy ? 'Making a code…' : 'Get a code'}
          </button>
        </form>
      ) : state === 'connected' ? (
        <div className="pair-done" role="status">
          <span className="pair-check" aria-hidden="true">
            ✓
          </span>
          <h4>{status.data?.device?.name ?? pairing.name} is connected</h4>
          <p className="muted">Captured chat from that laptop now uploads to this shop. You can close this.</p>
          <button type="button" className="btn primary block" onClick={onClose}>
            Done
          </button>
        </div>
      ) : (
        <div className="pair-code">
          <p className="muted small">
            Code for <b>{pairing.name}</b>
          </p>
          <div className={'code-box' + (expired ? ' expired' : '')} data-testid="pairing-code" aria-label={`Pairing code ${pairing.code.split('').join(' ')}`}>
            {pairing.code}
          </div>
          {expired ? (
            <>
              <p className="warn-text">This code expired.</p>
              <button type="button" className="btn primary block" onClick={() => void start()} disabled={busy}>
                Get a new code
              </button>
            </>
          ) : (
            <>
              <p className="pair-wait" aria-live="polite">
                <span className="spinner" aria-hidden="true" /> Waiting for the laptop… <span className="muted">expires in {mmss}</span>
              </p>
              <ol className="pair-steps">
                <li>On that laptop, click the Chat Scraper icon in Chrome's toolbar and choose <b>Settings</b>.</li>
                <li>
                  Type <b>{pairing.code}</b> and press <b>Connect</b>.
                </li>
              </ol>
              <button type="button" className="btn ghost block" onClick={() => navigator.clipboard?.writeText(pairing.code).catch(() => {})}>
                Copy code
              </button>
            </>
          )}
        </div>
      )}
    </Sheet>
  )
}

/** The old way, for scripts or when a code cannot be typed: a key shown once. */
function ManualKey() {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [newKey, setNewKey] = useState<{ name: string; key: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  return (
    <details className="manual-key">
      <summary>Advanced: create a device key instead</summary>
      <form
        className="inline-form"
        onSubmit={async (e) => {
          e.preventDefault()
          setError(null)
          try {
            const r = await api<{ device: Device; key: string }>('POST', '/api/devices', { name })
            setNewKey({ name: r.device.name, key: r.key })
            setName('')
            await qc.invalidateQueries({ queryKey: ['devices'] })
          } catch (err) {
            setError(err instanceof ApiError ? err.message : 'Could not create a key.')
          }
        }}
      >
        <label className="field">
          <span>Laptop name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Front laptop" required />
        </label>
        <button type="submit" className="btn">
          Create key
        </button>
      </form>
      {error && <p className="form-error">{error}</p>}
      {newKey && (
        <p className="card" data-testid="new-key">
          Key for <b>{newKey.name}</b> (copy it now, it will not be shown again): <code>{newKey.key}</code>
        </p>
      )}
    </details>
  )
}
