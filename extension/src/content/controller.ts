import { Recorder } from '../capture/recorder'
import { DeclineWatcher, isDeclineBody, outside } from '../capture/decline'
import { parseWinBody, WinWatcher } from '../capture/player'
import { buildSelector, chatContainer, findRow, scrollParent } from '../capture/row'
import type { CapturedRow } from '../capture/types'
import {
  type ChatSpot,
  MAX_SELECTORS,
  type Request,
  type SessionReply,
  type ShowRef,
  type UploadStatus,
  showPage,
} from '../shared/messages'
import { chatCsv, csvTime } from '../shared/csv'
import { showDate } from '../shared/showDate'
import { collectAll } from '../shipments/collect'
import { onShipmentsPage } from '../shipments/read'
import type { ShipmentsReply } from '../shipments/upload'
import { type StockSeen, stockAlerts } from '../stock/alerts'
import type { StockReply } from '../stock/fetch'
import { type BuyersReply, buyerLine } from '../buyers/known'
import { Panel, readLayout } from './panel'
import { dotState, uploadNote, winNotice } from './status'

const WORDS_KEY = 'cs_watchwords'
/** Where the host dragged the panel and how big they made it; every Whatnot tab shares it. */
const LAYOUT_KEY = 'cs_panel_layout'
/** Seller Hub's Shipments page: after a show, the host sends its rows from there. */
const SHIPMENTS_URL = 'https://www.whatnot.com/dashboard/shipments'
const FLUSH_MS = 1500
const FLUSH_AT = 50
/** Most rows in one message; the background refuses more than 1000. */
const FLUSH_MAX = 500
const RETRY_MS = 3000
const WIN_POLL_MS = 500
/** Declines outside the player (the Sold tab) are looked for on the timer, at most this often. */
const PAGE_SCAN_MS = 1000
/** Stock moves once an upload is parsed (a few seconds), so a slower poll is enough. */
const STOCK_POLL_MS = 8000
/** How often a recording checks the tab is still on the show's page. */
const PAGE_POLL_MS = 1000
/**
 * The seller's own live page shows chat but not who won: wins are read only from the buyer's
 * video player (capture/player.ts). Seen on the 2026-11-13 show: 45 minutes there, no wins.
 */
const SELLER_LIVE = /^\/dashboard\/live\//
const NO_WINS_HERE = 'Winners are not shown on the seller page. Record from the show opened in a buyer account.'
/**
 * No chat line for this long while the chat is on screen: Whatnot's chat may be scrolled up, and
 * then it adds few or no new lines (the 2026-11-13 show: about 3 minutes lost on one laptop).
 */
export const QUIET_MS = 120_000
const QUIET = "No new chat for 2 minutes. If Whatnot's chat is scrolled up, click its arrow to jump to the latest messages."
/** Find buyer refreshes at most this often while new lines arrive. */
export const FIND_MS = 300
/** Watch words apply this long after the last keystroke. */
const WORDS_MS = 250

type State = 'idle' | 'picking' | 'recording' | 'stopped'

const timeOf = (iso: string) => new Date(iso).toLocaleTimeString()

function send<T = unknown>(msg: Request): Promise<T> {
  try {
    return chrome.runtime.sendMessage(msg) as Promise<T>
  } catch (err) {
    return Promise.reject(err as Error) // "Extension context invalidated": the extension was reloaded
  }
}

/** The extension was updated or reloaded under this page: chrome.* calls fail from now on. */
const cutOff = () => !chrome.runtime?.id

