import { useEffect, useRef, useState } from 'react'
import { Icon } from '../../../components/Icon'
import { Sheet } from '../../../components/Sheet'
import { askExtension, isNewer, setLaptopVersion, useExtensionInfo, useThisLaptop, type ExtensionHello } from '../../../lib/extension'

const POLL_MS = 2000
const BACK_MS = 20_000
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

type Phase = 'ready' | 'reloading' | 'done' | 'stale' | 'lost'

/**
 * Update Chat Scraper in place: download, copy the files over the folder Chrome
 * loads it from, reload. The laptop keeps its pairing and every saved line;
 * removing the extension would lose both. When this laptop runs 1.5.0 or later
 * the sheet sees the new files land and reloads it with one button.
 */
export function UpdateExtension({ latest, forName, onClose }: { latest: string; forName?: string; onClose: () => void }) {
  const laptop = useThisLaptop()
  const [hello, setHello] = useState<ExtensionHello | null>(null)
  const [reachable, setReachable] = useState<boolean | null>(null)
  const [phase, setPhase] = useState<Phase>('ready')
  const [error, setError] = useState<string | null>(null)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  // Watch the folder: the extension reports the version of the files it would load.
  useEffect(() => {
    if (!laptop || phase === 'reloading' || phase === 'done') return
    let live = true
    const tick = () =>
      askExtension<ExtensionHello>(laptop.id, { cmd: 'hello' }).then(
        (h) => {
          if (!live) return
          setHello(h)
          setReachable(true)
          setPhase((p) => (p === 'lost' ? 'ready' : p))
        },
        () => live && setReachable(false),
      )
    void tick()
    const t = setInterval(tick, POLL_MS)
    return () => {
      live = false
      clearInterval(t)
    }
  }, [laptop, phase])

  const running = hello?.version ?? laptop?.version
  const upToDate = !!running && !isNewer(latest, running)
  const canReload = !!laptop && reachable === true
  const newFiles = !!hello && (hello.filesVersion === null || !isNewer(latest, hello.filesVersion))

  const reload = async () => {
    if (!laptop) return
    setPhase('reloading')
    setError(null)
    try {
      await askExtension(laptop.id, { cmd: 'reload' })
    } catch (e) {
      setPhase('ready')
      setError((e as Error).message)
      return
    }
    const until = Date.now() + BACK_MS
    while (Date.now() < until && alive.current) {
      await sleep(1000)
      try {
        const h = await askExtension<ExtensionHello>(laptop.id, { cmd: 'hello' }, 1500)
        if (!alive.current) return
        setHello(h)
        setLaptopVersion(laptop.id, h.version)
        setPhase(isNewer(latest, h.version) ? 'stale' : 'done')
        return
      } catch {
        /* still coming back */
      }
    }
    if (alive.current) setPhase('lost')
  }

  const done = phase === 'done' || (phase === 'ready' && upToDate && !!laptop)
  return (
    <Sheet
      title="Update Chat Scraper"
      sub={
        forName && !laptop ? (
          <>
            Do this on <b>{forName}</b>. It keeps the laptop connected.
          </>
        ) : running ? (
          `This laptop has ${running}. The new version is ${latest}.`
        ) : (
          `The new version is ${latest}. Updating keeps the laptop connected.`
        )
      }
      onClose={onClose}
    >
      {done ? (
        <div className="pair-done" role="status" data-testid="update-done">
          <span className="pair-check" aria-hidden="true">
            ✓
          </span>
          <h4>Chat Scraper {running} is running</h4>
          <p className="muted">This laptop is up to date and still connected. You can close this.</p>
          <button type="button" className="btn primary block" onClick={onClose}>
            Done
          </button>
        </div>
      ) : (
        <div className="update-ext">
          <p className="update-warn">
            <b>Don't remove the extension to update it.</b> Removing it disconnects the laptop and deletes any lines it hasn't uploaded yet.
          </p>
          <ol className="setup-steps">
            <li>
              <span className="step-n" aria-hidden="true">
                1
              </span>
              <div className="step-main">
                <b>Download version {latest}</b>
              </div>
              <a className="btn" href="/api/extension/download" download data-testid="update-download">
                <Icon name="download" size={16} /> Download
              </a>
            </li>
            <li>
              <span className="step-n" aria-hidden="true">
                2
              </span>
              <div className="step-main">
                <b>Replace the old files</b>
                <p className="muted small">
                  Unzip it. Copy everything inside into the folder Chrome loads Chat Scraper from, and replace the files there. To find that folder, open <code>chrome://extensions</code>, click{' '}
                  <b>Details</b> on Chat Scraper and look for <b>Loaded from</b>.
                </p>
                {canReload && (
                  <p className={'small update-files' + (newFiles ? ' ok' : '')} aria-live="polite" data-testid="update-files">
                    {newFiles ? (
                      <>✓ New files found{hello?.filesVersion ? ` (${hello.filesVersion})` : ''}</>
                    ) : (
                      <>
                        <span className="spinner" aria-hidden="true" /> Waiting for the new files. The folder still has {hello?.filesVersion}.
                      </>
                    )}
                  </p>
                )}
              </div>
            </li>
            <li>
              <span className="step-n" aria-hidden="true">
                3
              </span>
              <div className="step-main">
                <b>Reload Chat Scraper</b>
                {canReload ? (
                  <p className="muted small">
                    {hello?.recording ? 'A show is recording on this laptop. It keeps recording after the reload. ' : ''}
                    Keep Developer mode on in <code>chrome://extensions</code>. Chrome turns off reloaded extensions when it's off.
                  </p>
                ) : (
                  <p className="muted small">
                    Open <code>chrome://extensions</code> and click <b>↻</b> on Chat Scraper. Keep Developer mode on. Once this laptop has 1.5.0 or later, this button reloads it for you.
                  </p>
                )}
              </div>
              {canReload && (
                <button type="button" className="btn primary" onClick={() => void reload()} disabled={!newFiles || phase === 'reloading'} data-testid="update-reload">
                  {phase === 'reloading' ? 'Reloading…' : 'Reload Chat Scraper'}
                </button>
              )}
            </li>
          </ol>
          {phase === 'stale' && (
            <p className="form-error" role="alert">
              Chat Scraper reloaded but is still {running}. The folder still has the old files. Copy the new ones in, then reload again.
            </p>
          )}
          {phase === 'lost' && (
            <p className="form-error" role="alert">
              Chat Scraper hasn't come back. Open <code>chrome://extensions</code> and make sure it is turned on and Developer mode is on.
            </p>
          )}
          {error && (
            <p className="form-error" role="alert">
              Could not reload it: {error}
            </p>
          )}
        </div>
      )}
    </Sheet>
  )
}

