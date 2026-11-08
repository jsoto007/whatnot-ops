// Chat Search — reads shows saved by Chat Scraper (IndexedDB) or a file it downloaded (.csv now, .txt before).
// Chat lines are whatever viewers typed: they reach the page only as text (textContent, text nodes).
import type { CapturedRow, ShowMeta } from '../capture/types'
import { chatCsv, csvTime, parseChatCsv } from '../shared/csv'
import type { Broadcast } from '../shared/messages'
import { isExtensionPage, showOpenedAsFile } from '../shared/openedAsFile'
import { deleteShow, getRows, getShow, listShows, rowsAfter } from '../storage/db'

/** Matches drawn at a time; "Show more" draws the next lot. One letter typed used to draw the whole show. */
const PAGE = 500

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T

interface Line {
  time: string
  /** The whole line as chat showed it (username and message), what a search looks through. */
  text: string
  /** Username and message apart, and the capture time, for the CSV download (saved shows and CSV files have them). */
  user?: string
  body?: string
  iso?: string
}
interface Current {
  id: string
  label: string
  lines: Line[]
  updatedAt?: number
  meta?: ShowMeta
  /** Highest seq read (saved shows; -1 for none): a live update reads only the rows after it. */
  lastSeq: number
}

/** One card of results: matches close together share one, with the lines around them. */
interface Block {
  div: HTMLDivElement
  head: HTMLSpanElement
  /** First line in the card, and the last one drawn so far. */
  a: number
  b: number
  /** Matches in it, and the first one's time (the card's title). */
  n: number
  time: string
}

/** The results on screen, kept so more matches and live lines are added instead of redrawing everything. */
interface View {
  q: string
  ctx: number
  lines: Line[]
  /** Every matching line, in order. The first `shown` are drawn, never more than `limit`. */
  hits: number[]
  shown: number
  limit: number
  /** The newest card: a match close enough to it joins it. */
  last: Block | null
}

let index: ShowMeta[] = []
let current: Current | null = null
let fileShow: Current | null = null
let view: View | null = null

const timeOf = (iso: string) => (iso ? new Date(iso).toLocaleTimeString() : '')
const toLine = (r: CapturedRow): Line => ({ time: timeOf(r.capturedAt), text: r.rawText, user: r.username, body: r.body, iso: r.capturedAt })

function prettyDate(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y!, m! - 1, d).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}
const startTime = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })

function fillShows(selectId?: string): void {
  const sel = $<HTMLSelectElement>('show')
  sel.innerHTML = ''
  if (fileShow) sel.add(new Option('File: ' + fileShow.label, 'file'))
  index.forEach((s) =>
    sel.add(new Option(`${prettyDate(s.date)} show (started ${startTime(s.startedAt)}) · ${s.count} msgs`, s.id)),
  )
  if (!sel.options.length) sel.add(new Option('No saved shows yet', ''))
  if (selectId && [...sel.options].some((o) => o.value === selectId)) sel.value = selectId
}

async function loadIndex(selectId?: string): Promise<void> {
  index = await listShows()
  fillShows(selectId || current?.id || index[0]?.id)
  await openShow($<HTMLSelectElement>('show').value)
}

async function openShow(id: string): Promise<void> {
  if (id === 'file') {
    current = fileShow
    run()
    return
  }
  if (!id) {
    current = null
    run()
    return
  }
  const meta = await getShow(id)
  const rows = meta ? await getRows(id) : []
  current = meta
    ? { id, label: id, lines: rows.map(toLine), updatedAt: meta.updatedAt, meta, lastSeq: rows[rows.length - 1]?.seq ?? -1 }
    : null
  run()
}

const isLive = () => !!current && current.id !== 'file' && !!current.updatedAt && Date.now() - current.updatedAt < 60_000

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = ''): HTMLElementTagNameMap[K] =>
  Object.assign(document.createElement(tag), { className, textContent: text })

/** `text` into `into`, each occurrence of `q` (lower case) in a <mark>. */
function highlight(into: HTMLElement, text: string, q: string): void {
  const low = text.toLowerCase()
  let at = 0
  for (let i = low.indexOf(q); i >= 0 && q; i = low.indexOf(q, at)) {
    if (i > at) into.append(text.slice(at, i))
    into.append(el('mark', '', text.slice(i, i + q.length)))
    at = i + q.length
  }
  if (at < text.length) into.append(text.slice(at))
}

