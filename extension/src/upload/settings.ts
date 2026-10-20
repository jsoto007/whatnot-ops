/**
 * Where this laptop uploads, and with which key.
 *
 * - The server address lives in chrome.storage.local (the Settings page and the
 *   background read it). It must be https, except for localhost while testing.
 * - The device key lives in the extension's own IndexedDB (`kv` store), which
 *   the content script on whatnot.com cannot read: content scripts share the
 *   page's IndexedDB, not the extension's.
 * - The key is bound to the server origin it was issued for. If the address
 *   changes, the key is not sent to the new host; the laptop must pair again.
 */
import { kvDelete, kvGet, kvSet } from '../storage/db'

export const SETTINGS_KEYS = { url: 'cs_server_url', legacyKey: 'cs_api_key' } as const
export const KV_CONNECTION = 'connection'
/** `${origin}|${organization}` of the last shop this laptop was connected to. */
export const KV_LAST_SCOPE = 'last_scope'

export interface Settings {
  serverUrl: string
  apiKey: string
}
export interface Connection {
  apiKey: string
  origin: string
  organization?: string
}
interface Kv {
  get<T>(key: string): Promise<T | undefined>
  set(key: string, value: unknown): Promise<void>
  del(key: string): Promise<void>
}
const idbKv: Kv = { get: kvGet, set: kvSet, del: kvDelete }

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]'])

/** The origin to upload to, or a plain-language error. Paths, query strings and credentials are dropped or refused. */
export function serverOrigin(input: string): string {
  const s = input.trim()
  if (!s) return ''
  let url: URL
  try {
    url = new URL(s)
  } catch {
    throw new Error('That is not a web address. Use something like https://ops.example.com')
  }
  if (url.username || url.password) throw new Error('The web app address cannot contain a user name or password.')
  const local = LOCAL_HOSTS.has(url.hostname)
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && local)) {
    throw new Error('The web app address must start with https:// (http:// is only allowed for localhost).')
  }
  return url.origin
}

const safeOrigin = (u: string): string => {
  try {
    return serverOrigin(u)
  } catch {
    return ''
  }
}

/** Kept for callers that only tidy text; validation is serverOrigin(). */
export function normaliseUrl(u: string): string {
  return u.trim().replace(/\/+$/, '')
}

/** Build-time default (dist/defaults.json), used when the laptop has no server address saved yet. */
export async function defaultServerUrl(fetchFn: typeof fetch = fetch): Promise<string> {
  try {
    const res = await fetchFn(chrome.runtime.getURL('defaults.json'))
    const j = (await res.json()) as { serverUrl?: string }
    return safeOrigin(j.serverUrl ?? '')
  } catch {
    return ''
  }
}

export async function loadSettings(storage = chrome.storage.local, fetchFn: typeof fetch = fetch, kv: Kv = idbKv): Promise<Settings> {
  const r = await storage.get([SETTINGS_KEYS.url, SETTINGS_KEYS.legacyKey])
  const serverUrl = safeOrigin(String(r[SETTINGS_KEYS.url] ?? '')) || (await defaultServerUrl(fetchFn))
  let conn = await kv.get<Connection>(KV_CONNECTION)
  const legacy = String(r[SETTINGS_KEYS.legacyKey] ?? '').trim()
  if (legacy) {
    // Older builds kept the key in chrome.storage.local; move it out of the content script's reach.
    if (!conn && serverUrl) {
      conn = { apiKey: legacy, origin: serverUrl }
      await kv.set(KV_CONNECTION, conn)
    }
    await storage.remove(SETTINGS_KEYS.legacyKey)
  }
  const apiKey = conn && serverUrl && conn.origin === serverUrl ? conn.apiKey : ''
  return { serverUrl, apiKey }
}

/** Save the address (validated). Throws with a message for the user when it is not acceptable. */
export async function saveServerUrl(input: string, storage = chrome.storage.local): Promise<string> {
  const origin = serverOrigin(input)
  await storage.set({ [SETTINGS_KEYS.url]: origin })
  return origin
}

/** Remember a key for this server. Returns true when this is a different shop than last time. */
export async function saveConnection(c: Connection, kv: Kv = idbKv): Promise<boolean> {
  await kv.set(KV_CONNECTION, { ...c, apiKey: c.apiKey.trim() })
  if (!c.organization) return false
  const scope = `${c.origin}|${c.organization}`
  const prev = await kv.get<string>(KV_LAST_SCOPE)
  await kv.set(KV_LAST_SCOPE, scope)
  return prev !== undefined && prev !== scope
}

export async function forgetConnection(kv: Kv = idbKv): Promise<void> {
  await kv.del(KV_CONNECTION)
}

export const configured = (s: Settings): boolean => !!s.serverUrl && !!s.apiKey
