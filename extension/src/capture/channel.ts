/**
 * Whose show is on screen: the channel, meaning the seller's Whatnot handle. When a show ends,
 * Whatnot moves a viewer on to another channel's show in the same tab (the owner's words,
 * 2026-11-19). A recording's channel is the one shown where the host started it (never one
 * read later), and it stops reading the page while the page names another one, or any name at
 * all when it has none (content/controller.ts).
 *
 * Pure DOM, no chrome.*. Every doubt reads as "" (not read), never as a different channel:
 * nothing read means the recording carries on as before, while a wrong name would stop the
 * shop's own show.
 *
 * NOT VERIFIED against whatnot.com. Nobody has looked at where the live page shows the
 * seller's name. The `/user/<handle>` address is one the host opened a show at
 * (2026-11-13). The selectors in CHANNEL_SOURCES are guesses in the style of the test ids
 * Whatnot's player uses (show-product-title, show-timer, show-winning-status). Until one is
 * checked on a real live page, the reader may find nothing there. When it is checked, change
 * only this table.
 */

export interface ChannelSource {
  /** A CSS selector for the element that names the seller. */
  css: string
  /** `text`: its words are the handle. `href`: it links to `/user/<handle>`. */
  read: 'text' | 'href'
}

/** Where the page names the seller, most specific first. UNVERIFIED: see above. */
export const CHANNEL_SOURCES: readonly ChannelSource[] = [
  { css: '[data-testid="show-host-username"]', read: 'text' },
  { css: '[data-testid="show-host"] a[href^="/user/"]', read: 'href' },
]

/** What a handle looks like. Anything else (words, a button's label, markup) is not read. */
const HANDLE = /^[A-Za-z0-9._-]{2,80}$/
const PROFILE = /^\/user\/([^/?#]+)/

/** A handle as read: trimmed, without one leading @, or '' when it does not look like one. */
export function cleanChannel(raw: string | null | undefined): string {
  const s = (raw ?? '').trim().replace(/^@/, '').trim()
  return HANDLE.test(s) ? s : ''
}

/** Two handles name the same channel: Whatnot handles do not differ by case. */
export function sameChannel(a: string, b: string): boolean {
  return cleanChannel(a).toLowerCase() === cleanChannel(b).toLowerCase()
}

/** The handle in a channel page's address (`/user/<handle>`), or ''. */
export function channelOfPath(pathname: string): string {
  const m = PROFILE.exec(pathname)
  if (!m) return ''
  try {
    return cleanChannel(decodeURIComponent(m[1]!))
  } catch {
    return '' // a broken escape
  }
}

function fromElement(el: Element, read: ChannelSource['read']): string {
  if (read === 'text') return cleanChannel(el.textContent)
  return channelOfPath(el.getAttribute('href') ?? '')
}

/**
 * The channel the page shows, or '' when it cannot tell. The address wins; then the first
 * source in the table that names anyone. A source naming two different handles reads as ''.
 * `ignore` skips nodes that must never count (the chat box, our own panel).
 */
export function readChannel(doc: Document, pathname: string, ignore: (n: Node) => boolean = () => false): string {
  const fromPath = channelOfPath(pathname)
  if (fromPath) return fromPath
  for (const source of CHANNEL_SOURCES) {
    let found = ''
    for (const el of doc.querySelectorAll(source.css)) {
      if (ignore(el)) continue
      const name = fromElement(el, source.read)
      if (!name) continue
      if (found && !sameChannel(found, name)) return ''
      found ||= name
    }
    if (found) return found
  }
  return ''
}