/** Search the whole show again (a new query, show or context size). */
function run(): void {
  $('live').classList.toggle('on', isLive())
  const res = $('results')
  view = null
  res.replaceChildren()
  if (!current) {
    res.append(el('div', 'empty', 'No shows saved yet. Start Chat Scraper on a Whatnot show, or load a saved file.'))
  } else {
    const q = $<HTMLInputElement>('q').value.trim().toLowerCase()
    if (q) {
      const lines = current.lines
      const hits: number[] = []
      for (let i = 0; i < lines.length; i++) if (lines[i]!.text.toLowerCase().includes(q)) hits.push(i)
      view = { q, ctx: +$<HTMLSelectElement>('ctx').value, lines, hits, shown: 0, limit: PAGE, last: null }
      if (hits.length) draw()
      else res.append(el('div', 'empty', 'No matches. Try part of the name.'))
    }
  }
  summarize()
}

/** How many match, and "Show more" while some are not drawn. */
function summarize(): void {
  const sum = $('summary')
  const more = $('more')
  more.hidden = true
  if (!current) return sum.replaceChildren()
  const total = current.lines.length
  if (!view) return sum.replaceChildren(el('b', '', String(total)), ' messages in this show. Type a buyer name or word to search.')
  const { hits, shown, q } = view
  const n = hits.length
  sum.replaceChildren(
    el('b', '', String(n)),
    ` message${n === 1 ? '' : 's'} matching "${q}" out of ${total}.` + (shown < n ? ` Showing the first ${shown}.` : ''),
  )
  if (shown < n) {
    more.hidden = false
    $('moreBtn').textContent = `Show ${Math.min(PAGE, n - shown)} more`
  }
}

const title = (bl: Block) => bl.time + (bl.n > 1 ? ` · ${bl.n} matches` : '')

/** A new card that starts at line `a`, for the match on line `h`. */
function card(v: View, a: number, h: number): Block {
  const div = el('div', 'hit')
  const headRow = el('div', 'hit-head')
  const head = el('span')
  const copy = el('button', '', 'Copy')
  headRow.append(head, copy)
  div.append(headRow)
  const bl: Block = { div, head, a, b: a - 1, n: 1, time: v.lines[h]!.time }
  head.textContent = title(bl)
  copy.onclick = () => {
    const txt: string[] = []
    for (let i = bl.a; i <= bl.b; i++) txt.push(v.lines[i]!.time + '  ' + v.lines[i]!.text)
    void navigator.clipboard.writeText(txt.join('\n')).then(() => toast())
  }
  return bl
}

function addLine(v: View, bl: Block, i: number, hit: boolean): void {
  const ln = v.lines[i]!
  const row = el('div', hit ? 'line m' : 'line')
  const text = el('span')
  if (hit) highlight(text, ln.text, v.q)
  else text.textContent = ln.text
  row.append(el('span', 't', ln.time), text)
  bl.div.append(row)
  bl.b = i
}

/** Context lines after what the card has drawn, up to line `to`. */
function addLines(v: View, bl: Block, to: number): void {
  for (let i = bl.b + 1; i <= to; i++) addLine(v, bl, i, false)
}

/** The lines after match `k` (drawn last), up to `ctx`, stopping before the next match: it is drawn in its turn. */
function trail(v: View, k: number): void {
  const next = v.hits[k + 1] ?? Infinity
  addLines(v, v.last!, Math.min(v.hits[k]! + v.ctx, next - 1, v.lines.length - 1))
}

/** Draw matches up to the limit, carrying on from the last one drawn. */
function draw(): void {
  const v = view!
  const res = $('results')
  const end = Math.min(v.hits.length, v.limit)
  if (v.shown >= end) return
  res.querySelector(':scope > .empty')?.remove()
  const frag = document.createDocumentFragment()
  for (; v.shown < end; v.shown++) {
    const h = v.hits[v.shown]!
    let bl = v.last
    if (bl && h - v.ctx <= bl.b + 1) {
      bl.n++
      bl.head.textContent = title(bl)
    } else {
      bl = card(v, Math.max(h - v.ctx, (bl?.b ?? -1) + 1), h)
      frag.append(bl.div)
      v.last = bl
    }
    addLines(v, bl, h - 1)
    addLine(v, bl, h, true)
    trail(v, v.shown)
  }
  res.append(frag)
}

/** New lines of the open show (live): searched and drawn on their own, the rest stays as it is. */
function append(cur: Current, rows: CapturedRow[]): void {
  const from = cur.lines.length
  for (const r of rows) cur.lines.push(toLine(r))
  cur.lastSeq = rows[rows.length - 1]?.seq ?? cur.lastSeq
  const v = view
  if (v && v.lines === cur.lines) {
    const caughtUp = v.shown === v.hits.length
    for (let i = from; i < cur.lines.length; i++) if (cur.lines[i]!.text.toLowerCase().includes(v.q)) v.hits.push(i)
    // The newest match drawn was near the end of the show: it may be owed lines after it.
    if (caughtUp && v.shown) trail(v, v.shown - 1)
    draw()
  }
  summarize()
}

