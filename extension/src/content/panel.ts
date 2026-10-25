import { leftText, type StockAlert, type StockItem } from '../stock/alerts'

/** The floating panel, in a shadow root so Whatnot's CSS cannot touch it. */
export const HOST_ID = 'cs-host'
/** How long a low-stock alert stays up unless clicked away. */
const ALERT_MS = 20_000
const LOW_ROWS = 5

const HTML = `
  <style>
    :host { all: initial; }
    /* .row and friends set display, which would otherwise beat the hidden attribute. */
    [hidden] { display: none !important; }
    /* Liquid Glass, dark tint: white text stays readable over any stream behind it. */
    .p { width: 336px; box-sizing: border-box; padding: 12px; display: grid; gap: 10px;
         font: 14px/1.4 -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', system-ui, sans-serif; letter-spacing: -0.005em; color: #fff;
         background: rgba(30, 26, 52, 0.62); -webkit-backdrop-filter: blur(36px) saturate(200%); backdrop-filter: blur(36px) saturate(200%);
         border: 1px solid rgba(255, 255, 255, 0.28); border-radius: 32px;
         box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.35), 0 24px 70px rgba(10, 0, 40, 0.45); }
    .hd { display: flex; align-items: center; gap: 10px; padding: 4px 4px 0 8px; cursor: default; }
    .dot { width: 10px; height: 10px; border-radius: 50%; background: rgba(255, 255, 255, 0.55); flex: none; transition: background 200ms ease; }
    .dot.on { background: #ff453a; box-shadow: 0 0 0 5px rgba(255, 69, 58, 0.3); animation: pulse 1.4s infinite; }
    .dot.pick { background: #64d2ff; box-shadow: 0 0 0 5px rgba(100, 210, 255, 0.25); }
    .dot.warn { background: #ff9f0a; box-shadow: 0 0 0 5px rgba(255, 159, 10, 0.3); }
    @keyframes pulse { 50% { opacity: .35; } }
    @media (prefers-reduced-motion: reduce) { .dot.on { animation: none; } }
    .ttl { font-weight: 700; font-size: 17px; letter-spacing: -0.01em; flex: 1; }
    .cnt { font-variant-numeric: tabular-nums; font-weight: 600; font-size: 13px; padding: 4px 10px; border-radius: 999px; background: rgba(255, 255, 255, 0.16); }
    .x { width: 36px; height: 36px; flex: none; border-radius: 50%; border: 1px solid rgba(255, 255, 255, 0.35); background: rgba(255, 255, 255, 0.14);
         color: #fff; font: 600 18px/1 system-ui, sans-serif; cursor: pointer; display: grid; place-items: center; padding: 0; }
    .x:hover { background: rgba(255, 255, 255, 0.24); }
    .bd { display: grid; gap: 10px; }
    .bd.min { display: none; }
    .card { display: grid; gap: 4px; padding: 14px 16px; border-radius: 22px; background: rgba(10, 6, 30, 0.38); border: 1px solid rgba(255, 255, 255, 0.16); }
    .st { font-size: 15px; font-weight: 600; min-height: 20px; color: #fff; }
    .st.good { color: #7cf29a; } .st.bad { color: #ff8a80; } .st.info { color: #8fdcff; }
    .sync { font-size: 13px; color: rgba(255, 255, 255, 0.82); }
    .sync:empty { display: none; }
    .row { display: flex; gap: 8px; flex-wrap: wrap; }
    button.b { flex: 1; min-height: 44px; border-radius: 999px; padding: 0 10px; white-space: nowrap; font: 600 14px -apple-system, BlinkMacSystemFont, system-ui, sans-serif; cursor: pointer;
               border: 1px solid #fff; background: #fff; color: #1a1033; box-shadow: 0 6px 18px rgba(0, 0, 0, 0.22); transition: filter 160ms ease, transform 160ms ease; }
    button.b:hover { filter: brightness(0.95); }
    button.b:active { transform: scale(0.98); }
    button.b.sec { background: rgba(255, 255, 255, 0.16); color: #fff; border-color: rgba(255, 255, 255, 0.32); box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.3); }
    button.b.sec:hover { background: rgba(255, 255, 255, 0.24); filter: none; }
    button.b:disabled { opacity: .4; cursor: default; transform: none; }
    button:focus-visible, input:focus-visible { outline: 2px solid #8fdcff; outline-offset: 2px; }
    .tools { gap: 4px; padding: 4px; border-radius: 999px; background: rgba(10, 6, 30, 0.3); border: 1px solid rgba(255, 255, 255, 0.16); }
    .tools button.b { min-height: 38px; font-size: 13px; padding: 0 8px; background: transparent; border-color: transparent; color: #fff; box-shadow: none; }
    .tools button.b:hover { background: rgba(255, 255, 255, 0.16); filter: none; }
    label { font-size: 12px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: rgba(255, 255, 255, 0.82); display: block; margin: 0 0 6px 12px; }
    input { width: 100%; box-sizing: border-box; min-height: 42px; border: 1px solid rgba(255, 255, 255, 0.22); background: rgba(0, 0, 0, 0.24); color: #fff;
            border-radius: 999px; padding: 0 16px; font: 14px -apple-system, BlinkMacSystemFont, system-ui, sans-serif; }
    input::placeholder { color: rgba(255, 255, 255, 0.6); }
    .fl { max-height: 150px; overflow-y: auto; display: grid; border-radius: 18px; background: rgba(10, 6, 30, 0.34); border: 1px solid rgba(255, 255, 255, 0.16); }
    .fi { padding: 9px 14px; font-size: 13px; word-break: break-word; border-top: 1px solid rgba(255, 255, 255, 0.1); }
    .fi:first-child { border-top: 0; }
    .fi b { color: rgba(255, 255, 255, 0.7); font-weight: 600; margin-right: 6px; font-variant-numeric: tabular-nums; }
    #fl { background: rgba(255, 159, 10, 0.22); border-color: rgba(255, 214, 140, 0.38); }
    #fr .fi { cursor: pointer; }
    #fr .fi:hover { background: rgba(255, 255, 255, 0.1); }
    #fr { margin-top: 8px; }
    .empty { font-size: 13px; color: rgba(255, 255, 255, 0.78); padding: 10px 14px; }
    /* Low stock sits outside .bd so it still shows when the panel is minimised. */
    .al { padding: 12px 16px; border-radius: 22px; background: rgba(255, 159, 10, 0.92); color: #1a1033; border: 1px solid rgba(255, 214, 140, 0.6);
          font-weight: 700; font-size: 15px; cursor: pointer; box-shadow: 0 6px 18px rgba(0, 0, 0, 0.25); animation: flash .5s 4 alternate; }
    .al.urgent { background: #ff453a; color: #fff; border-color: rgba(255, 160, 150, 0.7); }
    .al div + div { margin-top: 2px; }
    @keyframes flash { to { opacity: .45; } }
    @media (prefers-reduced-motion: reduce) { .al { animation: none; } }
    .lw { display: grid; gap: 4px; padding: 12px 16px; border-radius: 22px; background: rgba(10, 6, 30, 0.38); border: 1px solid rgba(255, 159, 10, 0.45); }
    .lw .lt { font-size: 12px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: rgba(255, 255, 255, 0.82); }
    .lw .li { display: flex; gap: 8px; font-size: 13px; }
    .lw .li span:first-child { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .lw .n { font-weight: 700; color: #ffb340; font-variant-numeric: tabular-nums; }
    .lw .n.urgent { color: #ff8a80; }
  </style>
  <div class="p">
    <div class="hd">
      <span class="dot" id="dot"></span><span class="ttl">Chat Scraper</span>
      <span class="cnt" id="cnt" title="Messages captured">0</span>
      <button class="x" id="min" title="Minimise" aria-label="Minimise">–</button>
    </div>
    <div class="al" id="alert" role="alert" title="Click to dismiss" hidden></div>
    <div class="lw" id="low" hidden></div>
    <div class="bd" id="bd">
      <div class="card">
        <div class="st" id="st">Ready. Open the show, then press Start.</div>
        <div class="sync" id="sync"></div>
        <div class="sync" id="wins" hidden></div>
      </div>
      <div class="row" id="resumeRow" hidden>
        <button class="b" id="resume">Resume last show</button>
        <button class="b sec" id="fresh">Start fresh</button>
      </div>
      <div class="row">
        <button class="b" id="start">Start</button>
        <button class="b" id="stop">Stop &amp; Save</button>
      </div>
      <div class="row">
        <button class="b sec" id="search">Open Chat Search</button>
        <button class="b sec" id="settings" title="Connect this laptop to your shop">Settings</button>
      </div>
      <div class="row tools">
        <button class="b sec" id="backup">Save backup copy</button>
        <button class="b sec" id="clear">Clear</button>
      </div>
      <div>
        <label for="find">Find buyer (live)</label>
        <input id="find" placeholder="type a username">
        <div class="fl" id="fr" hidden></div>
      </div>
      <div>
        <label for="words">Watch words / usernames (comma separated)</label>
        <input id="words" placeholder="mine, sold, claim">
      </div>
      <div>
        <label>Flagged messages</label>
        <div class="fl" id="fl"><div class="empty">Nothing flagged yet.</div></div>
      </div>
    </div>
  </div>`

