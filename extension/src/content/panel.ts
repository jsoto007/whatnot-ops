import type { StockAlert, StockItem } from '../stock/alerts'
import { type DotState, type Notice, stockNotice } from './status'

/** The floating panel, in a shadow root so Whatnot's CSS cannot touch it. */
export const HOST_ID = 'cs-host'
/** How long a notification stays up unless clicked away, and how long its fade takes. */
export const NOTICE_MS = 5000
const NOTICE_FADE_MS = 450
/** A notification the pointer rested on goes this long after it leaves. */
const NOTICE_HOLD_MS = 1500
/** Most notifications on screen at once; the oldest goes when another arrives. */
export const NOTICE_MAX = 3
/** Width when the host never resized it, and the limits a resize stays inside. */
const WIDTH = 336
const MIN_W = 280
const MIN_H = 140
/** About the pill's width: what has to stay on screen while minimised. */
const PILL_W = 72
/** Room kept between the panel and the window edge. */
const EDGE = 8
/** Nearer the top of the window than this, notifications stack below the panel, not above it. */
const BELOW_AT = 220
/** A pointer that moved less than this between down and up clicked; more, and it dragged. */
const CLICK_PX = 4

export type Tab = 'live' | 'find' | 'flags'
const TABS: Tab[] = ['live', 'find', 'flags']

/** Where the host put the panel: top-left corner, and its size once resized. Null = bottom-right, default size. */
export interface PanelLayout {
  x: number
  y: number
  w?: number
  h?: number
}

const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

/** A layout read back from storage, or null when it is missing or not one. */
export function readLayout(v: unknown): PanelLayout | null {
  const o = v as Partial<PanelLayout> | null
  if (!o || !num(o.x) || !num(o.y)) return null
  return { x: o.x, y: o.y, ...(num(o.w) ? { w: o.w } : {}), ...(num(o.h) ? { h: o.h } : {}) }
}

/**
 * Keep the panel inside a `vw` × `vh` window: the whole width, and at least the header.
 * `shownW` is what is on screen when it is not the panel (the minimised pill). Pure, for Vitest.
 */
export function clampLayout(l: PanelLayout, vw: number, vh: number, shownW?: number): PanelLayout {
  const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(v, Math.max(lo, hi)))
  const w = clamp(l.w ?? WIDTH, MIN_W, vw - 2 * EDGE)
  const x = clamp(l.x, EDGE, vw - (shownW ?? w) - EDGE)
  const y = clamp(l.y, EDGE, vh - MIN_H / 2 - EDGE)
  const out: PanelLayout = { x, y }
  if (l.w !== undefined) out.w = w
  if (l.h !== undefined) out.h = clamp(l.h, MIN_H, vh - y - EDGE)
  return out
}

const SYS = "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', system-ui, sans-serif"

