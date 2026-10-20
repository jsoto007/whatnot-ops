/** Server connection, kept in chrome.storage.local (shared by background, options page and panel). */
export const SETTINGS_KEYS = { url: 'cs_server_url', key: 'cs_api_key' } as const

export interface Settings {
  serverUrl: string
  apiKey: string
}

/** Build-time default (dist/defaults.json), used when the laptop has no server URL saved yet. */
export async function defaultServerUrl(fetchFn: typeof fetch = fetch): Promise<string> {
  try {
    const res = await fetchFn(chrome.runtime.getURL('defaults.json'))
    const j = (await res.json()) as { serverUrl?: string }
    return normaliseUrl(j.serverUrl ?? '')
  } catch {
    return ''
  }
}

export async function loadSettings(storage = chrome.storage.local, fetchFn: typeof fetch = fetch): Promise<Settings> {
  const r = await storage.get([SETTINGS_KEYS.url, SETTINGS_KEYS.key])
  const saved = normaliseUrl(String(r[SETTINGS_KEYS.url] ?? ''))
  return {
    serverUrl: saved || (await defaultServerUrl(fetchFn)),
    apiKey: String(r[SETTINGS_KEYS.key] ?? '').trim(),
  }
}

export async function saveSettings(s: Settings, storage = chrome.storage.local): Promise<void> {
  await storage.set({ [SETTINGS_KEYS.url]: normaliseUrl(s.serverUrl), [SETTINGS_KEYS.key]: s.apiKey.trim() })
}

export function normaliseUrl(u: string): string {
  return u.trim().replace(/\/+$/, '')
}

export const configured = (s: Settings): boolean => !!s.serverUrl && !!s.apiKey
