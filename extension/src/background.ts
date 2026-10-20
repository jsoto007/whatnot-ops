import type { Request, SessionReply, UploadStatus } from './shared/messages'
import { RESUME_HOURS } from './shared/messages'
import {
  KV_SESSION,
  type SessionRef,
  getRows,
  getShow,
  kvDelete,
  kvGet,
  kvSet,
  markParked,
  markUploaded,
  parkAllPending,
  parkedCount,
  pendingCount,
  pendingRows,
  pruneShows,
  putRows,
  uploadedCount,
} from './storage/db'
import { migrateLegacyStorage } from './storage/migrate'
import { clampRow, clampUrl } from './upload/limits'
import { configured, loadSettings } from './upload/settings'
import { UploadError, type UploadOutcome, backoffSeconds, uploadOnce } from './upload/uploader'

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

// ---------- upload queue ----------
// Rows wait in IndexedDB with uploaded=0. A flush runs shortly after new rows
// arrive, on a retry alarm after a failure (exponential backoff), every minute
// as a safety net, and whenever the options page asks.
const ALARM_RETRY = 'cs_upload_retry'
const ALARM_SWEEP = 'cs_upload_sweep'
let failures = 0
let lastError: string | undefined
let lastSentAt: number | undefined
let flushing: Promise<UploadOutcome> | null = null
let debounce: ReturnType<typeof setTimeout> | null = null

chrome.alarms.create(ALARM_SWEEP, { periodInMinutes: 1 })
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === ALARM_RETRY || a.name === ALARM_SWEEP) void flush()
})

function scheduleFlush(delayMs = 2000): void {
  if (debounce) clearTimeout(debounce)
  debounce = setTimeout(() => void flush(), delayMs)
}

async function flush(): Promise<UploadOutcome> {
  if (flushing) return flushing
  flushing = (async () => {
    try {
      const settings = await loadSettings()
      const outcome = await uploadOnce(
        { pendingRows, pendingCount, markUploaded, markParked, show: async (id) => (await getShow(id)) ?? undefined },
        settings,
      )
      if (outcome.status === 'sent') {
        failures = 0
        lastError = outcome.parked ? `${outcome.parked} lines set aside (the server refused them): ${outcome.detail ?? ''}` : undefined
        lastSentAt = Date.now()
        if (outcome.remaining > 0) scheduleFlush(250)
      } else if (outcome.status === 'unconfigured') lastError = 'server not configured'
      return outcome
    } catch (err) {
      const e = err as UploadError
      lastError = e.message
      if (e.kind === 'offline') {
        failures++
        chrome.alarms.create(ALARM_RETRY, { delayInMinutes: backoffSeconds(failures) / 60 })
        return { status: 'offline', detail: e.message }
      }
      // auth: keep the rows, stop retrying until the connection changes or a sweep.
      return e.kind === 'auth' ? { status: 'auth' } : { status: 'rejected', detail: e.message }
    } finally {
      flushing = null
    }
  })()
  return flushing
}

async function uploadStatus(): Promise<UploadStatus> {
  return {
    configured: configured(await loadSettings()),
    pending: await pendingCount(),
    uploaded: await uploadedCount(),
    parked: await parkedCount(),
    lastError,
    lastSentAt,
  }
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes['cs_server_url']) {
    failures = 0
    scheduleFlush(100)
  }
})

/** Commands only the extension's own pages (Settings, Chat Search) may send; content scripts may not. */
const PAGE_ONLY = new Set<Request['cmd']>(['settingsChanged', 'parkPending'])
const CMDS = new Set<Request['cmd']>(['rows', 'getSession', 'clearSession', 'openSearch', 'openOptions', 'getUploadStatus', 'flushUploads', 'settingsChanged', 'parkPending'])

/** A content-script batch, cut to the server's limits so one bad line cannot stall uploads. */
function cleanRows(msg: Extract<Request, { cmd: 'rows' }>): Extract<Request, { cmd: 'rows' }> | null {
  if (!msg.show || typeof msg.show.id !== 'string' || !Array.isArray(msg.rows) || msg.rows.length > 1000) return null
  const rows = msg.rows.filter((r) => r && typeof r.id === 'string' && r.id.length <= 64 && typeof r.seq === 'number').map(clampRow)
  return { cmd: 'rows', show: { ...msg.show, url: clampUrl(msg.show.url) }, rows }
}

async function handle(msg: Request): Promise<unknown> {
  switch (msg.cmd) {
    case 'settingsChanged':
      failures = 0
      lastError = undefined
      scheduleFlush(100)
      return { ok: true }
    case 'parkPending':
      return { ok: true, parked: await parkAllPending() }
    case 'rows': {
      await migrateLegacyStorage()
      const meta = await putRows(msg.show, msg.rows)
      await kvSet(KV_SESSION, { showId: meta.id } satisfies SessionRef)
      await pruneShows()
      notifyPages(meta.id)
      scheduleFlush()
      return { ok: true, count: meta.count, upload: await uploadStatus() }
    }
    case 'getUploadStatus':
      return uploadStatus()
    case 'flushUploads':
      return flush()
    case 'openOptions':
      await chrome.runtime.openOptionsPage()
      return { ok: true }
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

chrome.runtime.onMessage.addListener((raw: Request, sender, sendResponse) => {
  // Only this extension's own content scripts and pages; never another extension.
  if (sender.id !== chrome.runtime.id || !raw || !CMDS.has(raw.cmd)) return false
  // Extension pages (Settings opens in a tab) have this extension's origin; a content
  // script's sender.url is the whatnot.com page it runs in.
  const fromPage = !!sender.url && sender.url.startsWith(chrome.runtime.getURL(''))
  if (PAGE_ONLY.has(raw.cmd) && !fromPage) return false
  const msg = raw.cmd === 'rows' ? cleanRows(raw) : raw
  if (!msg) {
    sendResponse({ error: 'bad rows message' })
    return false
  }
  handle(msg).then(sendResponse, () => sendResponse({ error: 'request failed' }))
  return true // async response
})