/** Wires the panel, the Recorder and the background store together. */
export class Controller {
  readonly panel = new Panel()
  private log: CapturedRow[] = []
  private state: State = 'idle'
  /**
   * Set from Start until Stop. While set the show is recording, also when the chat is
   * off screen or the tab is on another page: it carries on by itself, and the
   * background keeps it so a reloaded page carries on too (storage/recording.ts).
   */
  private spot: ChatSpot | null = null
  private pageTimer: ReturnType<typeof setInterval> | null = null
  private uploadTimer: ReturnType<typeof setInterval> | null = null
  private startedAt: string | null = null
  private recorder: Recorder | null = null
  private wins: WinWatcher | null = null
  private declines: DeclineWatcher | null = null
  private lastPageScan = 0
  private winTimer: ReturnType<typeof setInterval> | null = null
  /** When the last chat line arrived, and whether the panel says the chat went quiet. */
  private lastChatAt = 0
  private quiet = false
  private stockTimer: ReturnType<typeof setInterval> | null = null
  /** Last stock poll of this recording; null until the first one (the baseline). */
  private stockSeen: StockSeen | null = null
  /** Counts the "who won?" questions, so only the latest answer names the panel's line. */
  private buyerAsk = 0
  private playerObserver: MutationObserver | null = null
  private observedPlayer: Element | null = null
  private pending: CapturedRow[] = []
  private flushTimer: ReturnType<typeof setTimeout> | null = null
  /** The save in flight. One at a time: rows that arrive meanwhile go in the next one. */
  private saving: Promise<void> | null = null
  private synced = 0
  private watchWords: string[] = []
  private findTerm = ''
  private findTimer: ReturnType<typeof setTimeout> | null = null
  private wordsTimer: ReturnType<typeof setTimeout> | null = null
  /** The background's last word on uploads; it colours the dot. */
  private upload: UploadStatus | null = null
  /** chrome.* is gone: the extension was reloaded under this page. */
  private dead = false
  private onPickBound = (e: MouseEvent) => this.onPick(e)
  private onUnload = () => void this.flush()

  constructor() {
    const p = this.panel
    // The page's own scripts can reach the panel (open shadow root) and dispatch
    // synthetic clicks/inputs. Only a person's real input may drive it.
    const root = p.host.shadowRoot
    for (const type of ['click', 'input', 'change', 'keydown']) {
      root?.addEventListener(type, (e) => {
        if (!e.isTrusted) e.stopImmediatePropagation()
      }, true)
    }
    p.el('start').onclick = () => {
      p.el('resumeRow').hidden = true
      this.startPicking()
    }
    p.el('stop').onclick = () => {
      void this.stopRecording()
      this.download('show')
      this.render()
    }
    p.el('search').onclick = () => {
      void this.flush().finally(() => send({ cmd: 'openSearch' }).catch(() => {}))
    }
    p.el('shipSend').onclick = () => void this.sendShipments()
    p.el('shipOpen').onclick = () => window.open(SHIPMENTS_URL, '_blank', 'noopener')
    p.el('backup').onclick = () => this.download('backup')
    p.el('settings').onclick = () => send({ cmd: 'openOptions' }).catch(() => {})
    this.uploadTimer = setInterval(() => {
      if (cutOff()) return this.cutOff()
      this.renderShip() // Seller Hub navigates without reloading the page
      void this.refreshUpload()
    }, 5000)
    p.el('clear').onclick = () => this.clear()
    p.el<HTMLInputElement>('find').addEventListener('input', (e) => {
      this.findTerm = (e.target as HTMLInputElement).value.trim().toLowerCase()
      this.renderFind()
    })
    // Watch words take effect as they are typed (a short pause after the last key), not only on
    // leaving the field: the flagged list then grows while typing, not under the next click. The
    // panel sits on its bottom edge, so a list that grows on blur moves the tabs out from under
    // the pointer between mousedown and mouseup.
    const words = p.el<HTMLInputElement>('words')
    words.addEventListener('input', () => {
      if (this.wordsTimer) clearTimeout(this.wordsTimer)
      this.wordsTimer = setTimeout(() => this.setWords(words.value), WORDS_MS)
    })
    words.addEventListener('change', () => this.setWords(words.value))
    p.el('resume').onclick = () => {
      p.el('resumeRow').hidden = true
      p.setStatus('Resumed ' + this.log.length + ' saved messages.', 'good')
      this.startPicking()
    }
    p.el('fresh').onclick = async () => {
      // Only offered before recording. If it is ever reached mid-show, stop and save first: with no
      // show the recorder would keep counting lines that flush() can never save.
      await this.stopRecording()
      this.log = []
      this.pending = []
      this.startedAt = null
      this.synced = 0
      this.state = 'idle'
      await send({ cmd: 'clearSession' }).catch(() => {})
      p.el('resumeRow').hidden = true
      this.rebuildFlags()
      p.setStatus('Ready. Press Start.')
      p.setSync('')
      this.renderWins()
      this.render()
    }
    window.addEventListener('beforeunload', this.onUnload)
  }