const DISMISSED_KEY = 'wn.extension-update-dismissed'

function readDismissed(): string | null {
  try {
    return localStorage.getItem(DISMISSED_KEY)
  } catch {
    return null
  }
}

/** Across the top of every page when this laptop's Chat Scraper is behind; "Not now" hides it until the next version. */
export function UpdateBanner({ enabled }: { enabled: boolean }) {
  const laptop = useThisLaptop()
  const ext = useExtensionInfo(enabled && !!laptop)
  const [dismissed, setDismissed] = useState(readDismissed)
  const [open, setOpen] = useState(false)
  const latest = ext.data?.available ? ext.data.version : null
  if (!enabled || !laptop || !latest) return null
  const behind = isNewer(latest, laptop.version)
  if (!behind && !open) return null
  return (
    <>
      {behind && dismissed !== latest && (
        <div className="update-banner" role="status" data-testid="update-banner">
          <Icon name="download" size={18} />
          <span>
            <b>Chat Scraper {latest} is ready.</b> This laptop has {laptop.version}.
          </span>
          <button type="button" className="btn small primary" onClick={() => setOpen(true)}>
            Update
          </button>
          <button
            type="button"
            className="btn small ghost"
            onClick={() => {
              try {
                localStorage.setItem(DISMISSED_KEY, latest)
              } catch {
                /* private mode: hidden until the page reloads */
              }
              setDismissed(latest)
            }}
          >
            Not now
          </button>
        </div>
      )}
      {open && <UpdateExtension latest={latest} onClose={() => setOpen(false)} />}
    </>
  )
}
