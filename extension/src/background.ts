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
  markUploaded,
  pendingCount,
  pendingRows,
  pruneShows,
  putRows,
  uploadedCount,
} from './storage/db'
import { migrateLegacyStorage } from './storage/migrate'
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
        { pendingRows, pendingCount, markUploaded, show: async (id) => (await getShow(id)) ?? undefined },
        settings,
      )
      if (outcome.status === 'sent') {
        failures = 0
        lastError = undefined
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
      // auth / rejected: keep the rows, stop retrying until settings change or a sweep.
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
    lastError,
    lastSentAt,
  }
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && (changes['cs_server_url'] || changes['cs_api_key'])) {
    failures = 0
    scheduleFlush(100)
  }
})

async function handle(msg: Request): Promise<unknown> {
  switch (msg.cmd) {
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

chrome.runtime.onMessage.addListener((msg: Request, _sender, sendResponse) => {
  handle(msg).then(sendResponse, (err: unknown) => sendResponse({ error: String(err) }))
  return true // async response
})