  async init(): Promise<void> {
    this.panel.mount()
    this.panel.onLayout = (l) => {
      ;(l ? chrome.storage.local.set({ [LAYOUT_KEY]: l }) : chrome.storage.local.remove(LAYOUT_KEY)).catch(() => {})
    }
    try {
      const saved = await chrome.storage.local.get(LAYOUT_KEY)
      this.panel.setLayout(readLayout(saved[LAYOUT_KEY]))
    } catch {
      /* extension reloaded: the panel stays in the corner */
    }
    try {
      const words = await chrome.storage.local.get(WORDS_KEY)
      if (Array.isArray(words[WORDS_KEY])) {
        this.watchWords = words[WORDS_KEY] as string[]
        this.panel.el<HTMLInputElement>('words').value = this.watchWords.join(', ')
      }
      const session = await send<SessionReply>({ cmd: 'getSession' })
      if (session && (session.rows.length || session.recording)) {
        this.log = session.rows
        this.startedAt = session.show.id
        this.synced = session.rows.length
        this.rebuildFlags()
        this.renderWins()
        if (session.recording) {
          // Nobody pressed Stop: the page reloaded (or the extension was updated). Carry on, no click.
          this.startRecording(null, session.recording)
          if (this.recorder?.attached) {
            this.panel.setStatus(`Carried on recording (${this.log.length} saved). Watching chat.`, 'good')
          }
        } else {
          this.panel.el('resumeRow').hidden = false
          this.panel.setStatus(
            session.elsewhere
              ? `This show is recording in another tab (${this.log.length} saved). Resume here to move it to this tab.`
              : 'Found ' + this.log.length + ' saved messages from earlier. Resume or start fresh?',
            'info',
          )
        }
      }
    } catch {
      this.panel.setStatus('Extension was reloaded. Refresh this page.', 'bad')
    }
    this.render()
  }

  toggle(): void {
    this.panel.toggle()
  }

  /** Recording with the tab on the show's page (the background asks before another tab takes over). */
  get capturing(): boolean {
    return this.spot !== null && this.recorder !== null
  }

  /** Another tab took this show over (it was reloaded there, or Resume was pressed there). */
  release(showId: string): void {
    if (!this.spot || showId !== this.startedAt) return
    this.spot = null
    this.endCapture()
    void this.flush()
    this.state = this.log.length ? 'stopped' : 'idle'
    this.panel.setStatus('Recording moved to another tab; this one stopped.', 'info')
    this.render()
  }

  /** A newer copy of this script took over the tab (extension updated): leave without a trace. */
  destroy(): void {
    this.spot = null
    this.endCapture()
    if (this.uploadTimer) clearInterval(this.uploadTimer)
    for (const t of [this.flushTimer, this.findTimer, this.wordsTimer]) if (t) clearTimeout(t)
    this.uploadTimer = this.flushTimer = this.findTimer = this.wordsTimer = null
    document.removeEventListener('click', this.onPickBound, true)
    window.removeEventListener('beforeunload', this.onUnload)
    // Also drops the panel's window listener: it reached this Controller (and the show's log) through the buttons.
    this.panel.unmount()
  }

  /** Cut off with no newer copy injected (it failed, or Chrome blocked it): say what to do. */
  private cutOff(): void {
    this.dead = true
    this.spot = null
    this.endCapture()
    if (this.uploadTimer) clearInterval(this.uploadTimer)
    this.uploadTimer = null
    this.state = this.log.length ? 'stopped' : 'idle'
    this.render()
    this.panel.setStatus('The extension was updated or reloaded. Refresh this page to keep recording.', 'bad')
  }

  // ---------- capture ----------
  private startPicking(): void {
    this.state = 'picking'
    this.panel.setStatus('Now click one chat message (on the words, not the name).', 'info')
    this.render()
    document.addEventListener('click', this.onPickBound, true)
  }

  private onPick(e: MouseEvent): void {
    if (!e.isTrusted) return // a page script cannot choose what gets recorded
    if (e.composedPath().includes(this.panel.host)) return // clicks on our own panel
    e.preventDefault()
    e.stopPropagation()
    document.removeEventListener('click', this.onPickBound, true)
    const target = e.target as Element
    const row = findRow(target, scrollParent(target))
    if (!row) {
      this.panel.setStatus("Couldn't recognise that as a chat message. Press Start and click a different message.", 'bad')
      this.state = this.spot ? 'recording' : this.log.length ? 'stopped' : 'idle'
      this.render()
      return
    }
    const container = chatContainer(row)
    ;(container as HTMLElement).style.outline = '3px solid #69BE28'
    setTimeout(() => ((container as HTMLElement).style.outline = ''), 4000)
    const selector = buildSelector(row)
    const before = this.spot?.selectors.filter((s) => s !== selector) ?? []
    this.startRecording(container, { selectors: [selector, ...before].slice(0, MAX_SELECTORS), page: showPage(location.pathname) })
  }