const HTML = `
  <style>
    :host { all: initial; }
    /* Flex and grid rules set display, which would otherwise beat the hidden attribute. */
    [hidden] { display: none !important; }
    button { font: inherit; color: inherit; }
    button:focus-visible, input:focus-visible, [tabindex]:focus-visible { outline: 2px solid #8fdcff; outline-offset: 2px; }
    /* Liquid Glass, dark tint: white text stays readable over any stream behind it.
       Never taller than the window: the header and tabs stay put and the rest scrolls (.sc). */
    .p { position: relative; width: 336px; max-width: calc(100vw - 16px); max-height: calc(100vh - 32px); box-sizing: border-box; padding: 12px;
         display: flex; flex-direction: column; gap: 10px;
         font: 14px/1.4 ${SYS}; letter-spacing: -0.005em; color: #fff;
         background: rgba(30, 26, 52, 0.62); -webkit-backdrop-filter: blur(36px) saturate(200%); backdrop-filter: blur(36px) saturate(200%);
         border: 1px solid rgba(255, 255, 255, 0.28); border-radius: 32px;
         box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.35), 0 24px 70px rgba(10, 0, 40, 0.45); }
    .glass { background: rgba(30, 26, 52, 0.62); -webkit-backdrop-filter: blur(24px) saturate(180%); backdrop-filter: blur(24px) saturate(180%);
             border: 1px solid rgba(255, 255, 255, 0.28); box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.3), 0 10px 30px rgba(10, 0, 40, 0.45); }
    .hd { display: flex; align-items: center; gap: 8px; padding: 4px 4px 0 8px; cursor: grab; flex: none; user-select: none; -webkit-user-select: none; touch-action: none; }
    .hd.dragging, .pill.dragging { cursor: grabbing; }
    .sc { flex: 1 1 auto; min-height: 0; overflow-y: auto; overscroll-behavior: contain; display: grid; gap: 10px; align-content: start;
          margin: 0 -6px; padding: 0 6px 6px; scrollbar-width: thin; scrollbar-color: rgba(255, 255, 255, 0.35) transparent; }
    .grip { position: absolute; right: 6px; bottom: 6px; width: 18px; height: 18px; cursor: nwse-resize; touch-action: none; border-radius: 0 0 14px 0; opacity: .7;
            background: linear-gradient(135deg, transparent 0 55%, rgba(255, 255, 255, 0.75) 55% 62%, transparent 62% 74%, rgba(255, 255, 255, 0.75) 74% 81%, transparent 81%); }
    .grip:hover { opacity: 1; }
    /* The status dot: green is working, amber sorts itself out, red needs a click, blue waits for one, grey is not recording. */
    .dot { width: 10px; height: 10px; border-radius: 50%; background: rgba(255, 255, 255, 0.55); flex: none; transition: background 200ms ease, box-shadow 200ms ease; }
    .dot.good { background: #30d158; box-shadow: 0 0 0 5px rgba(48, 209, 88, 0.3); animation: breathe 2.6s ease-in-out infinite; }
    .dot.warn { background: #ff9f0a; box-shadow: 0 0 0 5px rgba(255, 159, 10, 0.3); }
    .dot.bad { background: #ff453a; box-shadow: 0 0 0 5px rgba(255, 69, 58, 0.32); animation: blink 2.4s steps(1) infinite; }
    .dot.pick { background: #64d2ff; box-shadow: 0 0 0 5px rgba(100, 210, 255, 0.28); }
    @keyframes breathe { 0%, 100% { opacity: 1; } 50% { opacity: .55; } }
    @keyframes blink { 0%, 60%, 100% { opacity: 1; } 70%, 90% { opacity: .25; } }
    @media (prefers-reduced-motion: reduce) { .dot, .toast { animation: none !important; } }
    .ttl { font-weight: 700; font-size: 17px; letter-spacing: -0.01em; flex: 1; min-width: 0; }
    .cnt { font-variant-numeric: tabular-nums; font-weight: 600; font-size: 13px; padding: 4px 10px; border-radius: 999px; background: rgba(255, 255, 255, 0.16); }
    .x { width: 36px; height: 36px; flex: none; border-radius: 50%; border: 1px solid rgba(255, 255, 255, 0.35); background: rgba(255, 255, 255, 0.14);
         color: #fff; font: 600 18px/1 system-ui, sans-serif; cursor: pointer; display: grid; place-items: center; padding: 0; }
    .x:hover { background: rgba(255, 255, 255, 0.24); }
    .x[aria-expanded="true"] { background: #fff; color: #1a1033; }
    /* Minimised: the dot and the count, nothing else. */
    .pill { display: flex; align-items: center; gap: 8px; height: 28px; padding: 0 11px 0 9px; border-radius: 999px; box-sizing: border-box; color: #fff;
            cursor: grab; user-select: none; -webkit-user-select: none; touch-action: none; }
    .pill .dot { width: 12px; height: 12px; }
    .pill .dot.good, .pill .dot.warn, .pill .dot.bad, .pill .dot.pick { box-shadow: 0 0 0 4px var(--halo, rgba(255, 255, 255, 0.12)); }
    .pill .dot.good { --halo: rgba(48, 209, 88, 0.3); } .pill .dot.warn { --halo: rgba(255, 159, 10, 0.3); }
    .pill .dot.bad { --halo: rgba(255, 69, 58, 0.32); } .pill .dot.pick { --halo: rgba(100, 210, 255, 0.28); }
    .pill b { font: 700 13px ${SYS}; font-variant-numeric: tabular-nums; }
    /* Notifications stack upward from the panel or the pill, newest at the bottom. */
    .toasts { position: absolute; right: 0; bottom: 100%; margin-bottom: 10px; display: flex; flex-direction: column; align-items: flex-end; gap: 8px;
              width: max-content; max-width: min(320px, calc(100vw - 32px)); pointer-events: none; }
    .toasts.below { bottom: auto; top: 100%; margin: 10px 0 0; }
    .toast { pointer-events: auto; display: flex; align-items: center; gap: 10px; padding: 9px 14px 9px 11px; border-radius: 16px; font: 13px/1.25 ${SYS}; color: #fff;
             cursor: pointer; animation: tin 250ms ease-out; transition: opacity 450ms ease, transform 450ms ease; }
    .toast .ic { width: 10px; height: 10px; border-radius: 50%; background: #30d158; flex: none; }
    .toast .tx { display: grid; gap: 1px; min-width: 0; }
    .toast b { font-weight: 700; overflow-wrap: anywhere; }
    .toast .tx span { font-size: 12px; opacity: .85; overflow-wrap: anywhere; }
    .toast.low { background: rgba(255, 159, 10, 0.94); color: #1a1033; border-color: rgba(255, 214, 140, 0.6); } .toast.low .ic { background: #1a1033; }
    .toast.out { background: rgba(255, 69, 58, 0.94); border-color: rgba(255, 160, 150, 0.7); } .toast.out .ic { background: #fff; }
    .toast.gone { opacity: 0; transform: translateY(-6px); }
    @keyframes tin { from { opacity: 0; transform: translateY(8px); } }
    /* Live · Find · Flags */
    .tabs { display: flex; gap: 4px; padding: 4px; border-radius: 999px; background: rgba(10, 6, 30, 0.3); border: 1px solid rgba(255, 255, 255, 0.16); flex: none; }
    .tab { flex: 1; min-height: 34px; border: 0; background: transparent; color: rgba(255, 255, 255, 0.85); border-radius: 999px; font: 600 13px ${SYS};
           cursor: pointer; display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 0 8px; }
    .tab:hover { background: rgba(255, 255, 255, 0.12); }
    .tab.on, .tab.on:hover { background: #fff; color: #1a1033; }
    .tab em { font-style: normal; font-size: 11px; padding: 1px 6px; border-radius: 999px; background: rgba(255, 159, 10, 0.9); color: #1a1033; }
    .tab.on em { background: #1a1033; color: #fff; }
    .pane { display: grid; gap: 10px; }
    .card { display: grid; gap: 4px; padding: 14px 16px; border-radius: 22px; background: rgba(10, 6, 30, 0.38); border: 1px solid rgba(255, 255, 255, 0.16); }
    .st { font-size: 15px; font-weight: 600; min-height: 20px; color: #fff; }
    .st.good { color: #7cf29a; } .st.bad { color: #ff8a80; } .st.info { color: #8fdcff; }
    .sync { font-size: 13px; color: rgba(255, 255, 255, 0.82); }
    .sync:empty { display: none; }
    /* Resume or start fresh: only when a saved show was found, inside the status card. */
    .prompt { display: flex; gap: 8px; margin-top: 8px; }
    .pb { flex: 1; min-height: 36px; border-radius: 999px; border: 1px solid #fff; background: #fff; color: #1a1033; font: 600 13px ${SYS}; cursor: pointer; padding: 0 10px; }
    .pb.sec { background: rgba(255, 255, 255, 0.16); color: #fff; border-color: rgba(255, 255, 255, 0.32); }
    .row { display: flex; gap: 8px; flex-wrap: wrap; }
    button.b { width: 100%; min-height: 44px; border-radius: 999px; padding: 0 14px; white-space: nowrap; font: 600 14px ${SYS}; cursor: pointer;
               border: 1px solid #fff; background: #fff; color: #1a1033; box-shadow: 0 6px 18px rgba(0, 0, 0, 0.22); transition: filter 160ms ease, transform 160ms ease;
               display: inline-flex; align-items: center; justify-content: center; gap: 8px; }
    .row button.b { flex: 1; width: auto; }
    button.b:hover { filter: brightness(0.95); }
    button.b:active { transform: scale(0.98); }
    button.b.sec, button.b.stop { background: rgba(255, 255, 255, 0.16); color: #fff; border-color: rgba(255, 255, 255, 0.32); box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.3); }
    button.b.sec:hover, button.b.stop:hover { background: rgba(255, 255, 255, 0.24); filter: none; }
    button.b.stop::before { content: ""; width: 10px; height: 10px; border-radius: 2px; background: #ff453a; }
    button.b:disabled { opacity: .4; cursor: default; transform: none; }
    /* Running low: one row that opens the list. */
    .lowbtn { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; box-sizing: border-box; padding: 10px 14px; border-radius: 18px;
              font: 600 13px ${SYS}; color: #fff; background: rgba(255, 159, 10, 0.22); border: 1px solid rgba(255, 214, 140, 0.38); cursor: pointer; text-align: left; }
    .lowbtn::after { content: "▾"; opacity: .7; }
    .lowbtn.open::after { content: "▴"; }
    .lowbtn.urgent { background: rgba(255, 69, 58, 0.22); border-color: rgba(255, 140, 130, 0.45); }
    .lw { display: grid; padding: 6px 16px; border-radius: 22px; background: rgba(10, 6, 30, 0.38); border: 1px solid rgba(255, 159, 10, 0.45);
          max-height: min(264px, 40vh); overflow-y: auto; scrollbar-width: thin; scrollbar-color: rgba(255, 255, 255, 0.35) transparent; }
    .lw .li { display: flex; align-items: baseline; gap: 10px; font-size: 13px; padding: 7px 0; border-top: 1px solid rgba(255, 255, 255, 0.1); }
    .lw .li:first-child { border-top: 0; }
    .lw .nm { flex: 1; min-width: 0; overflow-wrap: anywhere; line-height: 1.3; }
    .lw .cd { display: block; font-size: 11.5px; color: rgba(255, 255, 255, 0.66); letter-spacing: 0.02em; }
    .lw .n { flex: none; font-weight: 700; color: #ffb340; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .lw .n.urgent { color: #ff8a80; }
    /* Find and Flags */
    .find { display: flex; align-items: center; gap: 8px; min-height: 42px; padding: 0 14px; border-radius: 999px; border: 1px solid rgba(255, 255, 255, 0.22); background: rgba(0, 0, 0, 0.24); }
    .find svg { width: 15px; height: 15px; flex: none; opacity: .75; }
    .find input { flex: 1; min-width: 0; border: 0; background: transparent; color: #fff; font: 14px ${SYS}; padding: 0; min-height: 0; }
    .find input::placeholder, input.tx::placeholder { color: rgba(255, 255, 255, 0.6); }
    label { font-size: 12px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: rgba(255, 255, 255, 0.82); display: block; margin: 0 0 6px 12px; }
    input.tx { width: 100%; box-sizing: border-box; min-height: 42px; border: 1px solid rgba(255, 255, 255, 0.22); background: rgba(0, 0, 0, 0.24); color: #fff;
               border-radius: 999px; padding: 0 16px; font: 14px ${SYS}; }
    .fl { max-height: 220px; overflow-y: auto; display: grid; border-radius: 18px; background: rgba(10, 6, 30, 0.34); border: 1px solid rgba(255, 255, 255, 0.16); scrollbar-width: thin; }
    .fi { padding: 9px 14px; font-size: 13px; word-break: break-word; border-top: 1px solid rgba(255, 255, 255, 0.1); }
    .fi:first-child { border-top: 0; }
    .fi b { color: rgba(255, 255, 255, 0.7); font-weight: 600; margin-right: 6px; font-variant-numeric: tabular-nums; }
    /* A fixed height: the panel sits on its bottom edge, so a list that grew when the words field lost
       focus moved the tabs out from under the pointer between mousedown and mouseup. */
    #fl { height: 220px; background: rgba(255, 159, 10, 0.22); border-color: rgba(255, 214, 140, 0.38); }
    #fr .fi { cursor: pointer; }
    #fr .fi:hover { background: rgba(255, 255, 255, 0.1); }
    .empty { font-size: 13px; color: rgba(255, 255, 255, 0.78); padding: 10px 14px; }
    /* The ⋯ menu: everything the host does not need mid-show. */
    .menu { position: absolute; left: 12px; right: 12px; top: 58px; z-index: 3; padding: 6px; border-radius: 22px; background: rgb(24, 19, 46);
            border: 1px solid rgba(255, 255, 255, 0.22); box-shadow: 0 18px 50px rgba(0, 0, 0, 0.5); display: grid; gap: 2px; }
    .mi { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 11px 14px; border-radius: 16px; border: 0; background: transparent;
          color: #fff; font: 600 14px ${SYS}; cursor: pointer; text-align: left; }
    .mi:hover, .mi:focus-visible { background: rgba(255, 255, 255, 0.12); }
    .mi:disabled { opacity: .4; cursor: default; background: transparent; }
    .mi small { font-weight: 500; color: rgba(255, 255, 255, 0.6); font-size: 12px; }
    .mi.danger { color: #ff8a80; }
    .mi.sep { margin-top: 4px; border-top: 1px solid rgba(255, 255, 255, 0.12); border-radius: 0 0 16px 16px; padding-top: 12px; }
  </style>
  <div class="toasts" id="toasts" aria-live="polite"></div>
  <div class="pill glass" id="pill" role="button" tabindex="0" title="Chat Scraper · click to open, drag to move" hidden>
    <span class="dot" id="pdot"></span><b id="pcnt">0</b>
  </div>
  <div class="p" id="p">
    <div class="hd" id="hd" title="Drag to move · double-click to put it back in the corner">
      <span class="dot" id="dot"></span><span class="ttl">Chat Scraper</span>
      <span class="cnt" id="cnt" title="Messages captured">0</span>
      <button class="x" id="menu" title="More" aria-label="More" aria-haspopup="menu" aria-expanded="false">⋯</button>
      <button class="x" id="min" title="Minimise" aria-label="Minimise">–</button>
    </div>
    <div class="tabs" role="tablist">
      <button class="tab on" id="tab-live" data-tab="live" role="tab" aria-selected="true">Live</button>
      <button class="tab" id="tab-find" data-tab="find" role="tab" aria-selected="false">Find</button>
      <button class="tab" id="tab-flags" data-tab="flags" role="tab" aria-selected="false">Flags <em id="nflags" hidden>0</em></button>
    </div>
    <div class="sc">
      <div class="pane" id="pane-live">
        <div class="card">
          <div class="st" id="st">Ready. Open the show, then press Start.</div>
          <div class="sync" id="sync"></div>
          <div class="sync" id="wins" hidden></div>
          <div class="prompt" id="resumeRow" hidden>
            <button class="pb" id="resume">Resume last show</button>
            <button class="pb sec" id="fresh">Start fresh</button>
          </div>
        </div>
        <div class="card" id="ship" hidden>
          <div class="st" id="shipSt">Whatnot shipments</div>
          <div class="sync" id="shipNote">Pick this show in Whatnot's show filter and include every status, then send. Every page and every lot is read.</div>
          <div class="row"><button class="b" id="shipSend">Send shipments to Sales Ops</button></div>
        </div>
        <button class="b" id="start">Start</button>
        <button class="b stop" id="stop" hidden>Stop &amp; Save</button>
        <div class="row" id="shipNext" hidden>
          <button class="b sec" id="shipOpen" title="Opens Seller Hub's Shipments page in a new tab">Next: get Whatnot shipments</button>
        </div>
        <button class="lowbtn" id="lowbtn" aria-expanded="false" aria-controls="low" hidden><span id="lown">0 running low</span></button>
        <div class="lw" id="low" hidden></div>
      </div>
      <div class="pane" id="pane-find" hidden>
        <label class="find" aria-label="Find buyer (live)">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
          <input id="find" placeholder="Find buyer (live)">
        </label>
        <div class="fl" id="fr" hidden></div>
      </div>
      <div class="pane" id="pane-flags" hidden>
        <div class="fl" id="fl"><div class="empty">Nothing flagged yet.</div></div>
        <div>
          <label for="words">Watch words / usernames (comma separated)</label>
          <input class="tx" id="words" placeholder="mine, sold, claim">
        </div>
      </div>
    </div>
    <div class="menu" id="menubox" role="menu" hidden>
      <button class="mi" id="search" role="menuitem">Open Chat Search <small>new tab</small></button>
      <button class="mi" id="backup" role="menuitem">Save backup copy <small>spreadsheet</small></button>
      <button class="mi" id="settings" role="menuitem">Settings <small>connect this laptop</small></button>
      <button class="mi danger sep" id="clear" role="menuitem">Clear this show</button>
    </div>
    <div class="grip" id="grip" title="Drag to resize" aria-hidden="true"></div>
  </div>`

