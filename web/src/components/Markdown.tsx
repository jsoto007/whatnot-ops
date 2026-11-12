import { Fragment, type ReactNode } from 'react'
import { parseInline, parseMarkdown, type Block } from '../lib/markdown'

/** Runs of text as elements. The text itself is only ever a React text node, never HTML. */
function inline(text: string): ReactNode[] {
  return parseInline(text).map((run, i) => {
    if (run.kind === 'bold') return <b key={i}>{run.text}</b>
    if (run.kind === 'code') return <code key={i}>{run.text}</code>
    if (run.kind === 'link')
      return (
        <a key={i} href={run.text} target="_blank" rel="noopener noreferrer">
          {run.text}
        </a>
      )
    return <Fragment key={i}>{run.text}</Fragment>
  })
}

function draw(blocks: Block[], headingShift: number): ReactNode[] {
  return blocks.map((block, i) => {
    switch (block.kind) {
      case 'heading': {
        const Tag = `h${Math.min(6, block.level + headingShift)}` as 'h2'
        return <Tag key={i}>{inline(block.text)}</Tag>
      }
      case 'paragraph':
        return <p key={i}>{inline(block.text)}</p>
      case 'rule':
        return <hr key={i} />
      case 'quote':
        return <blockquote key={i}>{draw(block.blocks, headingShift)}</blockquote>
      case 'list': {
        const items = block.items.map((item, j) => <li key={j}>{inline(item)}</li>)
        return block.ordered ? (
          <ol key={i} start={block.start}>
            {items}
          </ol>
        ) : (
          <ul key={i}>{items}</ul>
        )
      }
    }
  })
}

/**
 * A plain-text document written in simple Markdown (lib/markdown.ts), drawn as headings,
 * paragraphs and lists. `headingShift` moves every heading down, so a file's "#" title can
 * sit under the page's own h1.
 */
export function Markdown({ source, headingShift = 0 }: { source: string; headingShift?: number }) {
  return <>{draw(parseMarkdown(source), headingShift)}</>
}