  /**
   * Record from `spot` until Stop: on `box` (the host just clicked a message), or
   * wherever the rows are once the chat renders (a reloaded page, no click).
   */
  private startRecording(box: Element | null, spot: ChatSpot): void {
    this.startedAt ??= new Date().toISOString()
    this.spot = spot
    this.state = 'recording'
    this.saveSpot()
    this.pageTimer ??= setInterval(() => this.checkPage(), PAGE_POLL_MS)
    if (box || this.onShowPage()) this.capture(box)
    else this.paused()
    this.render()
  }

  /** The background keeps the spot so a reload carries on; it also keeps Chrome from discarding this tab. */
  private saveSpot(): void {
    const show = this.showRef()
    if (show) send({ cmd: 'recording', show, spot: this.spot }).catch(() => {})
  }

  private onShowPage(): boolean {
    return !!this.spot && showPage(location.pathname) === this.spot.page
  }

  /**
   * Whatnot changes pages without a reload. Off the show's page (another show's chat
   * must never land in this one) capture pauses; back on it, it carries on by itself.
   */
  private checkPage(): void {
    if (!this.spot) return
    const here = this.onShowPage()
    if (here && !this.recorder) this.capture(null)
    else if (!here && this.recorder) {
      this.endCapture()
      void this.flush()
      this.paused()
    } else return
    this.render()
  }

  private paused(): void {
    this.panel.setStatus("Paused: this tab isn't on the show's page. Go back to the show and recording carries on, or press Stop.", 'info')
  }

  private capture(box: Element | null): void {
    const spot = this.spot!
    this.endCapture()
    const rec = new Recorder(this.startedAt!, { ignore: (n) => this.panel.contains(n) })
    rec.on((ev) => {
      if (ev.type === 'row') {
        this.chatArrived()
        return this.onRow(ev.row)
      }
      if (ev.type === 'healed') {
        this.lastChatAt = Date.now() // the chat was away, not quiet
        this.quiet = false
        this.panel.setStatus('Chat re-rendered; found the messages again.', 'good')
        if (this.spot && this.spot.selectors[0] !== ev.selector) {
          const rest = this.spot.selectors.filter((s) => s !== ev.selector)
          this.spot = { ...this.spot, selectors: [ev.selector, ...rest].slice(0, MAX_SELECTORS) }
          this.saveSpot()
        }
      } else {
        this.panel.setStatus(
          "The chat isn't on screen (" + ev.reason + '). Still recording: it carries on as soon as the chat is back.',
          'info',
        )
      }
      this.render()
    })
    this.recorder = rec
    this.lastChatAt = Date.now()
    this.quiet = false
    // Whole show: whatever chat is still on screen is already saved. Wins are not chat lines.
    const known = this.log.filter((r) => r.source !== 'player').map((r) => r.rawText)
    if (box) rec.start(box, spot.selectors[0]!, this.log.length, known)
    else rec.resume(document, spot.selectors, this.log.length, known)
    // Whatnot shows who won what only in the video player, never in the chat: read it too.
    this.wins = new WinWatcher(this.startedAt!)
    this.wins.remember(this.log)
    // A card declined after a win: the player or the show's Sold tab says so (see decline.ts).
    this.declines = new DeclineWatcher(this.startedAt!)
    this.declines.remember(this.log)
    this.lastPageScan = 0
    this.winTimer = setInterval(() => this.checkPlayer(true), WIN_POLL_MS)
    this.checkPlayer(true)
    this.stockTimer = setInterval(() => void this.checkStock(), STOCK_POLL_MS)
    void this.checkStock()
    if (rec.attached) this.panel.setStatus('Watching chat.', 'good')
    else this.panel.setStatus('Recording. Waiting for the chat to show on this page.', 'info')
    this.renderWins()
  }

