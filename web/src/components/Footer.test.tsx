import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { WHATNOT_POLICIES_URL, supportEmail } from '../lib/legal'
import { Footer } from './Footer'

const mount = () =>
  render(
    <MemoryRouter>
      <Footer />
    </MemoryRouter>,
  )

afterEach(() => vi.unstubAllEnvs())

test('links to the user agreement, privacy and Whatnot, and says use may change with Whatnot policy', () => {
  mount()
  const footer = screen.getByRole('contentinfo')
  const legal = within(footer).getByRole('navigation', { name: 'Legal' })
  expect(within(legal).getByRole('link', { name: 'User agreement' })).toHaveAttribute('href', '/terms')
  expect(within(legal).getByRole('link', { name: 'Privacy' })).toHaveAttribute('href', '/privacy')
  expect(footer).toHaveTextContent("Sales Ops is built to follow Whatnot's policies")
  expect(footer).toHaveTextContent('how you may use it, can change to stay in line with them')
  expect(footer).toHaveTextContent('By using Sales Ops you accept the user agreement')
  expect(footer).toHaveTextContent(`© ${new Date().getFullYear()} Sales Ops`)
  expect(footer).toHaveTextContent('not affiliated with, sponsored by or endorsed by Whatnot')
})

test("Whatnot's policies open in a new tab without handing the page over, and say so", () => {
  mount()
  const link = screen.getByRole('link', { name: "Whatnot's terms and policies (opens in a new tab)" })
  expect(link).toHaveAttribute('href', WHATNOT_POLICIES_URL)
  expect(link).toHaveAttribute('target', '_blank')
  expect(link.getAttribute('rel')).toContain('noopener')
})

test('Contact appears only when a real address was set at build time', () => {
  mount()
  expect(screen.queryByRole('link', { name: 'Contact' })).toBeNull()

  vi.stubEnv('VITE_SUPPORT_EMAIL', 'help@shop.test')
  mount()
  expect(screen.getByRole('link', { name: 'Contact' })).toHaveAttribute('href', 'mailto:help@shop.test')
})

test('a malformed contact address is ignored rather than put in a link', () => {
  for (const bad of ['', '   ', 'nobody', 'a@b', 'a b@c.test', 'x@y.test?subject=<script>', 'javascript:alert(1)']) {
    vi.stubEnv('VITE_SUPPORT_EMAIL', bad)
    expect(supportEmail(), bad).toBeNull()
  }
  vi.stubEnv('VITE_SUPPORT_EMAIL', '  help@shop.test ')
  expect(supportEmail()).toBe('help@shop.test')
})
