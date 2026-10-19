import { Deduper } from './dedup'
import { rowId } from './id'
import { autoDetectRows } from './row'
import { leafSegments, rowText, splitRow } from './text'
import type { CapturedRow } from './types'

export type RecorderEvent =
  | { type: 'row'; row: CapturedRow }
  | { type: 'healed'; selector: string }
  | { type: 'lost'; reason: string }

export interface RecorderOptions {
  /** Nodes to ignore entirely (our own panel). */
  ignore?: (node: Node) => boolean
  now?: () => number
  /** How often the watchdog looks for a dead selector / detached container. */
  watchdogMs?: number
  /** No matched row for this long while the DOM kept changing = selector is stale. */
  staleMs?: number
  /** Mutations without a matched row before the selector counts as stale. */
  staleMutations?: number
}

/**
 * Watches one chat container for rows matching a selector and emits
 * structured CapturedRows. DOM-only: no chrome.* APIs, so it runs in jsdom.
 *
 * Self-healing: a watchdog notices when (a) the container left the document,
 * or (b) the DOM keeps changing but nothing matches the selector any more,
 * re-detects the row shape and carries on, or reports `lost`.
 */
export class Recorder {
  private observer: MutationObserver | null = null
  private container: Element | null = null
  private selector = ''
  private dedup: Deduper
  private seq = 0
  private lastRowAt = 0
  private mutationsSinceRow = 0
  private watchdog: ReturnType<typeof setInterval> | null = null
  private listeners = new Set<(e: RecorderEvent) => void>()
  private readonly now: () => number
  private readonly opts: Required<Omit<RecorderOptions, 'ignore' | 'now'>>
  private readonly ignore: (node: Node) => boolean

  constructor(
    readonly showId: string,
    opts: RecorderOptions = {},
  ) {
    this.now = opts.now ?? Date.now
    this.ignore = opts.ignore ?? (() => false)
    this.opts = {
      watchdogMs: opts.watchdogMs ?? 3000,
      staleMs: opts.staleMs ?? 8000,
      staleMutations: opts.staleMutations ?? 5,
    }
    this.dedup = new Deduper()
  }

  on(fn: (e: RecorderEvent) => void): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  get rowCount(): number {
    return this.seq
  }

  get currentSelector(): string {
    return this.selector
  }

  get running(): boolean {
    return this.observer !== null
  }

  /**
   * Start on `container` with `selector`. `seq` continues from `startSeq`
   * (a resumed show) and `knownTexts` are skipped during the initial scan.
   */
  start(container: Element, selector: string, startSeq = 0, knownTexts: Iterable<string> = []): void {
    this.stop()
    this.container = container
    this.selector = selector
    this.seq = startSeq
    this.dedup = new Deduper()
    this.dedup.setKnownTail(knownTexts)
    this.lastRowAt = this.now()
    this.mutationsSinceRow = 0
    this.scan(container, true)
    this.observe(container)
    this.watchdog = setInterval(() => this.check(), this.opts.watchdogMs)
  }

  stop(): void {
    this.observer?.disconnect()
    this.observer = null
    if (this.watchdog) clearInterval(this.watchdog)
    this.watchdog = null
  }

  /** Exposed for tests; runs on the watchdog interval. */
  check(): void {
    if (!this.container || !this.observer) return
    const doc = this.container.ownerDocument
    if (!doc.contains(this.container)) {
      this.heal('container left the page')
      return
    }
    const stale =
      this.mutationsSinceRow >= this.opts.staleMutations && this.now() - this.lastRowAt >= this.opts.staleMs
    if (stale) this.heal('no rows matched while chat kept changing')
  }

  private heal(reason: string): void {
    const doc = this.container!.ownerDocument
    const root = doc.contains(this.container!) ? this.container! : doc.body
    const found = autoDetectRows(root)
    if (!found || (found.selector === this.selector && doc.contains(this.container!))) {
      this.mutationsSinceRow = 0
      this.emit({ type: 'lost', reason })
      return
    }
    const newContainer = doc.contains(this.container!) ? this.container! : (found.sample.parentElement ?? doc.body)
    const known = this.dedup
    this.observer?.disconnect()
    this.container = newContainer
    this.selector = found.selector
    this.dedup = known
    this.mutationsSinceRow = 0
    this.lastRowAt = this.now()
    this.scan(newContainer, true)
    this.observe(newContainer)
    this.emit({ type: 'healed', selector: found.selector })
  }

  private observe(container: Element): void {
    this.observer = new MutationObserver((muts) => this.onMutations(muts))
    this.observer.observe(container, { childList: true, subtree: true })
  }

  /** Exposed for tests. One batch = one framework render; replayed rows are skipped in order. */
  onMutations(muts: MutationRecord[]): void {
    const added: Element[] = []
    for (const m of muts) {
      for (const n of m.addedNodes) {
        if (n.nodeType === 1 && !this.ignore(n)) added.push(n as Element)
      }
    }
    if (!added.length || !this.selector) return
    this.mutationsSinceRow += added.length
    const fresh: Element[] = []
    for (const node of added) {
      for (const el of this.matching(node)) {
        if (!this.dedup.seenElement(el)) fresh.push(el)
      }
    }
    const texts = fresh.map(rowText)
    const replay = this.dedup.replayPrefix(texts)
    fresh.forEach((el, i) => {
      if (i >= replay) this.recordText(el, texts[i]!, false)
    })
  }

  private matching(node: Element): Element[] {
    const out: Element[] = []
    if (node.matches(this.selector)) out.push(node)
    for (const el of node.querySelectorAll(this.selector)) if (!this.ignore(el)) out.push(el)
    return out
  }

  private scan(node: Element, initial: boolean): void {
    if (!this.selector || this.ignore(node)) return
    for (const el of this.matching(node)) this.record(el, initial)
  }

  private record(el: Element, initial: boolean): void {
    if (this.dedup.seenElement(el)) return
    this.recordText(el, rowText(el), initial)
  }

  private recordText(el: Element, rawText: string, initial: boolean): void {
    if (!rawText) return
    const now = this.now()
    if (this.dedup.isDuplicate(rawText, now, initial)) return
    this.dedup.remember(rawText, now)
    const { username, body } = splitRow(leafSegments(el), rawText)
    const row: CapturedRow = {
      id: rowId(this.showId, username, body, now),
      showId: this.showId,
      username,
      body,
      rawText,
      capturedAt: new Date(now).toISOString(),
      seq: ++this.seq,
    }
    this.lastRowAt = now
    this.mutationsSinceRow = 0
    this.emit({ type: 'row', row })
  }

  private emit(e: RecorderEvent): void {
    for (const fn of this.listeners) fn(e)
  }
}
