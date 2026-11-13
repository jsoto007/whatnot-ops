/** Collapse newlines to " | " and trim. Same output as Chat Scraper v1.2 so old .txt files still match. */
export function cleanText(t: string | null | undefined): string {
  return (t ?? '').trim().replace(/\s*\n\s*/g, ' | ')
}

/**
 * Text of a chat row: its text segments joined by single spaces. innerText is
 * not used because adjacent inline elements (username, message) would be glued
 * together ("buyer77I will take 2"); this is identical in Chrome and jsdom.
 */
export function rowText(el: Element): string {
  return leafSegments(el).join(' ')
}

/** Non-empty trimmed text nodes in document order. */
export function leafSegments(el: Element): string[] {
  const doc = el.ownerDocument
  const walker = doc.createTreeWalker(el, 4 /* NodeFilter.SHOW_TEXT */)
  const out: string[] = []
  let n: Node | null
  while ((n = walker.nextNode())) {
    const t = (n.textContent ?? '').replace(/\s+/g, ' ').trim()
    if (t) out.push(t)
  }
  return out
}

const USERNAME = /^[A-Za-z0-9._-]{1,40}$/

export interface SplitRow {
  username: string
  body: string
}

/**
 * Whatnot draws a user with no profile photo as the first letter of their name, in its own
 * element before the name: "L" "lani808" "B4". That letter is not the username.
 */
const avatarLetter = (s: string[]): boolean =>
  s.length >= 3 && s[0]!.length === 1 && s[0]!.toLowerCase() === s[1]![0]!.toLowerCase() && USERNAME.test(s[1]!)

/**
 * Best-effort username/body split. Whatnot renders the username in its own
 * element before the message, so the first text segment is the username when
 * it looks like one and something follows it. Falls back to "name: text",
 * then to no username at all. rawText is always kept alongside.
 */
export function splitRow(all: string[], rawText: string): SplitRow {
  const segments = avatarLetter(all) ? all.slice(1) : all
  const first = segments[0]
  if (segments.length >= 2 && first && USERNAME.test(first.replace(/:$/, ''))) {
    return { username: first.replace(/:$/, ''), body: segments.slice(1).join(' ') }
  }
  const m = /^([A-Za-z0-9._-]{1,40}):\s+(.+)$/.exec(rawText)
  if (m) return { username: m[1]!, body: m[2]! }
  return { username: '', body: rawText }
}