/** One live refresh at a time; broadcasts that arrive meanwhile fold into one more. */
let refreshing: Promise<void> | null = null
let refreshAgain = false
function liveUpdate(): void {
  if (refreshing) {
    refreshAgain = true
    return
  }
  refreshing = refresh()
    .catch(() => {})
    .finally(() => {
      refreshing = null
      if (refreshAgain) {
        refreshAgain = false
        liveUpdate()
      }
    })
}

async function refresh(): Promise<void> {
  index = await listShows()
  fillShows($<HTMLSelectElement>('show').value)
  const cur = current
  if (!cur || cur.id === 'file') return
  const { meta, rows } = await rowsAfter(cur.id, cur.lastSeq)
  if (cur !== current) return // another show was opened meanwhile
  if (!meta || cur.lines.length + rows.length !== meta.count) {
    // Deleted, or lines that did not arrive in seq order: read the show again.
    await openShow(cur.id)
    return
  }
  cur.updatedAt = meta.updatedAt
  cur.meta = meta
  if (rows.length) append(cur, rows)
  $('live').classList.toggle('on', isLive())
}

let toastTimer: ReturnType<typeof setTimeout> | undefined
function toast(msg?: string): void {
  const t = $('toast')
  t.textContent = msg ?? 'Copied'
  t.classList.add('on')
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => t.classList.remove('on'), 1400)
}

// ---------- saved files: the CSV Chat Scraper writes now, the .txt it wrote before (and the old console script) ----------
export function parseFile(body: string): Line[] {
  const csv = parseChatCsv(body)
  if (csv) return csv.map((l) => ({ time: l.time, text: l.username ? `${l.username} ${l.message}` : l.message, user: l.username, body: l.message }))
  return parseTxt(body)
}

export function parseTxt(body: string): Line[] {
  return body
    .split(/\r?\n/)
    .filter((l) => l.trim() && !l.startsWith('#'))
    .map((l) => {
      const tab = l.indexOf('\t')
      return tab > -1 ? { time: l.slice(0, tab), text: l.slice(tab + 1) } : { time: '', text: l }
    })
}

$('load').onclick = () => $('file').click()
$<HTMLInputElement>('file').onchange = (e) => {
  const input = e.target as HTMLInputElement
  const f = input.files?.[0]
  if (!f) return
  f.text().then((body) => {
    fileShow = { id: 'file', label: f.name, lines: parseFile(body), lastSeq: -1 }
    fillShows('file')
    void openShow('file')
  })
  input.value = ''
}

$('dl').onclick = () => {
  if (!current) return
  // A spreadsheet Excel opens as is (shared/csv.ts). A line from an old .txt has only its time and text.
  const body = chatCsv(current.lines.map((l) => ({ time: l.iso ? csvTime(l.iso) : l.time, username: l.user ?? '', message: l.body ?? l.text })))
  const url = URL.createObjectURL(new Blob([body], { type: 'text/csv' }))
  const a = document.createElement('a')
  a.href = url
  a.download = current.meta ? `Whatnot_${current.meta.date}_show.csv` : current.label.replace(/\.(txt|csv)$/i, '') + '.csv'
  a.click()
  // The download has its own copy by then; otherwise the whole show stays in memory until the tab closes.
  setTimeout(() => URL.revokeObjectURL(url), 5000)
}

$('del').onclick = async () => {
  if (!current || current.id === 'file') {
    fileShow = null
    current = null
    await loadIndex()
    return
  }
  if (!confirm('Delete this saved show from Chat Search? Download it first if you might need it.')) return
  await deleteShow(current.id)
  current = null
  await loadIndex()
  toast('Deleted')
}

$<HTMLSelectElement>('show').onchange = (e) => void openShow((e.target as HTMLSelectElement).value)
$('go').onclick = run
$('moreBtn').onclick = () => {
  if (!view) return
  view.limit += PAGE
  draw()
  summarize()
}
// The toolbar icon opens this page off Whatnot, so Settings (pairing) has to be reachable from here.
$('settings').onclick = () => void chrome.runtime.openOptionsPage()
let typing: ReturnType<typeof setTimeout> | undefined
$('q').addEventListener('input', () => {
  clearTimeout(typing)
  typing = setTimeout(run, 200)
})
$('ctx').onchange = run

if (isExtensionPage()) {
  // Live updates while the show is still recording.
  chrome.runtime.onMessage.addListener((msg: Broadcast) => {
    if (msg.cmd === 'showUpdated') liveUpdate()
  })
  setInterval(() => $('live').classList.toggle('on', isLive()), 15_000)

  void loadIndex()
} else {
  // A file page's IndexedDB is not the extension's: it would show an empty list as if nothing was saved.
  $('settings').hidden = true
  showOpenedAsFile('Chat Search', 'see the shows saved on this laptop')
}