  /** Stop reading the page (chat, player, stock). The recording itself goes on while `spot` is set. */
  private endCapture(): void {
    this.recorder?.stop()
    this.recorder = null
    if (this.winTimer) clearInterval(this.winTimer)
    this.winTimer = null
    this.wins = null
    this.buyerAsk++ // an answer still on its way is not shown
    this.panel.setBuyer('')
    this.declines = null
    this.stopStock()
    this.playerObserver?.disconnect()
    this.playerObserver = null
    this.observedPlayer = null
    if (!this.spot && this.pageTimer) {
      clearInterval(this.pageTimer)
      this.pageTimer = null
    }
  }

  /** `timer`: the periodic check, which also looks outside the player for declines. */
  private checkPlayer(timer = false): void {
    if (!this.wins) return // not recording (a late observer callback after Stop)
    if (timer) this.checkQuiet()
    this.watchPlayer()
    const rows: CapturedRow[] = []
    const win = this.wins.check(document)
    if (win) {
      rows.push(win)
      this.declines?.noteWin(win)
    }
    if (this.declines) rows.push(...this.declines.check(document, this.declineScan(timer)))
    if (!rows.length) return
    for (const row of rows) {
      this.onRow(row)
      // Each win is also a notice beside the panel or the pill, for a few seconds.
      const notice = winNotice(row)
      if (notice) void this.whoWon(row, this.panel.notify(notice))
    }
    this.renderWins()
  }

  private checkQuiet(): void {
    if (this.quiet || !this.recorder?.attached || Date.now() - this.lastChatAt < QUIET_MS) return
    this.quiet = true
    this.panel.setStatus(QUIET, 'info')
  }

  private chatArrived(): void {
    this.lastChatAt = Date.now()
    if (!this.quiet) return
    this.quiet = false
    // Only take back our own words: another status may have replaced them meanwhile.
    if (this.panel.el('st').textContent === QUIET) this.panel.setStatus('Watching chat.', 'good')
  }

  /**
   * Where declines may be read. Never the chat (viewers type there) nor our panel. Outside the
   * player only while the Recorder is attached, so the chat box is known and can be skipped.
   */
  private declineScan(timer: boolean): { page: boolean; ignore: (n: Node) => boolean } {
    const rec = this.recorder
    const now = Date.now()
    const page = timer && !!rec?.attached && now - this.lastPageScan >= PAGE_SCAN_MS
    if (page) this.lastPageScan = now
    const selectors = [rec?.currentSelector ?? '', ...(this.spot?.selectors ?? [])]
    return { page, ignore: outside((n) => this.panel.contains(n), rec?.box ?? null, selectors) }
  }

  /** Check on every change to the player too: Chrome slows timers in a hidden or muted tab, not observers. */
  private watchPlayer(): void {
    const player = document.querySelector('[data-testid="live-player"]')
    if (player === this.observedPlayer) return
    this.playerObserver?.disconnect()
    this.observedPlayer = player
    this.playerObserver = player ? new MutationObserver(() => this.checkPlayer()) : null
    this.playerObserver?.observe(player!, { childList: true, subtree: true, characterData: true })
  }

  /** Warn when an item reaches 3, 2 or 1 left, or sells out (see stock/alerts.ts). */
  private async checkStock(): Promise<void> {
    let reply: StockReply | null
    try {
      reply = await send<StockReply | null>({ cmd: 'getStock', showId: this.startedAt ?? undefined })
    } catch {
      return // background asleep or reloaded; the next poll tries again
    }
    if (!reply || !this.stockTimer) return // not connected, offline, or stopped meanwhile
    const { alerts, seen } = stockAlerts(this.stockSeen, reply.items, reply.alertAt)
    this.stockSeen = seen
    this.panel.setLowStock(reply.items)
    this.panel.showStockAlerts(alerts)
  }

  /**
   * Ask the shop who the winner is (new, or back again; the team's flag) and say so on the win's
   * notice and in the panel. Says nothing when not connected or the web app does not answer.
   */
  private async whoWon(row: CapturedRow, notice: HTMLElement): Promise<void> {
    const win = parseWinBody(row.body)
    if (!win) return
    const asked = ++this.buyerAsk
    let reply: BuyersReply | null
    try {
      reply = await send<BuyersReply | null>({ cmd: 'getBuyers', showId: this.startedAt ?? undefined, names: [win.winner] })
    } catch {
      return
    }
    const buyer = reply?.buyers.find((b) => b.name.toLowerCase() === win.winner.toLowerCase())
    if (!buyer || !this.wins) return // not connected, offline, or stopped meanwhile
    const line = buyerLine(buyer)
    this.panel.amend(notice, line)
    // Answers can come back out of order: the panel's line is about the latest win only.
    if (asked === this.buyerAsk) this.panel.setBuyer(`${win.winner}: ${line}`)
  }

