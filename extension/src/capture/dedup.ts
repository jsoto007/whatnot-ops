/**
 * Two layers of de-duplication, same rules as v1.2:
 *  - an element is only ever recorded once (WeakSet);
 *  - the same text within `windowMs` is a re-render, not a new message;
 *  - on resume, text already in the saved tail is skipped during the initial scan.
 */
export class Deduper {
  private seen = new WeakSet<Element>()
  private recent: { text: string; ts: number }[] = []
  private knownTail = new Set<string>()
  /** Every recorded text in order, bounded, for replay detection. */
  private history: string[] = []

  constructor(
    private readonly windowMs = 5000,
    private readonly historyMax = 5000,
  ) {}

  /** True when the element was recorded before; marks it seen otherwise. */
  seenElement(el: Element): boolean {
    if (this.seen.has(el)) return true
    this.seen.add(el)
    return false
  }

  /** Texts already saved (e.g. the last 300 rows) so an initial scan after a resume does not repeat them. */
  setKnownTail(texts: Iterable<string>): void {
    this.knownTail = new Set(texts)
    this.history = [...texts].slice(-this.historyMax)
  }

  /**
   * A framework re-render puts old rows back as brand-new nodes, in the same
   * order they were recorded. Given the texts of one mutation batch, returns
   * how many leading rows replay a contiguous run of the recorded history
   * (0 unless at least `min` do). Genuinely new messages never line up with
   * three or more old ones in order, so they fall through and are recorded.
   */
  replayPrefix(texts: string[], min = 3): number {
    if (texts.length < min) return 0
    const first = texts[0]!
    let best = 0
    for (let j = this.history.length - 1; j >= 0; j--) {
      if (this.history[j] !== first) continue
      let k = 1
      while (k < texts.length && j + k < this.history.length && this.history[j + k] === texts[k]) k++
      if (k > best) best = k
      if (best === texts.length) break
    }
    return best >= min ? best : 0
  }

  isDuplicate(text: string, now: number, initial: boolean): boolean {
    if (initial) return this.knownTail.has(text)
    this.prune(now)
    return this.recent.some((r) => r.text === text)
  }

  remember(text: string, now: number): void {
    this.recent.push({ text, ts: now })
    this.knownTail.add(text)
    this.history.push(text)
    if (this.history.length > this.historyMax * 1.5) this.history.splice(0, this.history.length - this.historyMax)
  }

  private prune(now: number): void {
    const cutoff = now - this.windowMs
    let i = 0
    while (i < this.recent.length && this.recent[i]!.ts < cutoff) i++
    if (i) this.recent.splice(0, i)
  }
}
