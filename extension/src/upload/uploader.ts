import type { CapturedRow } from '../capture/types'
import type { Settings } from './settings'

export const BATCH_MAX = 200

export interface ChatEventBatch {
  version: 1
  show: { id: string; startedAt: string; url?: string }
  rows: CapturedRow[]
}

export interface ShowLookup {
  (showId: string): Promise<{ startedAt: string; url?: string } | undefined>
}

export interface UploadStore {
  /** Oldest un-uploaded rows, across shows, up to `limit`. */
  pendingRows(limit: number): Promise<CapturedRow[]>
  pendingCount(): Promise<number>
  markUploaded(ids: string[]): Promise<void>
  show: ShowLookup
}

export type UploadOutcome =
  | { status: 'idle' }
  | { status: 'unconfigured' }
  | { status: 'sent'; sent: number; remaining: number }
  | { status: 'auth' }
  | { status: 'rejected'; detail: string }
  | { status: 'offline'; detail: string }

export class UploadError extends Error {
  constructor(
    readonly kind: 'auth' | 'rejected' | 'offline',
    message: string,
  ) {
    super(message)
  }
}

/** Sends one batch per show. Pure: fetch and store are injected, so it runs in Vitest. */
export async function uploadOnce(
  store: UploadStore,
  settings: Settings,
  fetchFn: typeof fetch = fetch,
): Promise<UploadOutcome> {
  if (!settings.serverUrl || !settings.apiKey) return { status: 'unconfigured' }
  const rows = await store.pendingRows(BATCH_MAX)
  if (!rows.length) return { status: 'idle' }
  const byShow = new Map<string, CapturedRow[]>()
  for (const r of rows) (byShow.get(r.showId) ?? byShow.set(r.showId, []).get(r.showId)!).push(r)
  let sent = 0
  for (const [showId, showRows] of byShow) {
    const meta = await store.show(showId)
    const batch: ChatEventBatch = {
      version: 1,
      show: { id: showId, startedAt: meta?.startedAt ?? showId, ...(meta?.url ? { url: meta.url } : {}) },
      rows: showRows,
    }
    await postBatch(settings, batch, fetchFn)
    await store.markUploaded(showRows.map((r) => r.id))
    sent += showRows.length
  }
  return { status: 'sent', sent, remaining: await store.pendingCount() }
}

export async function postBatch(settings: Settings, batch: ChatEventBatch, fetchFn: typeof fetch = fetch): Promise<void> {
  let res: Response
  try {
    res = await fetchFn(settings.serverUrl + '/api/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + settings.apiKey },
      body: JSON.stringify(batch),
    })
  } catch (err) {
    throw new UploadError('offline', (err as Error).message || 'network error')
  }
  if (res.ok) return
  if (res.status === 401) throw new UploadError('auth', 'server rejected the API key')
  if (res.status === 400 || res.status === 413 || res.status === 422) {
    let detail = `${res.status}`
    try {
      const j = (await res.json()) as { error?: string; details?: string[] }
      detail = [j.error, ...(j.details ?? [])].filter(Boolean).join('; ') || detail
    } catch {
      /* not JSON */
    }
    throw new UploadError('rejected', detail)
  }
  throw new UploadError('offline', `server returned ${res.status}`)
}

export async function ping(settings: Settings, fetchFn: typeof fetch = fetch): Promise<{ ok: true; device: string; organization: string }> {
  let res: Response
  try {
    res = await fetchFn(settings.serverUrl + '/api/ingest/ping', { headers: { Authorization: 'Bearer ' + settings.apiKey } })
  } catch (err) {
    throw new UploadError('offline', (err as Error).message || 'network error')
  }
  if (res.status === 401) throw new UploadError('auth', 'server rejected the API key')
  if (!res.ok) throw new UploadError('offline', `server returned ${res.status}`)
  return (await res.json()) as { ok: true; device: string; organization: string }
}

/** Trade a pairing code from the web app for this laptop's device key (used once, then forgotten). */
export async function pairDevice(
  serverUrl: string,
  code: string,
  fetchFn: typeof fetch = fetch,
): Promise<{ key: string; device: string; organization: string }> {
  let res: Response
  try {
    res = await fetchFn(serverUrl + '/api/pair', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: code.trim() }),
    })
  } catch (err) {
    throw new UploadError('offline', (err as Error).message || 'network error')
  }
  if (res.ok) return (await res.json()) as { key: string; device: string; organization: string }
  let detail = `server returned ${res.status}`
  try {
    detail = ((await res.json()) as { error?: string }).error ?? detail
  } catch {
    /* not JSON: keep the status */
  }
  throw new UploadError(res.status >= 500 ? 'offline' : 'rejected', detail)
}

/** Exponential backoff in seconds: 5, 10, 20 … capped at 5 minutes. */
export function backoffSeconds(failures: number): number {
  return Math.min(300, 5 * 2 ** Math.max(0, failures - 1))
}
