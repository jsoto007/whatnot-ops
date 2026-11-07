/**
 * When the background uploads next. A save asks for an upload shortly (a 2 s debounce).
 * After a failed upload no save brings the next try forward: it waits for the backoff,
 * however many lines are saved meanwhile (the retry alarm fires then too).
 */
export class UploadTimer {
  private timer: ReturnType<typeof setTimeout> | null = null
  /** When `timer` fires (ms since the epoch). */
  private at = 0
  /** No upload before this; 0 when nothing failed. */
  private notBefore = 0

  constructor(private readonly upload: () => void) {}

  /** Upload in `delayMs`, or at the end of the backoff while one runs. A pending upload is
   *  never pushed later: a busy chat saves every ~1.5 s, and re-arming a 2 s timer on each
   *  save meant it never fired (uploads then waited for the one-minute sweep). */
  soon(delayMs = 2000): void {
    const now = Date.now()
    if (now < this.notBefore) {
      // Already set for the end of the backoff or later: leave it, never sooner and not pushed back.
      if (this.timer && this.at >= this.notBefore) return
      return this.arm(Math.max(now + delayMs, this.notBefore))
    }
    if (this.timer && this.at <= now + delayMs) return
    this.arm(now + delayMs)
  }

  /** An upload failed: none before `ms` from now. */
  backOff(ms: number): void {
    this.notBefore = Date.now() + ms
  }

  /** Uploads work again (one went through, or the connection changed). */
  reset(): void {
    this.notBefore = 0
  }

  private arm(at: number): void {
    if (this.timer) clearTimeout(this.timer)
    this.at = at
    this.timer = setTimeout(() => {
      this.timer = null
      // Set before an upload failed: wait for the backoff that failure started.
      if (Date.now() < this.notBefore) this.soon(0)
      else this.upload()
    }, Math.max(0, at - Date.now()))
  }
}