export type StatusKind = 'good' | 'bad' | 'info' | ''

export class Panel {
  readonly host: HTMLDivElement
  private readonly root: ShadowRoot
  private alertTimer: ReturnType<typeof setTimeout> | null = null

  constructor(doc: Document = document) {
    this.host = doc.createElement('div')
    this.host.id = HOST_ID
    this.host.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483647;'
    this.root = this.host.attachShadow({ mode: 'open' })
    this.root.innerHTML = HTML
    this.el('min').onclick = () => {
      const bd = this.el('bd')
      bd.classList.toggle('min')
      this.el('min').textContent = bd.classList.contains('min') ? '+' : '–'
    }
    this.el('alert').onclick = () => this.clearStockAlert()
  }

  mount(doc: Document = document): void {
    doc.documentElement.appendChild(this.host)
  }

  contains(node: Node): boolean {
    return node === this.host || this.host.contains(node)
  }

  el<T extends HTMLElement = HTMLElement>(id: string): T {
    return this.root.getElementById(id) as T
  }

  setStatus(msg: string, kind: StatusKind = ''): void {
    const s = this.el('st')
    s.textContent = msg
    s.className = 'st ' + kind
  }

  setSync(msg: string): void {
    this.el('sync').textContent = msg
  }

  /** Wins read from the video player; hidden until the first one. */
  setWins(msg: string): void {
    this.el('wins').textContent = msg
    this.el('wins').hidden = !msg
  }

