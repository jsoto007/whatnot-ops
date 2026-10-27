import type { Broadcast, ChatSpot, PingReply, PortalHello, PortalRequest, Request, SessionReply, UploadStatus } from './shared/messages'
import { MAX_SELECTORS, RESUME_HOURS } from './shared/messages'
import {
  KV_RECORDING,
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
import { fetchStock } from './stock/fetch'
import { migrateLegacyStorage } from './storage/migrate'
import { type Tabs, claimRecording, endRecording, forgetRecording, resumeFor } from './storage/recording'
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

chrome.runtime.onInstalled.addListener(() => {
  void migrateLegacyStorage()
  void reinject()
})
chrome.runtime.onStartup.addListener(() => void migrateLegacyStorage())

/**
 * Chrome does not put content scripts back into open tabs after the extension
 * is updated or reloaded: the old one is cut off (it can no longer save) and
 * the tab would need a refresh. Inject the new one; it retires the old one and
 * carries on a recording that was running (content/index.ts).
 */
async function reinject(): Promise<void> {
  const cs = chrome.runtime.getManifest().content_scripts?.[0]
  if (!cs?.matches?.length || !cs.js?.length) return
  const open = await chrome.tabs.query({ url: cs.matches }).catch(() => [] as chrome.tabs.Tab[])
  for (const tab of open) {
    if (tab.id === undefined || tab.discarded) continue // a discarded tab gets the new script when it reloads
    chrome.scripting.executeScript({ target: { tabId: tab.id }, files: cs.js }).catch(() => {})
  }
}

const PING_MS = 1500

/** The tabs side of storage/recording.ts. */
const tabs: Tabs = {
  async isRecording(tabId) {
    try {
      const reply = await Promise.race([
        chrome.tabs.sendMessage<Broadcast, PingReply>(tabId, { cmd: 'ping' }),
        new Promise<null>((r) => setTimeout(() => r(null), PING_MS)),
      ])
      return reply?.recording === true
    } catch {
      return false // closed, or no Chat Scraper on that page any more
    }
  },
  release(tabId, showId) {
    chrome.tabs.sendMessage(tabId, { cmd: 'released', showId } satisfies Broadcast).catch(() => {})
  },
  keepAlive(tabId, keep) {
    chrome.tabs.update(tabId, { autoDiscardable: !keep }).catch(() => {})
  },
}

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
      counts = null // what is pending changed
    }
  })()
  return flushing
}

/**
 * Counting walks the index: with 30 kept shows of 20k lines (600k rows) one count
 * takes about 2 s (measured in Chromium). Only Settings gets the total uploaded; the
 * panel's 5-second poll (every Whatnot tab) reuses counts up to STATUS_MS old, and a
 * panel that is not connected gets none (it does not show them). Otherwise a long
 * show's saves queue up behind the counting.
 */
const STATUS_MS = 3000
let counts: { at: number; value: Promise<{ pending: number; parked: number }> } | null = null

