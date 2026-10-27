/**
 * Which Chat Scraper this is. Every call to the web app says so (X-Extension-Version),
 * so Settings → Laptops can show which laptops need the new download.
 */

/** This build's version, or '' where there is no extension runtime (tests). */
export function extensionVersion(): string {
  try {
    return chrome.runtime.getManifest().version
  } catch {
    return ''
  }
}

const parts = (v: string): number[] | null => (/^\d{1,5}(\.\d{1,5}){0,3}$/.test(v) ? v.split('.').map(Number) : null)

/** True when `latest` is a later version than `current` (dotted numbers, as Chrome writes them). */
export function isNewer(latest: string | null | undefined, current: string | null | undefined): boolean {
  const a = parts(latest ?? '')
  const b = parts(current ?? '')
  if (!a || !b) return false
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const d = (a[i] ?? 0) - (b[i] ?? 0)
    if (d) return d > 0
  }
  return false
}

/** The headers every device call sends: its key and which version is calling. */
export function deviceHeaders(apiKey: string, version = extensionVersion()): Record<string, string> {
  return { Authorization: 'Bearer ' + apiKey, ...(version ? { 'X-Extension-Version': version } : {}) }
}
