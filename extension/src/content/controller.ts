import { Recorder } from '../capture/recorder'
import { WinWatcher } from '../capture/player'
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
import { showDate } from '../shared/showDate'
import { type StockSeen, stockAlerts } from '../stock/alerts'
import type { StockReply } from '../stock/fetch'
import { Panel, readLayout } from './panel'

const WORDS_KEY = 'cs_watchwords'
/** Where the host dragged the panel and how big they made it; every Whatnot tab shares it. */
const LAYOUT_KEY = 'cs_panel_layout'
const FLUSH_MS = 1500
const FLUSH_AT = 50
/** Most rows in one message; the background refuses more than 1000. */
const FLUSH_MAX = 500
const RETRY_MS = 3000
const WIN_POLL_MS = 500
/** Stock moves once an upload is parsed (a few seconds), so a slower poll is enough. */
const STOCK_POLL_MS = 8000
/** How often a recording checks the tab is still on the show's page. */
const PAGE_POLL_MS = 1000

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
  private winTimer: ReturnType<typeof setInterval> | null = null
  private stockTimer: ReturnType<typeof setInterval> | null = null
  /** Last stock poll of this recording; null until the first one (the baseline). */
  private stockSeen: StockSeen | null = null
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
    p.el('backup').onclick = () => this.download('backup')
    p.el('settings').onclick = () => send({ cmd: 'openOptions' }).catch(() => {})
    this.uploadTimer = setInterval(() => (cutOff() ? this.cutOff() : void this.refreshUpload()), 5000)
    p.el('clear').onclick = () => this.clear()
    p.el<HTMLInputElement>('find').addEventListener('input', (e) => {
      this.findTerm = (e.target as HTMLInputElement).value.trim().toLowerCase()
      this.renderFind()
    })
    p.el<HTMLInputElement>('words').addEventListener('change', (e) => this.setWords((e.target as HTMLInputElement).value))
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
    for (const t of [this.uploadTimer, this.flushTimer, this.findTimer]) if (t) clearTimeout(t)
    document.removeEventListener('click', this.onPickBound, true)
    window.removeEventListener('beforeunload', this.onUnload)
    this.panel.host.remove()
  }

  /** Cut off with no newer copy injected (it failed, or Chrome blocked it): say what to do. */
  private cutOff(): void {
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
      if (ev.type === 'row') return this.onRow(ev.row)
      if (ev.type === 'healed') {
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
    // Whole show: whatever chat is still on screen is already saved. Wins are not chat lines.
    const known = this.log.filter((r) => r.source !== 'player').map((r) => r.rawText)
    if (box) rec.start(box, spot.selectors[0]!, this.log.length, known)
    else rec.resume(document, spot.selectors, this.log.length, known)
    // Whatnot shows who won what only in the video player, never in the chat: read it too.
    this.wins = new WinWatcher(this.startedAt!)
    this.wins.remember(this.log)
    this.winTimer = setInterval(() => this.checkPlayer(), WIN_POLL_MS)
    this.checkPlayer()
    this.stockTimer = setInterval(() => void this.checkStock(), STOCK_POLL_MS)
    void this.checkStock()
    if (rec.attached) this.panel.setStatus('Watching chat.', 'good')
    else this.panel.setStatus('Recording. Waiting for the chat to show on this page.', 'info')
  }

  /** Stop reading the page (chat, player, stock). The recording itself goes on while `spot` is set. */
  private endCapture(): void {
    this.recorder?.stop()
    this.recorder = null
    if (this.winTimer) clearInterval(this.winTimer)
    this.winTimer = null
    this.wins = null
    this.stopStock()
    this.playerObserver?.disconnect()
    this.playerObserver = null
    this.observedPlayer = null
    if (!this.spot && this.pageTimer) {
      clearInterval(this.pageTimer)
      this.pageTimer = null
    }
  }

  private checkPlayer(): void {
    if (!this.wins) return // not recording (a late observer callback after Stop)
    this.watchPlayer()
    const row = this.wins.check(document)
    if (!row) return
    this.onRow(row)
    this.renderWins()
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

  private stopStock(): void {
    if (this.stockTimer) clearInterval(this.stockTimer)
    this.stockTimer = null
    this.stockSeen = null
    this.panel.setLowStock([])
    this.panel.clearStockAlert()
  }

  private renderWins(): void {
    const wins = this.log.filter((r) => r.source === 'player')
    const last = wins[wins.length - 1]
    this.panel.setWins(last ? `wins ${wins.length} · last: ${last.body}` : '')
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
        this.panel.setSync(`saved ${this.synced}` + this.uploadNote(reply.upload))
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

  private uploadNote(u?: UploadStatus): string {
    if (!u) return ''
    if (!u.configured) return ' · not uploading (connect this laptop in Settings)'
    if (u.lastError) return ` · ${u.pending} to upload — ${u.lastError}`
    return u.pending ? ` · ${u.pending} to upload` : ' · uploaded'
  }

  async refreshUpload(): Promise<void> {
    try {
      const u = await send<UploadStatus>({ cmd: 'getUploadStatus' })
      if (this.log.length) this.panel.setSync(`saved ${this.synced}` + this.uploadNote(u))
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
    const flagged = this.log.filter((r) => this.matchWord(r.rawText))
    const line = (r: CapturedRow) => timeOf(r.capturedAt) + '\t' + r.rawText
    const header = [
      '# Whatnot show chat — ' + showDate(this.startedAt),
      '# Started: ' + (this.startedAt ? new Date(this.startedAt).toLocaleString() : 'unknown'),
      '# Saved:   ' + new Date().toLocaleString(),
      '# Messages: ' + this.log.length,
      '',
    ]
    const footer = flagged.length
      ? ['', '# FLAGGED (' + this.watchWords.join(', ') + ')', ...flagged.map((r) => '# ' + line(r))]
      : []
    const body = [...header, ...this.log.map(line), ...footer].join('\n')
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([body], { type: 'text/plain' }))
    a.download = `Whatnot_${showDate(this.startedAt)}_${label}.txt`
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
    this.watchWords = str
      .split(',')
      .map((w) => w.trim().toLowerCase())
      .filter(Boolean)
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
  }

  private rebuildFlags(): void {
    const fl = this.panel.el('fl')
    fl.innerHTML = ''
    const hits = this.log.filter((r) => this.matchWord(r.rawText)).slice(-30)
    if (!hits.length) fl.innerHTML = '<div class="empty">Nothing flagged yet.</div>'
    hits.forEach((r) => this.addFlag(r))
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

  private queueFind(): void {
    if (this.findTimer) clearTimeout(this.findTimer)
    this.findTimer = setTimeout(() => this.renderFind(), 300)
  }

  // ---------- render ----------
  private render(): void {
    const p = this.panel
    const s = this.state
    const recording = this.spot !== null
    const watching = !!this.recorder?.attached
    p.setDot(s === 'picking' ? 'picking' : recording ? (watching ? 'recording' : 'warn') : 'idle')
    // While the chat cannot be found the host may point at it again; recording does not stop for that.
    p.el<HTMLButtonElement>('start').disabled = s === 'picking' || watching
    p.el('start').textContent = recording ? (watching ? 'Start' : 'Pick chat') : this.log.length ? 'Continue' : 'Start'
    p.el<HTMLButtonElement>('stop').disabled = !recording
    p.el<HTMLButtonElement>('backup').disabled = !this.log.length
    p.el<HTMLButtonElement>('clear').disabled = recording || !this.log.length
    p.setCount(this.log.length)
  }
}
