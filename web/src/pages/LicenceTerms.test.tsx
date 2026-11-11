import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { LicenceTerms } from './LicenceTerms'

// Cut from LICENSE.md, one of each thing it uses. The whole file is read through this page
// in web/e2e/auth.spec.ts, which fails if any of its Markdown is left on screen.
const SAMPLE = `# Sales Ops — Proprietary Software License and Data Terms

**Copyright © 2026 SotoDev. All rights reserved.**

Version 1.0 · Effective 2026-11-11

> **Plain-language summary.** This summary is not part of the terms. If it
> differs from them, the terms below control.
>
> **Your data is yours.** Everything your shop puts into Sales Ops belongs
> to you.

---

## 1. Definitions

In this License:

- **"SotoDev"** means the individual who owns the Software and does business
  under the name SotoDev.
- **"Software"** means Sales Ops, including data contracts (such as
  \`shared/chat-event-batch.schema.json\`).

## 2. How this License applies

2.2 **Acceptance by the Client.** This License becomes a binding agreement
when the Client clicks to accept it.

(a) If this License and a signed Agreement conflict, the signed Agreement
controls.

## 6. Restrictions

The Client will not:

9. bypass any pairing or other protection measure; or let more than
   one person use the same login;
10. use the Software in violation of law; or
11. perform load testing of the hosted Software.

## 22. Contact

Anyone else may contact SotoDev through the repository owner's profile at
<https://github.com/jsoto007>.
`

const scrollTo = vi.fn()

function serve(body: string, init: ResponseInit = { status: 200, headers: { 'Content-Type': 'text/markdown; charset=utf-8' } }) {
  const fetchMock = vi.fn(async () => new Response(body, init))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const mount = () =>
  render(
    <MemoryRouter>
      <LicenceTerms />
    </MemoryRouter>,
  )

beforeEach(() => {
  scrollTo.mockClear()
  vi.stubGlobal('scrollTo', scrollTo)
})
afterEach(() => vi.unstubAllGlobals())

test('shows the licence file as a document: title, sections, lists and the summary, with no Markdown marks left', async () => {
  const fetchMock = serve(SAMPLE)
  mount()
  expect(screen.getByText('Loading the terms…')).toBeInTheDocument()
  expect(await screen.findByRole('heading', { level: 2, name: 'Sales Ops — Proprietary Software License and Data Terms' })).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledWith('/LICENSE.md')
  expect(scrollTo).toHaveBeenCalledWith(0, 0)

  const page = screen.getByRole('article', { name: 'Sales Ops License and Data Terms' })
  expect(page).toHaveAttribute('aria-busy', 'false')
  expect(page).toHaveTextContent('Copyright © 2026 SotoDev. All rights reserved.')
  expect(page).toHaveTextContent('Version 1.0 · Effective 2026-11-11')

  // The file's "#" title is the page's one h2; every numbered section is an h3, in order.
  expect(page.querySelectorAll('h2')).toHaveLength(1)
  expect(screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)).toEqual([
    '1. Definitions',
    '2. How this License applies',
    '6. Restrictions',
    '22. Contact',
  ])

  // The plain-language summary is set apart, in its own two paragraphs.
  const summary = page.querySelector('blockquote')!
  expect(within(summary).getByText('Your data is yours.').tagName).toBe('B')
  expect(summary.querySelectorAll('p')).toHaveLength(2)

  // Definitions are bullets; restrictions keep the numbers the file gives them.
  expect(page.querySelectorAll('ul > li')).toHaveLength(2)
  const restrictions = page.querySelector('ol')!
  expect(restrictions).toHaveAttribute('start', '9')
  const items = within(restrictions).getAllByRole('listitem')
  expect(items).toHaveLength(3)
  // Lines wrapped in the file are one sentence again, and two-digit items are still items.
  expect(items[0]).toHaveTextContent('bypass any pairing or other protection measure; or let more than one person use the same login;')
  expect(items[1]).toHaveTextContent(/^use the Software in violation of law; or$/)

  // Clause lead-ins are bold; file names are code; the one address is a link.
  expect(screen.getByText('Acceptance by the Client.').tagName).toBe('B')
  expect(screen.getByText('shared/chat-event-batch.schema.json').tagName).toBe('CODE')
  const profile = screen.getByRole('link', { name: 'https://github.com/jsoto007' })
  expect(profile).toHaveAttribute('href', 'https://github.com/jsoto007')
  expect(profile).toHaveAttribute('rel', 'noopener noreferrer')

  // Nothing of the file's own markup reaches the reader.
  expect(page.textContent).not.toMatch(/\*\*|`|#|>|<https?:/)

  expect(screen.getByRole('link', { name: 'open them as a plain text file' })).toHaveAttribute('href', '/LICENSE.md')
  expect(screen.getByRole('link', { name: 'Back to Sales Ops' })).toHaveAttribute('href', '/')
})

test('text in the file is shown as text, never run as HTML', async () => {
  serve('# Terms\n\n<img src=x onerror="alert(1)"> and <script>alert(2)</script>')
  mount()
  await screen.findByRole('heading', { level: 2, name: 'Terms' })
  const page = screen.getByRole('article')
  expect(page.querySelector('img, script')).toBeNull()
  expect(page).toHaveTextContent('<img src=x onerror="alert(1)"> and <script>alert(2)</script>')
})

test("when the file is missing the server answers with the app's own page: offer the plain file instead", async () => {
  serve('<!doctype html><title>Sales Ops</title>', { status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8' } })
  mount()
  expect(await screen.findByRole('alert')).toHaveTextContent('The terms could not be shown on this page.')
  expect(screen.getByRole('heading', { level: 2, name: 'Sales Ops License and Data Terms' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Open them as a plain text file' })).toHaveAttribute('href', '/LICENSE.md')
  expect(screen.getByRole('article')).not.toHaveTextContent('doctype')
})

test('a server error or a dropped connection gets the same message', async () => {
  serve('nope', { status: 500 })
  const { unmount } = mount()
  expect(await screen.findByRole('alert')).toBeInTheDocument()
  unmount()

  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    }),
  )
  mount()
  expect(await screen.findByRole('alert')).toBeInTheDocument()
})
