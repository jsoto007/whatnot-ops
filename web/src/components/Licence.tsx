import { useState } from 'react'
import { useAuth } from '../auth/useAuth'
import { ApiError, api, type Me } from '../lib/api'
import { LICENCE_ACCEPT, LICENCE_TITLE, LICENCE_URL } from '../lib/labels'
import { Icon } from './Icon'

/** The licence's name, opening the terms page (/license) in a new tab: the page behind it keeps what was typed. */
export function LicenceLink({ children = LICENCE_TITLE }: { children?: string }) {
  return (
    <a href={LICENCE_URL} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  )
}

/**
 * Asks the shop's owner to accept the licence, on every page until they do: shops created
 * before sign-up had the box (api services/licence.py). The server decides who is asked
 * (`me.licence.needed`: owners only, never staff or the developer's support account).
 */
export function LicenceBanner() {
  const { me, setMe } = useAuth()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const status = me?.licence
  if (!status?.needed) return null
  const accept = async () => {
    setBusy(true)
    setError(null)
    try {
      setMe(await api<Me>('POST', '/api/auth/licence', { version: status.version }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Try again.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="banner" aria-label={LICENCE_TITLE} data-testid="licence-banner">
      <div className="banner-main">
        <span className="banner-icon" aria-hidden>
          <Icon name="clipboard" size={20} />
        </span>
        <div>
          <b>Please read and accept the {LICENCE_TITLE}.</b>
          <div className="small">
            Your shop's data stays yours; the software is licensed to the shop. Version {status.version}. You see this
            because you own the shop; your team does not.
          </div>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
        </div>
      </div>
      <div className="btns">
        <a className="btn" href={LICENCE_URL} target="_blank" rel="noopener noreferrer">
          Read the terms
        </a>
        <button type="button" className="btn primary" onClick={accept} disabled={busy} data-testid="accept-licence">
          {busy ? '…' : LICENCE_ACCEPT}
        </button>
      </div>
    </section>
  )
}
