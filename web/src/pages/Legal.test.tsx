import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { LEGAL_UPDATED, WHATNOT_POLICIES_URL } from '../lib/legal'
import { Privacy, Terms } from './Legal'

const scrollTo = vi.fn()

beforeEach(() => {
  scrollTo.mockClear()
  vi.stubGlobal('scrollTo', scrollTo)
})
afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

const mount = (page: React.ReactNode) => render(<MemoryRouter>{page}</MemoryRouter>)

test('the user agreement puts Whatnot policy first and says features may change because of it', () => {
  mount(<Terms />)
  expect(screen.getByRole('heading', { level: 2, name: 'User agreement' })).toBeInTheDocument()
  expect(screen.getByText(`Last updated ${LEGAL_UPDATED}`)).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: "Whatnot's policies come first" })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: "Whatnot's terms and policies" })).toHaveAttribute('href', WHATNOT_POLICIES_URL)
  const page = screen.getByRole('article')
  expect(page).toHaveTextContent('not made by, affiliated with, sponsored by or endorsed by Whatnot')
  expect(page).toHaveTextContent('a feature here may work differently, be limited, be paused or be removed')
  expect(page).toHaveTextContent("follow Whatnot's policy and tell the shop's owner")
  expect(page).toHaveTextContent('By signing in or using Sales Ops you accept it')
})

test("the user agreement gives way to the shop's own written agreement", () => {
  mount(<Terms />)
  expect(screen.getByRole('heading', { name: "The shop's own agreement" })).toBeInTheDocument()
  expect(screen.getByRole('article')).toHaveTextContent("Where that agreement and this page say different things, the shop's agreement applies")
})

test('privacy says what is kept about buyers and what is not', () => {
  mount(<Privacy />)
  expect(screen.getByRole('heading', { level: 2, name: 'Privacy' })).toBeInTheDocument()
  const page = screen.getByRole('article')
  expect(page).toHaveTextContent('No street address, email address or phone number is kept')
  expect(page).toHaveTextContent('Shipping labels are fetched from Whatnot when someone prints them and are not saved')
  expect(page).toHaveTextContent('Sales Ops itself sets one cookie')
  expect(page).toHaveTextContent('There are no advertising or analytics cookies')
})

// api services/accounts.py writes the address into the activity record at every sign-in, and
// services/retention.py never deletes that record: the page must not say "a short time" only.
test('privacy says sign-ins keep the network address, for how long, and who carries the traffic', () => {
  mount(<Privacy />)
  const page = screen.getByRole('article')
  expect(page).toHaveTextContent('Each sign-in, and each failed attempt to sign in to an account, is recorded with the network address it came from')
  expect(page).toHaveTextContent('is kept with the record of sign-ins described above')
  expect(page).toHaveTextContent('the record of changes and sign-ins, with the network addresses in it, are kept for as long as the shop uses Sales Ops')
  expect(page).toHaveTextContent('The counts used to block abuse are deleted after about a day')
  expect(page).toHaveTextContent('the network service in front of the hosting that carries every request to it and guards it against attacks')
})

test('both pages start at the top and lead back to the app', () => {
  mount(<Terms />)
  expect(scrollTo).toHaveBeenCalledWith(0, 0)
  expect(screen.getByRole('link', { name: 'Back to Sales Ops' })).toHaveAttribute('href', '/')
})

test('questions go to the shop owner, and to the contact address when one is set', () => {
  const { unmount } = mount(<Terms />)
  expect(screen.getByRole('article')).toHaveTextContent("Ask the owner of your shop's account.")
  unmount()

  vi.stubEnv('VITE_SUPPORT_EMAIL', 'help@shop.test')
  mount(<Privacy />)
  expect(screen.getByRole('article')).toHaveTextContent("Ask the owner of your shop's account, or write to help@shop.test.")
  expect(screen.getByRole('link', { name: 'help@shop.test' })).toHaveAttribute('href', 'mailto:help@shop.test')
})

test('no setting, vendor or internal name reaches the screen', () => {
  for (const page of [<Terms key="t" />, <Privacy key="p" />]) {
    const { unmount } = mount(page)
    expect(screen.getByRole('article').textContent).not.toMatch(/VITE_|_EMAIL|argon|Render|Cloudflare|Mailgun|Anthropic|Claude|Postgres|wn_session|localStorage/i)
    unmount()
  }
})
