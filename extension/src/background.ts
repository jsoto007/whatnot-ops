import type { Request, SessionReply } from './shared/messages'
import { RESUME_HOURS } from './shared/messages'
import { KV_SESSION, type SessionRef, getRows, getShow, kvDelete, kvGet, kvSet, pruneShows, putRows } from './storage/db'
import { migrateLegacyStorage } from './storage/migrate'

const SEARCH_URL = () => chrome.runtime.getURL('search.html')

function openSearch(): void {
  chrome.tabs.create({ url: SEARCH_URL() })
}

// Toolbar button: show/hide the panel on a Whatnot tab, open Chat Search elsewhere.
chrome.action.onClicked.addListener((tab) => {
  if (tab.id === undefined) return openSearch()
  chrome.tabs.sendMessage(tab.id, { cmd: 'toggle' }).catch(openSearch)
})

chrome.runtime.onInstalled.addListener(() => void migrateLegacyStorage())
chrome.runtime.onStartup.addListener(() => void migrateLegacyStorage())

function notifyPages(showId: string): void {
  // Reaches extension pages (Chat Search); nobody listening is fine.
  chrome.runtime.sendMessage({ cmd: 'showUpdated', showId }).catch(() => {})
}

async function handle(msg: Request): Promise<unknown> {
  switch (msg.cmd) {
    case 'rows': {
      await migrateLegacyStorage()
      const meta = await putRows(msg.show, msg.rows)
      await kvSet(KV_SESSION, { showId: meta.id } satisfies SessionRef)
      await pruneShows()
      notifyPages(meta.id)
      return { ok: true, count: meta.count }
    }
    case 'getSession': {
      await migrateLegacyStorage()
      const ref = await kvGet<SessionRef>(KV_SESSION)
      if (!ref) return null
      const show = await getShow(ref.showId)
      if (!show || Date.now() - show.updatedAt > RESUME_HOURS * 3_600_000) return null
      const rows = await getRows(show.id)
      return { show, rows } satisfies SessionReply
    }
    case 'clearSession':
      await kvDelete(KV_SESSION)
      return { ok: true }
    case 'openSearch':
      openSearch()
      return { ok: true }
  }
}

chrome.runtime.onMessage.addListener((msg: Request, _sender, sendResponse) => {
  handle(msg).then(sendResponse, (err: unknown) => sendResponse({ error: String(err) }))
  return true // async response
})
