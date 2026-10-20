/** Server connection, kept in chrome.storage.local (shared by background, options page and panel). */
export const SETTINGS_KEYS = { url: 'cs_server_url', key: 'cs_api_key' } as const

export interface Settings {
  serverUrl: string
  apiKey: string
}

export async function loadSettings(storage = chrome.storage.local): Promise<Settings> {
  const r = await storage.get([SETTINGS_KEYS.url, SETTINGS_KEYS.key])
  return {
    serverUrl: normaliseUrl(String(r[SETTINGS_KEYS.url] ?? '')),
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