  private stopStock(): void {
    if (this.stockTimer) clearInterval(this.stockTimer)
    this.stockTimer = null
    this.stockSeen = null
    this.panel.setLowStock([])
    this.panel.clearStockAlert()
  }

  private renderWins(): void {
    const player = this.log.filter((r) => r.source === 'player')
    const wins = player.filter((r) => !isDeclineBody(r.body))
    const declined = player.length - wins.length
    const last = wins[wins.length - 1]
    this.panel.setWins(
      last
        ? `wins ${wins.length}` + (declined ? ` · declined ${declined}` : '') + ` · last: ${last.body}`
        : this.wins && SELLER_LIVE.test(location.pathname)
          ? NO_WINS_HERE
          : '',
    )
  }

  private onRow(row: CapturedRow): void {
    // One numbering for chat lines and player wins, so they never share a seq.
    row.seq = this.log.length + 1
    this.log.push(row)
    this.pending.push(row)
    if (this.matchWord(row.rawText)) this.addFlag(row)
    if (this.findTerm) this.queueFind()
    this.panel.setCount(this.log.length)
    if (this.pending.length >= FLUSH_AT) void this.flush()
    else if (!this.flushTimer) this.flushTimer = setTimeout(() => void this.flush(), FLUSH_MS)
  }

  /** Stop: the only thing that ends a recording. */
  private stopRecording(): Promise<void> {
    const show = this.showRef()
    if (this.spot && show) send({ cmd: 'recording', show, spot: null }).catch(() => {})
    this.spot = null
    this.endCapture()
    document.removeEventListener('click', this.onPickBound, true)
    this.state = this.log.length ? 'stopped' : 'idle'
    return this.flush()
  }

  // ---------- persistence (background owns IndexedDB) ----------
  private showRef(): ShowRef | null {
    return this.startedAt ? { id: this.startedAt, startedAt: this.startedAt, url: location.pathname } : null
  }

  /**
   * Hand pending rows to the background, at most FLUSH_MAX per message and one
   * message at a time, until none are left. A backlog (the background was busy or
   * restarting) drains in chunks instead of growing into a batch it would refuse.
   */
  flush(): Promise<void> {
    if (this.flushTimer) clearTimeout(this.flushTimer)
    this.flushTimer = null
    this.saving ??= this.drain().finally(() => (this.saving = null))
    return this.saving
  }

  private async drain(): Promise<void> {
    while (this.pending.length && this.showRef()) {
      if (!(await this.saveBatch())) return // failed: kept, retried on the next row, flush or timer
    }
  }

  private async saveBatch(): Promise<boolean> {
    const show = this.showRef()!
    const batch = this.pending.slice(0, FLUSH_MAX)
    this.pending = this.pending.slice(batch.length)
    try {
      const reply = await send<{ ok?: boolean; count?: number; upload?: UploadStatus; error?: string }>({
        cmd: 'rows',
        show,
        rows: batch,
      })
      if (reply?.ok) {
        this.synced = reply.count ?? this.synced + batch.length
        if (reply.upload) this.upload = reply.upload
        this.panel.setSync(`saved ${this.synced}` + uploadNote(this.upload))
        this.renderDot()
        return true
      }
      throw new Error(reply?.error ?? 'save failed')
    } catch (err) {
      this.pending = batch.concat(this.pending)
      this.panel.setSync(`${this.pending.length} not saved yet — ${String((err as Error).message ?? err)}`)
      // A quiet chat brings no next row to retry with.
      if (!this.flushTimer && !cutOff()) this.flushTimer = setTimeout(() => void this.flush(), RETRY_MS)
      return false
    }
  }

  async refreshUpload(): Promise<void> {
    try {
      const u = await send<UploadStatus>({ cmd: 'getUploadStatus' })
      this.upload = u
      if (this.log.length) this.panel.setSync(`saved ${this.synced}` + uploadNote(u))
      this.renderDot()
    } catch {
      /* background asleep or reloaded */
    }
  }

