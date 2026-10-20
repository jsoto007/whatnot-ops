import { Recorder } from '../capture/recorder'
import { findRow, buildSelector, scrollParent } from '../capture/row'
import type { CapturedRow } from '../capture/types'
import type { Request, SessionReply, ShowRef, UploadStatus } from '../shared/messages'
import { showDate } from '../shared/showDate'
import { Panel } from './panel'

const WORDS_KEY = 'cs_watchwords'
const FLUSH_MS = 1500
const FLUSH_AT = 50

type State = 'idle' | 'picking' | 'recording' | 'stopped'

const timeOf = (iso: string) => new Date(iso).toLocaleTimeString()

function send<T = unknown>(msg: Request): Promise<T> {
  return chrome.runtime.sendMessage(msg) as Promise<T>
}

/** Wires the panel, the Recorder and the background store together. */
export class Controller {
  readonly panel = new Panel()
  private log: CapturedRow[] = []
  private state: State = 'idle'
  private startedAt: string | null = null
  private recorder: Recorder | null = null
  private pending: CapturedRow[] = []
  private flushTimer: ReturnType<typeof setTimeout> | null = null
  private synced = 0
  private watchWords: string[] = []
  private findTerm = ''
  private findTimer: ReturnType<typeof setTimeout> | null = null
  private onPickBound = (e: MouseEvent) => this.onPick(e)

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
      this.stopRecording()
      this.download('show')
      this.render()
    }
    p.el('search').onclick = () => {
      void this.flush().finally(() => send({ cmd: 'openSearch' }).catch(() => {}))
    }
    p.el('backup').onclick = () => this.download('backup')
    p.el('settings').onclick = () => send({ cmd: 'openOptions' }).catch(() => {})
    setInterval(() => void this.refreshUpload(), 5000)
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
    p.el('fresh').onclick = () => {
      this.log = []
      this.startedAt = null
      send({ cmd: 'clearSession' }).catch(() => {})
      p.el('resumeRow').hidden = true
      this.rebuildFlags()
      p.setStatus('Ready. Press Start.')
      this.render()
    }
    window.addEventListener('beforeunload', () => void this.flush())
  }

  async init(): Promise<void> {
    this.panel.mount()
    try {
      const words = await chrome.storage.local.get(WORDS_KEY)
      if (Array.isArray(words[WORDS_KEY])) {
        this.watchWords = words[WORDS_KEY] as string[]
        this.panel.el<HTMLInputElement>('words').value = this.watchWords.join(', ')
      }
      const session = await send<SessionReply>({ cmd: 'getSession' })
      if (session && session.rows.length) {
        this.log = session.rows
        this.startedAt = session.show.id
        this.synced = session.rows.length
        this.panel.el('resumeRow').hidden = false
        this.panel.setStatus('Found ' + this.log.length + ' saved messages from earlier. Resume or start fresh?', 'info')
        this.rebuildFlags()
      }
    } catch {
      this.panel.setStatus('Extension was reloaded. Refresh this page.', 'bad')
    }
    this.render()
  }

  toggle(): void {
    this.panel.toggle()
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
    const box = scrollParent(target)
    const row = findRow(target, box)
    if (!row) {
      this.panel.setStatus("Couldn't recognise that as a chat message. Press Start and click a different message.", 'bad')
      this.state = this.log.length ? 'stopped' : 'idle'
      this.render()
      return
    }
    const selector = buildSelector(row)
    // The chat may not overflow yet (a show that just started): never watch the
    // whole body, fall back to the list the row sits in.
    const container = box === document.body ? (row.parentElement ?? box) : box
    ;(container as HTMLElement).style.outline = '3px solid #69BE28'
    setTimeout(() => ((container as HTMLElement).style.outline = ''), 4000)
    this.startRecording(container, selector)
  }

  private startRecording(box: Element, selector: string): void {
    this.startedAt ??= new Date().toISOString()
    this.recorder?.stop()
    this.recorder = new Recorder(this.startedAt, { ignore: (n) => this.panel.contains(n) })
    this.recorder.on((ev) => {
      if (ev.type === 'row') this.onRow(ev.row)
      else if (ev.type === 'healed') {
        this.panel.setStatus('Chat re-rendered; found the messages again.', 'good')
        this.panel.setDot('recording')
      } else {
        this.panel.setStatus('Lost the chat (' + ev.reason + '). Press Start and click a message.', 'bad')
        this.panel.setDot('warn')
      }
    })
    const known = this.log.map((r) => r.rawText) // whole show: whatever is still on screen is already saved
    this.recorder.start(box, selector, this.log.length, known)
    this.state = 'recording'
    this.panel.setStatus('Watching chat.', 'good')
    this.render()
  }

  private onRow(row: CapturedRow): void {
    this.log.push(row)
    this.pending.push(row)
    if (this.matchWord(row.rawText)) this.addFlag(row)
    if (this.findTerm) this.queueFind()
    this.panel.setCount(this.log.length)
    if (this.pending.length >= FLUSH_AT) void this.flush()
    else if (!this.flushTimer) this.flushTimer = setTimeout(() => void this.flush(), FLUSH_MS)
  }

  private stopRecording(): void {
    this.recorder?.stop()
    this.recorder = null
    document.removeEventListener('click', this.onPickBound, true)
    this.state = this.log.length ? 'stopped' : 'idle'
    void this.flush()
  }

  // ---------- persistence (background owns IndexedDB) ----------
  private showRef(): ShowRef | null {
    return this.startedAt ? { id: this.startedAt, startedAt: this.startedAt, url: location.pathname } : null
  }

  async flush(): Promise<void> {
    if (this.flushTimer) clearTimeout(this.flushTimer)
    this.flushTimer = null
    const show = this.showRef()
    if (!show || !this.pending.length) return
    const batch = this.pending
    this.pending = []
    try {
      const reply = await send<{ ok?: boolean; count?: number; upload?: UploadStatus; error?: string }>({
        cmd: 'rows',
        show,
        rows: batch,
      })
      if (reply?.ok) {
        this.synced = reply.count ?? this.synced + batch.length
        this.panel.setSync(`saved ${this.synced}` + this.uploadNote(reply.upload))
      } else throw new Error(reply?.error ?? 'save failed')
    } catch (err) {
      this.pending = batch.concat(this.pending)
      this.panel.setSync(`${this.pending.length} not saved yet — ${String((err as Error).message ?? err)}`)
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
    p.setDot(s === 'recording' ? 'recording' : s === 'picking' ? 'picking' : 'idle')
    p.el<HTMLButtonElement>('start').disabled = s === 'recording' || s === 'picking'
    p.el('start').textContent = this.log.length && s !== 'recording' ? 'Continue' : 'Start'
    p.el<HTMLButtonElement>('stop').disabled = s !== 'recording'
    p.el<HTMLButtonElement>('backup').disabled = !this.log.length
    p.el<HTMLButtonElement>('clear').disabled = s === 'recording' || !this.log.length
    p.setCount(this.log.length)
  }
}
