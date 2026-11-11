import { parseInline, parseMarkdown } from './markdown'

test('headings, wrapped paragraphs and rules', () => {
  expect(parseMarkdown('# Title\n\nFirst line\nsecond line.\n\n---\n\n## 2. Section\nBody')).toEqual([
    { kind: 'heading', level: 1, text: 'Title' },
    { kind: 'paragraph', text: 'First line second line.' },
    { kind: 'rule' },
    { kind: 'heading', level: 2, text: '2. Section' },
    { kind: 'paragraph', text: 'Body' },
  ])
})

test('a bullet list joins its wrapped lines and ends at the next paragraph', () => {
  expect(parseMarkdown('In this License:\n\n- **"A"** means one\n  thing.\n- **"B"** means another.\n\nAfter.')).toEqual([
    { kind: 'paragraph', text: 'In this License:' },
    { kind: 'list', ordered: false, start: 1, items: ['**"A"** means one thing.', '**"B"** means another.'] },
    { kind: 'paragraph', text: 'After.' },
  ])
})

test('a numbered list keeps its first number, two-digit items and blank lines between items', () => {
  expect(parseMarkdown('9. nine,\n   still nine;\n10. ten; or\n\n11. eleven.\n\n## Next')).toEqual([
    { kind: 'list', ordered: true, start: 9, items: ['nine, still nine;', 'ten; or', 'eleven.'] },
    { kind: 'heading', level: 2, text: 'Next' },
  ])
})

test('a bullet list and a numbered list side by side stay two lists', () => {
  expect(parseMarkdown('- a\n1. b')).toEqual([
    { kind: 'list', ordered: false, start: 1, items: ['a'] },
    { kind: 'list', ordered: true, start: 1, items: ['b'] },
  ])
})

test('a line of prose that begins like a list item stays in its paragraph', () => {
  expect(parseMarkdown('Sections 4, 7 and\n8. of this License\n- apply.')).toEqual([{ kind: 'paragraph', text: 'Sections 4, 7 and 8. of this License - apply.' }])
})

test('a quote holds its own paragraphs', () => {
  expect(parseMarkdown('> **Summary.** Not part of\n> the terms.\n>\n> **Your data is yours.**\n\nAfter')).toEqual([
    {
      kind: 'quote',
      blocks: [
        { kind: 'paragraph', text: '**Summary.** Not part of the terms.' },
        { kind: 'paragraph', text: '**Your data is yours.**' },
      ],
    },
    { kind: 'paragraph', text: 'After' },
  ])
})

test('Windows line endings read the same', () => {
  expect(parseMarkdown('# T\r\n\r\nA\r\nB\r\n')).toEqual([
    { kind: 'heading', level: 1, text: 'T' },
    { kind: 'paragraph', text: 'A B' },
  ])
})

test('inline: bold, code and links; everything else is plain text', () => {
  expect(parseInline('2.1 **To everyone.** See `NOTICE.md` at <https://example.test/a?b=1>.')).toEqual([
    { kind: 'text', text: '2.1 ' },
    { kind: 'bold', text: 'To everyone.' },
    { kind: 'text', text: ' See ' },
    { kind: 'code', text: 'NOTICE.md' },
    { kind: 'text', text: ' at ' },
    { kind: 'link', text: 'https://example.test/a?b=1' },
    { kind: 'text', text: '.' },
  ])
  // Not links, not markup: a script tag, another scheme, a lone star.
  expect(parseInline('<script>alert(1)</script> <javascript:alert(1)> 2 * 3')).toEqual([
    { kind: 'text', text: '<script>alert(1)</script> <javascript:alert(1)> 2 * 3' },
  ])
})
