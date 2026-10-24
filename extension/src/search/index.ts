// Chat Search — reads shows saved by Chat Scraper (IndexedDB) or a downloaded .txt file.
import type { CapturedRow, ShowMeta } from '../capture/types'
import type { Broadcast } from '../shared/messages'
import { deleteShow, getRows, getShow, listShows } from '../storage/db'

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T

interface Line {
  time: string
  text: string
}
interface Current {
  id: string
  label: string
  lines: Line[]
  updatedAt?: number
  meta?: ShowMeta
}

let index: ShowMeta[] = []
let current: Current | null = null
let fileShow: Current | null = null

const timeOf = (iso: string) => (iso ? new Date(iso).toLocaleTimeString() : '')
const toLines = (rows: CapturedRow[]): Line[] => rows.map((r) => ({ time: timeOf(r.capturedAt), text: r.rawText }))

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
  current = meta ? { id, label: id, lines: toLines(await getRows(id)), updatedAt: meta.updatedAt, meta } : null
  run()
}

const isLive = () => !!current && current.id !== 'file' && !!current.updatedAt && Date.now() - current.updatedAt < 60_000

const esc = (s: string) =>
  s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] as string)

function highlight(text: string, q: string): string {
  if (!q) return esc(text)
  const i = text.toLowerCase().indexOf(q)
  if (i < 0) return esc(text)
  return esc(text.slice(0, i)) + '<mark>' + esc(text.slice(i, i + q.length)) + '</mark>' + highlight(text.slice(i + q.length), q)
}

function run(): void {
  $('live').classList.toggle('on', isLive())
  const res = $('results')
  const sum = $('summary')
  res.innerHTML = ''
  if (!current) {
    sum.textContent = ''
    res.innerHTML = '<div class="empty">No shows saved yet. Start Chat Scraper on a Whatnot show, or load a .txt file.</div>'
    return
  }
  const lines = current.lines
  const q = $<HTMLInputElement>('q').value.trim().toLowerCase()
  if (!q) {
    sum.innerHTML = `<b>${lines.length}</b> messages in this show. Type a buyer name or word to search.`
    return
  }
  const ctx = +$<HTMLSelectElement>('ctx').value
  const hits: number[] = []
  lines.forEach((r, i) => {
    if (r.text.toLowerCase().includes(q)) hits.push(i)
  })
  sum.innerHTML = `<b>${hits.length}</b> message${hits.length === 1 ? '' : 's'} matching "${esc(q)}" out of ${lines.length}.`
  if (!hits.length) {
    res.innerHTML = '<div class="empty">No matches. Try part of the name.</div>'
    return
  }
  // Merge matches that sit close together so context isn't repeated.
  const blocks: { a: number; b: number; hits: number[] }[] = []
  for (const i of hits) {
    const a = Math.max(0, i - ctx)
    const b = Math.min(lines.length - 1, i + ctx)
    const last = blocks[blocks.length - 1]
    if (last && a <= last.b + 1) {
      last.b = b
      last.hits.push(i)
    } else blocks.push({ a, b, hits: [i] })
  }
  const frag = document.createDocumentFragment()
  for (const bl of blocks) {
    const div = document.createElement('div')
    div.className = 'hit'
    const n = bl.hits.length
    div.innerHTML = `<div class="hit-head"><span>${esc(lines[bl.hits[0]!]!.time)}${n > 1 ? ' · ' + n + ' matches' : ''}</span><button>Copy</button></div>`
    for (let i = bl.a; i <= bl.b; i++) {
      const m = bl.hits.includes(i)
      const line = document.createElement('div')
      line.className = 'line' + (m ? ' m' : '')
      line.innerHTML = `<span class="t">${esc(lines[i]!.time)}</span><span>${m ? highlight(lines[i]!.text, q) : esc(lines[i]!.text)}</span>`
      div.appendChild(line)
    }
    div.querySelector('button')!.onclick = () => {
      const txt: string[] = []
      for (let i = bl.a; i <= bl.b; i++) txt.push(lines[i]!.time + '  ' + lines[i]!.text)
      navigator.clipboard.writeText(txt.join('\n')).then(() => toast())
    }
    frag.appendChild(div)
  }
  res.appendChild(frag)
}

let toastTimer: ReturnType<typeof setTimeout> | undefined
function toast(msg?: string): void {
  const t = $('toast')
  t.textContent = msg ?? 'Copied'
  t.classList.add('on')
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => t.classList.remove('on'), 1400)
}

// ---------- .txt files (from Chat Scraper or the old console script) ----------
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
    fileShow = { id: 'file', label: f.name, lines: parseTxt(body) }
    fillShows('file')
    void openShow('file')
  })
  input.value = ''
}

$('dl').onclick = () => {
  if (!current) return
  const body = current.lines.map((r) => r.time + '\t' + r.text).join('\n')
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([body], { type: 'text/plain' }))
  a.download = current.meta ? `Whatnot_${current.meta.date}_show.txt` : current.label
  a.click()
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
// The toolbar icon opens this page off Whatnot, so Settings (pairing) has to be reachable from here.
$('settings').onclick = () => void chrome.runtime.openOptionsPage()
let typing: ReturnType<typeof setTimeout> | undefined
$('q').addEventListener('input', () => {
  clearTimeout(typing)
  typing = setTimeout(run, 200)
})
$('ctx').onchange = run

// Live updates while the show is still recording.
chrome.runtime.onMessage.addListener((msg: Broadcast) => {
  if (msg.cmd !== 'showUpdated') return
  void (async () => {
    index = await listShows()
    const keep = $<HTMLSelectElement>('show').value
    fillShows(keep)
    if (current && current.id === msg.showId) await openShow(msg.showId)
  })()
})
setInterval(() => $('live').classList.toggle('on', isLive()), 15_000)

void loadIndex()