export type StatusKind = 'good' | 'bad' | 'info' | ''

export class Panel {
  readonly host: HTMLDivElement
  private readonly root: ShadowRoot
  private layout: PanelLayout | null = null
  private minimised = false
  /** The running-low list is open (it stays so across polls and a minimise). */
  private lowOpen = false
  /** Each notification's fade or removal timer. */
  private readonly timers = new Map<HTMLElement, ReturnType<typeof setTimeout>>()
  /** The window `mount` listens on for resizes; `unmount` lets go of it. */
  private mountedOn: Window | null = null
  /** One reference, so `unmount` can remove it: an anonymous listener kept the whole panel (and the show it holds) alive. */
  private readonly onResize = (): void => this.apply()
  /** Told when the host finishes a move or resize (null after a reset), so it can be remembered. */
  onLayout: ((l: PanelLayout | null) => void) | null = null

  constructor(doc: Document = document) {
    this.host = doc.createElement('div')
    this.host.id = HOST_ID
    this.root = this.host.attachShadow({ mode: 'open' })
    this.root.innerHTML = HTML
    this.el('min').onclick = () => this.minimise(true)
    const pill = this.el('pill')
    pill.addEventListener('click', () => {
      // The click that ends a drag is not a request to open.
      if (pill.dataset.dragged) delete pill.dataset.dragged
      else this.minimise(false)
    })
    pill.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        this.minimise(false)
      }
    })
    this.el('menu').onclick = () => this.openMenu(this.el('menubox').hidden)
    this.el('menubox').addEventListener('click', (e) => {
      if ((e.target as Element).closest('.mi')) this.openMenu(false)
    })
    this.root.addEventListener('click', (e) => {
      const path = e.composedPath()
      if (!this.el('menubox').hidden && !path.includes(this.el('menubox')) && !path.includes(this.el('menu'))) this.openMenu(false)
    })
    this.root.addEventListener('keydown', (e) => {
      if ((e as KeyboardEvent).key === 'Escape') this.openMenu(false)
    })
    // By id, not querySelectorAll: in jsdom the selector engine caches the last root it searched,
    // which would keep a retired panel (and the show it holds) alive in the memory tests.
    for (const name of TABS) this.el(`tab-${name}`).onclick = () => this.showTab(name)
    this.el('lowbtn').onclick = () => this.setLowOpen(!this.lowOpen)
    this.el('toasts').addEventListener('click', (e) => {
      const t = (e.target as Element).closest<HTMLElement>('.toast')
      if (t) this.dismiss(t)
    })
    this.wireMove(this.el('hd'))
    this.wireMove(pill)
    this.wireResize()
    this.apply()
  }

  mount(doc: Document = document): void {
    doc.documentElement.appendChild(this.host)
    this.mountedOn?.removeEventListener('resize', this.onResize)
    this.mountedOn = doc.defaultView
    this.mountedOn?.addEventListener('resize', this.onResize)
  }

  /**
   * Off the page for good (a newer copy of the content script took over). Nothing the page
   * keeps may still point here: the buttons' handlers hold the Controller and its whole show.
   */
  unmount(): void {
    this.mountedOn?.removeEventListener('resize', this.onResize)
    this.mountedOn = null
    for (const t of this.timers.values()) clearTimeout(t)
    this.timers.clear()
    this.onLayout = null
    this.host.remove()
  }

  getLayout(): PanelLayout | null {
    return this.layout
  }

  /** Put the panel where the host left it (null = bottom-right corner, default size). */
  setLayout(l: PanelLayout | null): void {
    this.layout = l
    this.apply()
  }

  private get win(): Window | null {
    return this.host.ownerDocument.defaultView
  }

  /** Place the host and size the panel from `layout`, kept inside the window. */
  private apply(): void {
    const p = this.el('p')
    const w = this.win
    const l = this.layout && w ? clampLayout(this.layout, w.innerWidth, w.innerHeight, this.minimised ? PILL_W : undefined) : null
    this.host.style.cssText = l
      ? `position:fixed;left:${l.x}px;top:${l.y}px;z-index:2147483647;`
      : 'position:fixed;right:16px;bottom:16px;z-index:2147483647;'
    p.style.width = l?.w ? `${l.w}px` : ''
    p.style.height = l?.h && !this.minimised ? `${l.h}px` : ''
    p.style.maxHeight = l && w ? `${w.innerHeight - l.y - EDGE}px` : ''
    this.el('toasts').classList.toggle('below', !!l && l.y < BELOW_AT)
  }

  /** The current box (panel or pill), as a layout anchored at its top-left corner. */
  private here(): PanelLayout {
    const r = this.host.getBoundingClientRect()
    return { ...this.layout, x: r.left, y: r.top }
  }

  /** Drag `el` to move the whole thing; a press that does not move is left to `el`'s own click. */
  private wireMove(el: HTMLElement): void {
    el.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || (e.target as Element).closest('button')) return
      e.preventDefault()
      const start = this.here()
      const [sx, sy] = [e.clientX, e.clientY]
      let moved = false
      el.setPointerCapture?.(e.pointerId)
      el.classList.add('dragging')
      const move = (m: PointerEvent) => {
        if (!moved && Math.hypot(m.clientX - sx, m.clientY - sy) < CLICK_PX) return
        moved = true
        this.setLayout({ ...start, x: start.x + m.clientX - sx, y: start.y + m.clientY - sy })
      }
      const up = () => {
        el.removeEventListener('pointermove', move)
        el.classList.remove('dragging')
        if (moved) {
          el.dataset.dragged = '1'
          this.settle()
        }
      }
      el.addEventListener('pointermove', move)
      el.addEventListener('pointerup', up, { once: true })
      el.addEventListener('pointercancel', up, { once: true })
    })
    if (el.id === 'hd') {
      // Double-click the header to send the panel back to the corner.
      el.addEventListener('dblclick', (e) => {
        if ((e.target as Element).closest('button')) return
        this.setLayout(null)
        this.onLayout?.(null)
      })
    }
  }

  /** Drag the bottom-right grip to resize. */
  private wireResize(): void {
    const grip = this.el('grip')
    grip.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return
      e.preventDefault()
      const r = this.el('p').getBoundingClientRect()
      const start: PanelLayout = { x: r.left, y: r.top, w: r.width, h: r.height }
      const [sx, sy] = [e.clientX, e.clientY]
      grip.setPointerCapture?.(e.pointerId)
      const move = (m: PointerEvent) => this.setLayout({ ...start, w: start.w! + m.clientX - sx, h: start.h! + m.clientY - sy })
      const up = () => {
        grip.removeEventListener('pointermove', move)
        this.settle()
      }
      grip.addEventListener('pointermove', move)
      grip.addEventListener('pointerup', up, { once: true })
      grip.addEventListener('pointercancel', up, { once: true })
    })
  }

  /** After a move or resize: store the clamped layout, the one actually on screen. */
  private settle(): void {
    const w = this.win
    if (this.layout && w) this.layout = clampLayout(this.layout, w.innerWidth, w.innerHeight, this.minimised ? PILL_W : undefined)
    this.onLayout?.(this.layout)
  }

  contains(node: Node): boolean {
    return node === this.host || this.host.contains(node)
  }

  el<T extends HTMLElement = HTMLElement>(id: string): T {
    return this.root.getElementById(id) as T
  }

  // ---------- minimised, tabs, menu ----------

  /** Minimised, only the pill shows: the dot and the count. Notifications still appear beside it. */
  minimise(on: boolean): void {
    this.minimised = on
    this.el('p').hidden = on
    this.el('pill').hidden = !on
    this.openMenu(false)
    this.apply()
  }

  get isMinimised(): boolean {
    return this.minimised
  }

  showTab(tab: Tab): void {
    for (const name of TABS) {
      const on = name === tab
      const t = this.el(`tab-${name}`)
      t.classList.toggle('on', on)
      t.setAttribute('aria-selected', String(on))
      this.el(`pane-${name}`).hidden = !on
    }
    if (tab === 'find') this.el<HTMLInputElement>('find').focus()
  }

  openMenu(on: boolean): void {
    this.el('menubox').hidden = !on
    this.el('menu').setAttribute('aria-expanded', String(on))
  }

  // ---------- what it says ----------

  setStatus(msg: string, kind: StatusKind = ''): void {
    const s = this.el('st')
    s.textContent = msg
    s.className = 'st ' + kind
  }

  /** The Shipments card's line (only on Seller Hub's Shipments page). */
  setShipStatus(msg: string, kind: StatusKind = ''): void {
    const s = this.el('shipSt')
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
    const text = String(n)
    this.el('cnt').textContent = text
    this.el('pcnt').textContent = text
  }

  setDot(state: DotState): void {
    const cls = 'dot' + (state === 'idle' ? '' : state === 'picking' ? ' pick' : ' ' + state)
    this.el('dot').className = cls
    this.el('pdot').className = cls
  }

  /** The number on the Flags tab; none when nothing is flagged. */
  setFlagCount(n: number): void {
    const em = this.el('nflags')
    em.textContent = String(n)
    em.hidden = !n
  }

  // ---------- notifications ----------

  /** Show a notice beside the panel or the pill for NOTICE_MS, or until it is clicked. Text only. */
  notify(n: Notice): void {
    const box = this.el('toasts')
    while (box.children.length >= NOTICE_MAX) this.remove(box.firstElementChild as HTMLElement)
    const doc = box.ownerDocument
    const el = doc.createElement('div')
    el.className = `toast glass ${n.kind}` + (n.urgent ? ' urgent' : '')
    if (n.group) el.dataset.group = n.group
    el.setAttribute('role', 'status')
    el.title = 'Click to dismiss'
    const ic = doc.createElement('span')
    ic.className = 'ic'
    const tx = doc.createElement('span')
    tx.className = 'tx'
    const b = doc.createElement('b')
    b.textContent = n.title
    const s = doc.createElement('span')
    s.textContent = n.detail
    tx.append(b, s)
    el.append(ic, tx)
    box.append(el)
    this.arm(el, NOTICE_MS)
    el.addEventListener('pointerenter', () => this.disarm(el))
    el.addEventListener('pointerleave', () => this.arm(el, NOTICE_HOLD_MS))
  }

  /** Fade a notice out, then take it off. */
  dismiss(el: HTMLElement): void {
    if (el.classList.contains('gone')) return
    this.disarm(el)
    el.classList.add('gone')
    this.timers.set(el, setTimeout(() => this.remove(el), NOTICE_FADE_MS))
  }

  /** Take notices off at once: all of them, or one group's. */
  clearNotices(group?: Notice['group']): void {
    for (const el of [...this.el('toasts').children] as HTMLElement[]) {
      if (!group || el.dataset.group === group) this.remove(el)
    }
  }

  private arm(el: HTMLElement, ms: number): void {
    this.disarm(el)
    this.timers.set(el, setTimeout(() => this.dismiss(el), ms))
  }

  private disarm(el: HTMLElement): void {
    const t = this.timers.get(el)
    if (t) clearTimeout(t)
    this.timers.delete(el)
  }

  private remove(el: HTMLElement): void {
    this.disarm(el)
    el.remove()
  }

  /** The newest low-stock alerts, as notices. Text only: names come from the catalog. */
  showStockAlerts(alerts: StockAlert[]): void {
    for (const a of alerts.slice(0, NOTICE_MAX)) this.notify(stockNotice(a))
  }

  clearStockAlert(): void {
    this.clearNotices('stock')
  }

  // ---------- running low ----------

  /**
   * What this show has sold that is down to 3 or fewer, fewest first; hidden when none.
   * One row says how many; it opens the list, where every name shows in full.
   */
  setLowStock(items: StockItem[]): void {
    const btn = this.el('lowbtn')
    const box = this.el('low')
    if (!items.length) {
      btn.hidden = true
      box.hidden = true
      box.replaceChildren()
      return
    }
    const doc = box.ownerDocument
    const shown = [...items].sort((a, b) => a.available - b.available)
    this.el('lown').textContent = `${shown.length} running low`
    btn.classList.toggle('urgent', shown.some((i) => i.available <= 1))
    btn.hidden = false
    box.replaceChildren(
      ...shown.map((i) => {
        const r = doc.createElement('div')
        r.className = 'li'
        const name = doc.createElement('span')
        name.className = 'nm'
        name.textContent = i.name
        const code = doc.createElement('span')
        code.className = 'cd'
        code.textContent = i.code
        name.append(code)
        const n = doc.createElement('span')
        n.className = 'n' + (i.available <= 1 ? ' urgent' : '')
        n.textContent = leftText(i.available)
        r.append(name, n)
        return r
      }),
    )
    box.hidden = !this.lowOpen
  }

  private setLowOpen(on: boolean): void {
    this.lowOpen = on
    const btn = this.el('lowbtn')
    btn.classList.toggle('open', on)
    btn.setAttribute('aria-expanded', String(on))
    this.el('low').hidden = !on || btn.hidden
  }

  toggle(): void {
    this.host.style.display = this.host.style.display === 'none' ? '' : 'none'
  }
}

/** "2 left", "sold out" or "oversold by 3" (stock/alerts.ts). */
function leftText(available: number): string {
  if (available > 0) return `${available} left`
  return available === 0 ? 'sold out' : `oversold by ${-available}`
}
