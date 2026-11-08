/** Tag + class signature; siblings sharing it are "the same kind of element". */
export const sig = (el: Element): string => el.tagName + '|' + el.className

/** Tag, every class, and a `data-testid` when there is one. */
function ownSelector(el: Element): string {
  const tag = el.tagName.toLowerCase()
  const classes = [...el.classList].map((c) => '.' + CSS.escape(c)).join('')
  const testId = el.getAttribute('data-testid')
  return tag + classes + (testId ? `[data-testid="${CSS.escape(testId)}"]` : '')
}

/**
 * CSS selector for elements shaped like `el`: tag plus every class. A row with
 * nothing of its own to go by (Whatnot's seller dashboard: bare `<div data-index>`
 * in a react-virtuoso list) is anchored to its parent as a direct child,
 * `div[data-testid="virtuoso-item-list"] > div`: a bare `div` would match every
 * box inside every message and record each piece as a message of its own.
 */
export function buildSelector(el: Element, depth = 0): string {
  const own = ownSelector(el)
  const bare = own === el.tagName.toLowerCase()
  const parent = el.parentElement
  if (!bare || !parent || parent === el.ownerDocument.body || depth >= 3) return own
  return `${buildSelector(parent, depth + 1)} > ${own}`
}

/**
 * True when `selector` matches an element inside another match under `root`. A
 * chat row never contains another chat row, so such a selector is picking up
 * the pieces of a message (or the whole list) and must not be recorded with.
 */
export function nests(root: Element, selector: string): boolean {
  let matches: Element[]
  try {
    matches = [...root.querySelectorAll(selector)]
  } catch {
    return true // not a valid selector: never record with it
  }
  if (root.matches(selector) && matches.length) return true
  return matches.some((el) => {
    const up = el.parentElement?.closest(selector)
    return !!up && root.contains(up)
  })
}

/** Nearest scrolling ancestor, else document.body. */
export function scrollParent(el: Element | null): Element {
  const doc = el?.ownerDocument ?? document
  let cur: Element | null = el
  while (cur && cur !== doc.body) {
    const s = doc.defaultView!.getComputedStyle(cur)
    if (/(auto|scroll)/.test(s.overflowY) && cur.scrollHeight > cur.clientHeight) return cur
    cur = cur.parentElement
  }
  return doc.body
}

/**
 * The element to watch for `row`: the chat's scrolling box, or the row's own
 * list when the chat does not overflow yet (a show that just started). Never
 * the whole body.
 */
export function chatContainer(row: Element): Element {
  const box = scrollParent(row)
  return box === row.ownerDocument.body ? (row.parentElement ?? box) : box
}

/**
 * Walk up from the clicked element to the ancestor with the most siblings of
 * the same signature. That ancestor is the chat row. Needs at least `minCount`
 * look-alike siblings, otherwise the click was not on a list of messages.
 */
export function findRow(target: Element, box: Element, minCount = 3): Element | null {
  const item = virtualItem(target, box)
  if (item) return item
  let best: Element | null = null
  let bestCount = 1
  let el: Element | null = target
  while (el && el !== box && el.parentElement) {
    const s = sig(el)
    const count = [...el.parentElement.children].filter((c) => sig(c) === s).length
    if (count >= bestCount) {
      best = el
      bestCount = count
    }
    el = el.parentElement
  }
  return bestCount >= minCount ? best : null
}

/**
 * The item of a virtualized list (react-virtuoso, TanStack Virtual: `data-index` on
 * every item) that holds `target`. The list itself says these are its rows, so one
 * or two messages are enough: a show's chat starts with "shared this show" and
 * "joined", and the host presses Start before the third message arrives.
 */
function virtualItem(target: Element, box: Element): Element | null {
  let el: Element | null = target
  while (el && el !== box && el.parentElement) {
    if (el.hasAttribute('data-index') && [...el.parentElement.children].every((c) => c.hasAttribute('data-index'))) {
      return el
    }
    el = el.parentElement
  }
  return null
}

export interface Detected {
  selector: string
  sample: Element
  count: number
}

/**
 * Without a click: the largest group of same-signature siblings with text
 * inside `container`. Used to re-find the rows when Whatnot re-renders the
 * chat with new class names. Prefers elements that carry classes so a bare
 * tag selector cannot swallow the whole page.
 */
export function autoDetectRows(container: Element, minCount = 3): Detected | null {
  let best: { count: number; el: Element; classed: boolean } | null = null
  const candidates: Element[] = [container, ...container.querySelectorAll('*')]
  for (const parent of candidates) {
    if (parent.children.length < minCount) continue
    const groups = new Map<string, Element[]>()
    for (const c of parent.children) {
      const s = sig(c)
      const g = groups.get(s)
      if (g) g.push(c)
      else groups.set(s, [c])
    }
    for (const g of groups.values()) {
      if (g.length < minCount) continue
      const first = g[0]!
      if (!g.some((e) => (e.textContent ?? '').trim())) continue
      const classed = first.classList.length > 0
      if (nests(container, buildSelector(first))) continue
      if (!best || (classed && !best.classed) || (classed === best.classed && g.length > best.count)) {
        best = { count: g.length, el: first, classed }
      }
    }
  }
  return best ? { selector: buildSelector(best.el), sample: best.el, count: best.count } : null
}