async function uploadStatus(full = false): Promise<UploadStatus> {
  const isConfigured = configured(await loadSettings())
  if (!full && !isConfigured) return { configured: false, pending: 0, parked: 0, lastError, lastSentAt }
  if (full || !counts || Date.now() - counts.at > STATUS_MS) {
    counts = { at: Date.now(), value: Promise.all([pendingCount(), parkedCount()]).then(([pending, parked]) => ({ pending, parked })) }
    counts.value.catch(() => (counts = null))
  }
  return {
    configured: isConfigured,
    ...(await counts.value),
    ...(full ? { uploaded: await uploadedCount() } : {}),
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
const CMDS = new Set<Request['cmd']>(['rows', 'recording', 'getSession', 'clearSession', 'openSearch', 'openOptions', 'getUploadStatus', 'flushUploads', 'getStock', 'settingsChanged', 'parkPending'])

/** A content-script batch, cut to the server's limits so one bad line cannot stall uploads. */
function cleanRows(msg: Extract<Request, { cmd: 'rows' }>): Extract<Request, { cmd: 'rows' }> | null {
  if (!msg.show || typeof msg.show.id !== 'string' || !Array.isArray(msg.rows) || msg.rows.length > 1000) return null
  const rows = msg.rows.filter((r) => r && typeof r.id === 'string' && r.id.length <= 64 && typeof r.seq === 'number').map(clampRow)
  return { cmd: 'rows', show: { ...msg.show, url: clampUrl(msg.show.url) }, rows }
}

const okSpot = (s: ChatSpot | null): boolean =>
  s === null ||
  (!!s &&
    Array.isArray(s.selectors) &&
    s.selectors.length > 0 &&
    s.selectors.length <= MAX_SELECTORS &&
    s.selectors.every((x) => typeof x === 'string' && x.length > 0 && x.length <= 2000) &&
    typeof s.page === 'string' &&
    s.page.length <= 2048)

function cleanRecording(msg: Extract<Request, { cmd: 'recording' }>): Extract<Request, { cmd: 'recording' }> | null {
  if (!msg.show || typeof msg.show.id !== 'string' || msg.show.id.length > 64 || typeof msg.show.startedAt !== 'string') return null
  if (!okSpot(msg.spot ?? null)) return null
  const spot = msg.spot ? { selectors: msg.spot.selectors, page: msg.spot.page } : null
  return { cmd: 'recording', show: { id: msg.show.id, startedAt: msg.show.startedAt, url: clampUrl(msg.show.url) }, spot }
}

async function handle(msg: Request, sender: chrome.runtime.MessageSender): Promise<unknown> {
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
      counts = null // the panel's "uploaded" must count the rows just stored
      return { ok: true, count: meta.count, upload: await uploadStatus() }
    }
    case 'recording': {
      const tabId = sender.tab?.id
      if (!msg.spot) {
        await endRecording(tabId, tabs)
        return { ok: true }
      }
      // Register the show before its first lines arrive, so a reload right away still resumes it.
      await putRows(msg.show, [])
      await kvSet(KV_SESSION, { showId: msg.show.id } satisfies SessionRef)
      await claimRecording(msg.show.id, msg.spot, tabId, tabs)
      return { ok: true }
    }
    case 'getUploadStatus':
      return uploadStatus(fromExtensionPage(sender))
    case 'flushUploads':
      return flush()
    case 'getStock':
      // The key never reaches the content script; it only gets the stock rows.
      return fetchStock(await loadSettings(), typeof msg.showId === 'string' && msg.showId.length <= 64 ? msg.showId : undefined)
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
      return { show, rows, ...(await resumeFor(show.id, sender.tab?.id, tabs)) } satisfies SessionReply
    }
    case 'clearSession':
      await kvDelete(KV_SESSION)
      await forgetRecording(tabs)
      return { ok: true }
    case 'openSearch':
      openSearch()
      return { ok: true }
  }
}

// Extension pages (Settings opens in a tab) have this extension's origin; a content
// script's sender.url is the whatnot.com page it runs in.
const fromExtensionPage = (sender: chrome.runtime.MessageSender): boolean =>
  !!sender.url && sender.url.startsWith(chrome.runtime.getURL(''))

chrome.runtime.onMessage.addListener((raw: Request, sender, sendResponse) => {
  // Only this extension's own content scripts and pages; never another extension.
  if (sender.id !== chrome.runtime.id || !raw || !CMDS.has(raw.cmd)) return false
  if (PAGE_ONLY.has(raw.cmd) && !fromExtensionPage(sender)) return false
  const msg = raw.cmd === 'rows' ? cleanRows(raw) : raw.cmd === 'recording' ? cleanRecording(raw) : raw
  if (!msg) {
    sendResponse({ error: 'bad ' + raw.cmd + ' message' })
    return false
  }
  handle(msg, sender).then(sendResponse, () => sendResponse({ error: 'request failed' }))
  return true // async response
})

// ---------- the shop's web app ----------
// Settings → Laptops asks which version runs here and, once the files in this
// folder were replaced with a new download, tells Chrome to load them. Chrome
// only lets the portal's origin call (externally_connectable, written by its
// download); this also checks it is the shop this laptop uploads to.

/** The version of the manifest in the folder now: Chrome reads an unpacked extension's files from disk. */
async function filesVersion(): Promise<string | null> {
  try {
    const res = await fetch(chrome.runtime.getURL('manifest.json'), { cache: 'no-store' })
    const v = ((await res.json()) as { version?: unknown }).version
    return typeof v === 'string' ? v : null
  } catch {
    return null
  }
}

chrome.runtime.onMessageExternal.addListener((raw: PortalRequest, sender, sendResponse) => {
  if (!raw || (raw.cmd !== 'hello' && raw.cmd !== 'reload')) return false
  void (async () => {
    const { serverUrl } = await loadSettings()
    if (!serverUrl || sender.origin !== serverUrl) return sendResponse({ error: 'not the web app this laptop uploads to' })
    if (raw.cmd === 'reload') {
      sendResponse({ ok: true })
      // Rows are already in IndexedDB; a recording carries on after the reload (onInstalled → reinject).
      setTimeout(() => chrome.runtime.reload(), 250)
      return
    }
    sendResponse({
      version: chrome.runtime.getManifest().version,
      filesVersion: await filesVersion(),
      recording: !!(await kvGet(KV_RECORDING)),
    } satisfies PortalHello)
  })().catch(() => sendResponse({ error: 'request failed' }))
  return true // async response
})
