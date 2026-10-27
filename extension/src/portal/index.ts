/**
 * Runs on the shop's web app only (the download writes its address into the
 * manifest). Tells the page that Chat Scraper is on this laptop, its id and
 * version, so Settings → Laptops can offer the update and then ask the
 * background to reload (externally_connectable). It reads nothing from the page.
 */
import type { PortalHere } from '../shared/messages'

function here(): void {
  let msg: PortalHere
  try {
    msg = { source: 'chat-scraper', type: 'here', id: chrome.runtime.id, version: chrome.runtime.getManifest().version }
  } catch {
    return // cut off by a reload; the new copy answers through the background
  }
  window.postMessage(msg, location.origin)
}

window.addEventListener('message', (e) => {
  const d = e.data as { source?: unknown; type?: unknown } | null
  if (e.source === window && e.origin === location.origin && d?.source === 'sales-ops' && d.type === 'who') here()
})
here()
