/** The floating panel, in a shadow root so Whatnot's CSS cannot touch it. */
export const HOST_ID = 'cs-host'

const HTML = `
  <style>
    :host { all: initial; }
    /* .row and friends set display, which would otherwise beat the hidden attribute. */
    [hidden] { display: none !important; }
    .p { width: 290px; font: 13px/1.4 system-ui, -apple-system, Segoe UI, sans-serif; color: #fff;
         background: #002244; border: 2px solid #69BE28; border-radius: 12px; box-shadow: 0 6px 24px rgba(0,0,0,.4); overflow: hidden; }
    .hd { display: flex; align-items: center; gap: 8px; padding: 8px 10px; background: #001833; cursor: default; }
    .dot { width: 10px; height: 10px; border-radius: 50%; background: #A5ACAF; flex: none; }
    .dot.on { background: #69BE28; animation: pulse 1.4s infinite; }
    .dot.pick { background: #4FC3F7; }
    .dot.warn { background: #FF8A80; }
    @keyframes pulse { 50% { opacity: .35; } }
    .ttl { font-weight: 700; flex: 1; }
    .cnt { font-variant-numeric: tabular-nums; color: #69BE28; font-weight: 700; }
    .x { background: none; border: 0; color: #A5ACAF; font-size: 16px; cursor: pointer; padding: 0 2px; }
    .bd { padding: 10px; display: grid; gap: 8px; }
    .bd.min { display: none; }
    .st { font-size: 12px; color: #A5ACAF; min-height: 16px; }
    .st.good { color: #69BE28; } .st.bad { color: #FF8A80; } .st.info { color: #4FC3F7; }
    .sync { font-size: 11px; color: #A5ACAF; }
    .row { display: flex; gap: 6px; flex-wrap: wrap; }
    button.b { flex: 1; border: 0; border-radius: 8px; padding: 7px 8px; font: 600 12px system-ui, sans-serif; cursor: pointer;
               background: #69BE28; color: #002244; }
    button.b.sec { background: #0B3A66; color: #fff; }
    button.b:disabled { opacity: .4; cursor: default; }
    label { font-size: 11px; color: #A5ACAF; display: block; margin-bottom: 3px; }
    input { width: 100%; box-sizing: border-box; border: 1px solid #0B3A66; background: #001833; color: #fff;
            border-radius: 6px; padding: 6px; font: 12px system-ui, sans-serif; }
    .fl { max-height: 150px; overflow-y: auto; display: grid; gap: 4px; }
    .fi { background: #0B3A66; border-left: 3px solid #69BE28; border-radius: 4px; padding: 4px 6px; font-size: 12px; word-break: break-word; }
    .fi b { color: #69BE28; font-weight: 600; margin-right: 4px; }
    #fr .fi { border-left-color: #4FC3F7; cursor: pointer; }
    #fr .fi:hover { background: #124a80; }
    #fr { margin-top: 6px; }
    .empty { font-size: 11px; color: #A5ACAF; }
  </style>
  <div class="p">
    <div class="hd">
      <span class="dot" id="dot"></span><span class="ttl">Chat Scraper</span>
      <span class="cnt" id="cnt">0</span>
      <button class="x" id="min" title="Minimise">–</button>
    </div>
    <div class="bd" id="bd">
      <div class="st" id="st">Ready. Open the show, then press Start.</div>
      <div class="sync" id="sync"></div>
      <div class="sync" id="wins" hidden></div>
      <div class="row" id="resumeRow" hidden>
        <button class="b" id="resume">Resume last show</button>
        <button class="b sec" id="fresh">Start fresh</button>
      </div>
      <div class="row">
        <button class="b" id="start">Start</button>
        <button class="b" id="stop">Stop &amp; Save</button>
      </div>
      <div class="row">
        <button class="b" id="search">Open Chat Search</button>
        <button class="b sec" id="settings" title="Connect this laptop to your shop">Settings</button>
      </div>
      <div class="row">
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
