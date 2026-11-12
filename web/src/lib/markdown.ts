/**
 * The small part of Markdown that LICENSE.md is written in, parsed into plain data:
 * headings, paragraphs, bullet and numbered lists, quotes and rules. Lines wrapped in the
 * file are joined back into one paragraph or list item. `components/Markdown.tsx` draws it.
 * Anything else in the source stays as the text that was written.
 */

export type Block =
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'list'; ordered: boolean; start: number; items: string[] }
  | { kind: 'quote'; blocks: Block[] }
  | { kind: 'rule' }

const HEADING = /^(#{1,6})\s+(.*)$/
const RULE = /^(-{3,}|\*{3,}|_{3,})\s*$/
const ITEM = /^(?:[-*]|(\d+)\.)\s+(.*)$/
const INDENTED = /^\s+\S/

export function parseMarkdown(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, '\n').split('\n')
  const blocks: Block[] = []
  let paragraph: string[] = []
  const flush = () => {
    if (paragraph.length) blocks.push({ kind: 'paragraph', text: paragraph.join(' ') })
    paragraph = []
  }
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!
    if (!line.trim()) {
      flush()
      continue
    }
    const heading = HEADING.exec(line)
    if (heading) {
      flush()
      blocks.push({ kind: 'heading', level: heading[1]!.length, text: heading[2]!.trim() })
      continue
    }
    if (RULE.test(line)) {
      flush()
      blocks.push({ kind: 'rule' })
      continue
    }
    if (line.startsWith('>')) {
      flush()
      const quoted: string[] = []
      for (; i < lines.length && lines[i]!.startsWith('>'); i++) quoted.push(lines[i]!.replace(/^>\s?/, ''))
      i-- // the line after the quote is read by the loop
      blocks.push({ kind: 'quote', blocks: parseMarkdown(quoted.join('\n')) })
      continue
    }
    // A list starts on its own line, never in the middle of a wrapped paragraph (a line of
    // prose may happen to begin with "2." or a dash).
    const first = paragraph.length ? null : ITEM.exec(line)
    if (first) {
      const ordered = first[1] !== undefined
      const sameList = (text: string) => {
        const item = ITEM.exec(text)
        return item && (item[1] !== undefined) === ordered ? item : null
      }
      const items: string[][] = []
      for (; i < lines.length; i++) {
        const text = lines[i]!
        const item = sameList(text)
        if (item) items.push([item[2]!.trim()])
        else if (INDENTED.test(text)) items[items.length - 1]!.push(text.trim())
        else if (text.trim() || !(sameList(lines[i + 1] ?? '') || INDENTED.test(lines[i + 1] ?? ''))) break
      }
      i-- // the line that ended the list is read by the loop
      blocks.push({ kind: 'list', ordered, start: ordered ? Number(first[1]) : 1, items: items.map((item) => item.join(' ')) })
      continue
    }
    paragraph.push(line.trim())
  }
  flush()
  return blocks
}

export type Inline = { kind: 'text' | 'bold' | 'code' | 'link'; text: string }

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|<https?:\/\/[^\s<>]+>)/

/** One line of text split into plain runs, **bold**, `code` and <https://…> links. */
export function parseInline(text: string): Inline[] {
  return text
    .split(INLINE)
    .filter((part) => part !== '')
    .map((part): Inline => {
      if (part.length > 4 && part.startsWith('**') && part.endsWith('**')) return { kind: 'bold', text: part.slice(2, -2) }
      if (part.length > 2 && part.startsWith('`') && part.endsWith('`')) return { kind: 'code', text: part.slice(1, -1) }
      if (/^<https?:\/\/[^\s<>]+>$/.test(part)) return { kind: 'link', text: part.slice(1, -1) }
      return { kind: 'text', text: part }
    })
}