  // ---------- download ----------
  private download(label: string): void {
    if (!this.log.length) {
      this.panel.setStatus('Nothing captured yet.', 'bad')
      return
    }
    // A spreadsheet Excel opens as is (shared/csv.ts): one row per message, watch-word hits marked.
    const body = chatCsv(
      this.log.map((r) => ({
        time: csvTime(r.capturedAt),
        username: r.username,
        message: r.body || r.rawText,
        flagged: this.matchWord(r.rawText),
      })),
    )
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([body], { type: 'text/csv' }))
    a.download = `Whatnot_${showDate(this.startedAt)}_${label}.csv`
    a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 5000)
    this.panel.setStatus('Downloaded ' + this.log.length + ' messages.', 'good')
  }

  private clear(): void {
    if (!confirm('Clear the panel and start a new show? This show stays saved in Chat Search.')) return
    void this.flush()
    this.log = []
    this.pending = []
    this.startedAt = null
    this.synced = 0
    send({ cmd: 'clearSession' }).catch(() => {})
    this.state = 'idle'
    this.rebuildFlags()
    this.panel.setStatus('Cleared.')
    this.panel.setSync('')
    this.renderWins()
    this.render()
  }

  // ---------- watch words ----------
  private matchWord(text: string): boolean {
    if (!this.watchWords.length) return false
    const t = text.toLowerCase()
    return this.watchWords.some((w) => t.includes(w))
  }

  private setWords(str: string): void {
    if (this.wordsTimer) clearTimeout(this.wordsTimer)
    this.wordsTimer = null
    const next = str
      .split(',')
      .map((w) => w.trim().toLowerCase())
      .filter(Boolean)
    if (next.join(',') === this.watchWords.join(',')) return // nothing changed: no rebuild, no write
    this.watchWords = next
    chrome.storage.local.set({ [WORDS_KEY]: this.watchWords }).catch(() => {})
    this.rebuildFlags()
  }

  private addFlag(r: CapturedRow): void {
    const fl = this.panel.el('fl')
    fl.querySelector('.empty')?.remove()
    const div = document.createElement('div')
    div.className = 'fi'
    div.innerHTML = '<b></b><span></span>'
    div.querySelector('b')!.textContent = timeOf(r.capturedAt)
    div.querySelector('span')!.textContent = r.rawText
    fl.prepend(div)
    while (fl.children.length > 30) fl.lastChild!.remove()
    this.panel.setFlagCount(fl.querySelectorAll('.fi').length)
  }

  private rebuildFlags(): void {
    const fl = this.panel.el('fl')
    fl.innerHTML = ''
    const hits = this.log.filter((r) => this.matchWord(r.rawText)).slice(-30)
    if (!hits.length) fl.innerHTML = '<div class="empty">Nothing flagged yet.</div>'
    hits.forEach((r) => this.addFlag(r))
    this.panel.setFlagCount(hits.length)
  }

  // ---------- find buyer ----------
  private renderFind(): void {
    const fr = this.panel.el('fr')
    if (!this.findTerm) {
      fr.hidden = true
      fr.innerHTML = ''
      return
    }
    fr.hidden = false
    fr.innerHTML = ''
    const q = this.findTerm
    const hits = this.log
      .filter((r) => r.username.toLowerCase().includes(q) || r.rawText.toLowerCase().includes(q))
      .slice(-15)
      .reverse()
    if (!hits.length) {
      fr.innerHTML = '<div class="empty">No messages from that name yet.</div>'
      return
    }
    for (const r of hits) {
      const div = document.createElement('div')
      div.className = 'fi'
      div.title = 'Click to copy'
      div.innerHTML = '<b></b><span></span>'
      div.querySelector('b')!.textContent = timeOf(r.capturedAt)
      div.querySelector('span')!.textContent = r.rawText
      div.onclick = () => {
        navigator.clipboard.writeText(timeOf(r.capturedAt) + '  ' + r.rawText).then(
          () => this.panel.setStatus('Copied.', 'good'),
          () => {},
        )
      }
      fr.appendChild(div)
    }
  }

  /**
   * At most one refresh per FIND_MS while lines arrive. A throttle, not a debounce: resetting
   * the timer on every line meant a busy chat never let the results refresh at all.
   */
  private queueFind(): void {
    this.findTimer ??= setTimeout(() => {
      this.findTimer = null
      this.renderFind()
    }, FIND_MS)
  }

  // ---------- Whatnot's Shipments page (after the show) ----------
  private shipBusy = false

  /** Read every page of Seller Hub's Shipments table and send it to the web app. */
  private async sendShipments(): Promise<void> {
    if (this.shipBusy) return
    const p = this.panel
    const btn = p.el<HTMLButtonElement>('shipSend')
    this.shipBusy = true
    btn.disabled = true
    p.setShipStatus('Reading the Shipments table…', 'info')
    try {
      const got = await collectAll(document, (n, info) =>
        p.setShipStatus(`Reading page ${n}${info ? ` (${info.to} of ${info.total})` : ''}…`, 'info'),
      )
      if (got.show && 'all' in got.show) {
        p.setShipStatus("Pick this show in Whatnot's show filter first (it says All Shows), then send again.", 'bad')
        return
      }
      if (!got.rows.length) {
        p.setShipStatus(got.stoppedEarly ?? "No shipments on this page. Check Whatnot's show and status filters.", 'bad')
        return
      }
      p.setShipStatus(`Sending ${got.rows.length} shipments…`, 'info')
      const reply = await send<ShipmentsReply | { error: string }>({ cmd: 'shipments', rows: got.rows, show: got.show?.label ?? '' })
      if (!reply || 'error' in reply) {
        p.setShipStatus(reply?.error ?? 'Could not send.', 'bad')
        return
      }
      const parts = [`Sent ${reply.stored} shipment${reply.stored === 1 ? '' : 's'} (${reply.lots} lot${reply.lots === 1 ? '' : 's'})`]
      if (reply.show) parts.push(`filed under the ${reply.show.date} show`)
      if (reply.unmatched) parts.push(`${reply.unmatched} matched no show in Sales Ops`)
      if (got.noLots) parts.push(`${got.noLots} would not open, sent without their lots`)
      if (got.total !== null && got.rows.length < got.total) parts.push(`read ${got.rows.length} of ${got.total}`)
      if (got.stoppedEarly) parts.push(got.stoppedEarly)
      p.setShipStatus(parts.join(' · ') + '.', got.stoppedEarly || reply.unmatched || got.noLots ? 'info' : 'good')
      p.el('shipNote').textContent = "Open the show's Order slips in the web app: it is now the pick list, one row per Whatnot lot with the product the winner chose in chat."
    } catch {
      p.setShipStatus('The extension was reloaded. Refresh this page.', 'bad')
    } finally {
      this.shipBusy = false
      btn.disabled = false
    }
  }

  private renderShip(): void {
    const p = this.panel
    const here = onShipmentsPage(location.pathname)
    p.el('ship').hidden = !here
    // After Stop: point the host at the next step. Not while recording, and not on that page.
    p.el('shipNext').hidden = here || this.spot !== null || this.state !== 'stopped' || !this.log.length
  }

  // ---------- render ----------
  private render(): void {
    this.renderShip()
    const p = this.panel
    const s = this.state
    const recording = this.spot !== null
    const watching = !!this.recorder?.attached
    this.renderDot()
    // One button: Start (or Continue) before, Stop & Save during. While the chat cannot be found the
    // host may point at it again ("Pick chat"); recording does not stop for that. The resume question
    // stands in for the button until it is answered.
    const start = p.el<HTMLButtonElement>('start')
    start.hidden = (recording && watching) || !p.el('resumeRow').hidden
    start.disabled = s === 'picking'
    start.textContent = recording ? 'Pick chat' : this.log.length ? 'Continue' : 'Start'
    p.el('stop').hidden = !recording
    p.el<HTMLButtonElement>('backup').disabled = !this.log.length
    p.el<HTMLButtonElement>('clear').disabled = recording || !this.log.length
    p.setCount(this.log.length)
  }

  /** The dot's colour: whether it records, whether it sees the chat, and how uploads go (status.ts). */
  private renderDot(): void {
    this.panel.setDot(
      dotState({
        picking: this.state === 'picking',
        recording: this.spot !== null,
        watching: !!this.recorder?.attached,
        upload: this.upload,
        cutOff: this.dead,
      }),
    )
  }
}