  /** Flash the newest low-stock alerts until clicked or `ALERT_MS` passes. Text only: names come from the catalog. */
  showStockAlerts(alerts: StockAlert[]): void {
    const box = this.el('alert')
    if (!alerts.length) return
    box.replaceChildren(
      ...alerts.slice(0, 3).map((a) => {
        const d = box.ownerDocument.createElement('div')
        d.textContent = a.text
        return d
      }),
    )
    box.className = 'al' + (alerts.some((a) => a.urgent) ? ' urgent' : '')
    box.hidden = false
    if (this.alertTimer) clearTimeout(this.alertTimer)
    this.alertTimer = setTimeout(() => this.clearStockAlert(), ALERT_MS)
  }

  clearStockAlert(): void {
    this.el('alert').hidden = true
    if (this.alertTimer) clearTimeout(this.alertTimer)
    this.alertTimer = null
  }

  /** Everything with 3 or fewer left, fewest first; hidden when nothing is low. */
  setLowStock(items: StockItem[]): void {
    const box = this.el('low')
    const doc = box.ownerDocument
    const shown = [...items].sort((a, b) => a.available - b.available).slice(0, LOW_ROWS)
    const title = doc.createElement('div')
    title.className = 'lt'
    title.textContent = 'Running low'
    const rows = shown.map((i) => {
      const r = doc.createElement('div')
      r.className = 'li'
      const name = doc.createElement('span')
      name.textContent = `${i.name} (${i.code})`
      name.title = name.textContent
      const n = doc.createElement('span')
      n.className = 'n' + (i.available <= 1 ? ' urgent' : '')
      n.textContent = leftText(i.available)
      r.append(name, n)
      return r
    })
    if (items.length > shown.length) {
      const more = doc.createElement('div')
      more.className = 'lt'
      more.textContent = `+${items.length - shown.length} more on the Inventory page`
      rows.push(more)
    }
    box.replaceChildren(title, ...rows)
    box.hidden = !items.length
  }

  setCount(n: number): void {
    this.el('cnt').textContent = String(n)
  }

  setDot(state: 'idle' | 'picking' | 'recording' | 'warn'): void {
    this.el('dot').className =
      'dot' + (state === 'recording' ? ' on' : state === 'picking' ? ' pick' : state === 'warn' ? ' warn' : '')
  }

  toggle(): void {
    this.host.style.display = this.host.style.display === 'none' ? '' : 'none'
  }
}
