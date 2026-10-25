/** Tag + class signature; siblings sharing it are "the same kind of element". */
export const sig = (el: Element): string => el.tagName + '|' + el.className

/** CSS selector for elements shaped like `el`: tag plus every class. */
export function buildSelector(el: Element): string {
  const tag = el.tagName.toLowerCase()
  const classes = [...el.classList].map((c) => '.' + CSS.escape(c)).join('')
  return tag + classes
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
      if (!best || (classed && !best.classed) || (classed === best.classed && g.length > best.count)) {
        best = { count: g.length, el: first, classed }
      }
    }
  }
  return best ? { selector: buildSelector(best.el), sample: best.el, count: best.count } : null
}
