import { expect, test, type Page } from '@playwright/test'

const PW = 'correct-horse-battery'
const run = Date.now().toString(36)

async function signup(page: Page, email: string, org: string) {
  await page.goto('/signup')
  await page.getByLabel('Shop name').fill(org)
  await page.getByLabel('Your name').fill('Olive Owner')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password (10+ characters)').fill(PW)
  await page.getByLabel('I have read and accept the Sales Ops License and Data Terms').check()
  await page.getByRole('button', { name: 'Create shop' }).click()
  await expect(page).toHaveURL(/\/app$/)
}

test('owner signs up, creates a device key, invites fulfillment who gets a limited app', async ({ page, browser }) => {
  const owner = `owner-${run}@shop.test`
  await signup(page, owner, `Shop ${run}`)
  await expect(page.getByText('No shows yet')).toBeVisible()
  const ownerNav = page.getByRole('navigation', { name: 'Sections' })
  await expect(ownerNav).toContainText('Team')
  await expect(ownerNav).toContainText('Settings')

  // Device key for a laptop, shown once.
  await page.getByRole('link', { name: 'Settings' }).click()
  // Step 1 offers the extension download when the server has a build of it.
  await expect(page.getByTestId('download-extension').or(page.getByTestId('extension-missing'))).toBeVisible()
  await page.getByText('Advanced: create a device key instead').click()
  await page.getByLabel('Laptop name').fill('Front laptop')
  await page.getByRole('button', { name: 'Create key' }).click()
  await expect(page.getByTestId('new-key')).toContainText('wn_')
  await expect(page.locator('.table').first()).toContainText('Front laptop')

  // Invite a fulfillment teammate; Mailgun is unset in test, so the accept link is shown.
  await page.getByRole('link', { name: 'Team' }).click()
  await page.getByLabel('Email').fill(`pat-${run}@shop.test`)
  await page.getByLabel('Role').selectOption('fulfillment')
  await page.getByRole('button', { name: 'Send invite' }).click()
  await expect(page.getByTestId('invite-result')).toContainText('Email is not configured')
  const acceptUrl = await page.getByTestId('accept-url').textContent()
  await expect(page.locator('table').nth(1)).toContainText(`pat-${run}@shop.test`)

  const ctx = await browser.newContext()
  const pat = await ctx.newPage()
  await pat.goto(new URL(acceptUrl!).pathname)
  await expect(pat.getByRole('heading', { name: `Join Shop ${run}` })).toBeVisible()
  await pat.getByLabel('Your name').fill('Pat Packer')
  await pat.getByLabel('Password (10+ characters)').fill(PW)
  await pat.getByRole('button', { name: 'Create account' }).click()
  await expect(pat).toHaveURL(/\/app$/)
  // The landing page must actually render for this role, not just route there.
  await expect(pat.getByRole('heading', { name: 'Needs attention' })).toBeVisible()
  await expect(pat.getByText('No shows yet')).toBeVisible()
  await expect(pat.getByText('Could not load the dashboard.')).toHaveCount(0)
  const nav = pat.getByRole('navigation', { name: 'Sections' })
  await expect(nav).toContainText('Dashboard')
  await expect(nav).toContainText('Orders')
  await expect(nav).not.toContainText('Team')
  await pat.goto('/app/team')
  await expect(pat.getByText('You do not have access')).toBeVisible()
  await ctx.close()

  // The owner sees Pat in the team and the activity log records it.
  await page.reload()
  await expect(page.locator('table').first()).toContainText('Pat Packer')
  await page.getByRole('link', { name: 'Settings' }).click()
  await expect(page.getByText('Joined the shop as Fulfillment')).toBeVisible()
  await expect(page.getByText('invite.accepted')).toHaveCount(0) // words, never action codes

  // Sign out and back in.
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page).toHaveURL(/\/login$/)
  await page.getByLabel('Email').fill(owner)
  await page.getByLabel('Password').fill('not-the-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('alert')).toHaveText('wrong email or password')
  await page.getByLabel('Password').fill(PW)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page).toHaveURL(/\/app$/)
})

test('the shop is created only with the licence ticked; the terms open beside the form', async ({ page, context }) => {
  const email = `licence-${run}@shop.test`
  await page.goto('/signup')
  await page.getByLabel('Shop name').fill(`Licence ${run}`)
  await page.getByLabel('Your name').fill('Olive Owner')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password (10+ characters)').fill(PW)
  const box = page.getByLabel('I have read and accept the Sales Ops License and Data Terms')
  await expect(box).not.toBeChecked()
  // Unticked, the browser keeps the form (the API refuses it too: api/tests/test_licence.py).
  await page.getByRole('button', { name: 'Create shop' }).click()
  await expect(page).toHaveURL(/\/signup$/)
  expect(await box.evaluate((el) => (el as HTMLInputElement).validity.valueMissing)).toBe(true)

  // The terms open in a new tab as a page set out to be read (headings and lists, not the
  // file's raw text); the form keeps what was typed.
  const [terms] = await Promise.all([
    context.waitForEvent('page'),
    page.getByRole('link', { name: 'Sales Ops License and Data Terms' }).click(),
  ])
  await terms.waitForLoadState()
  expect(new URL(terms.url()).pathname).toBe('/license')
  await expect(terms.getByRole('heading', { level: 2, name: 'Sales Ops — Proprietary Software License and Data Terms' })).toBeVisible()
  await expect(terms.getByRole('heading', { level: 3, name: '7. How SotoDev handles Client Data' })).toBeVisible()
  const document = terms.getByRole('article')
  await expect(document).toContainText('Version 1.0')
  await expect(document).toContainText('Copyright © 2026 SotoDev')
  // The whole of LICENSE.md is drawn: its 22 sections, the 11 numbered restrictions, and none
  // of the file's own Markdown marks (fails when the file starts using something the page cannot draw).
  await expect(document.getByRole('heading', { level: 3 })).toHaveCount(22)
  await expect(document.locator('ol > li')).toHaveCount(11)
  expect(await document.innerText()).not.toMatch(/\*\*|`|(^|\n)\s*#{1,6} |(^|\n)\s*> |<https?:/)
  // The file itself is still served beside the app, for anyone who wants a copy.
  const file = await terms.request.get('/LICENSE.md')
  expect(file.ok()).toBe(true)
  expect(await file.text()).toContain('Version 1.0 · Effective')
  await terms.close()
  await expect(page.getByLabel('Email')).toHaveValue(email)

  await box.check()
  await page.getByRole('button', { name: 'Create shop' }).click()
  await expect(page).toHaveURL(/\/app$/)
  // Accepted at sign-up: no banner, and the activity log says so in words.
  await expect(page.getByTestId('licence-banner')).toHaveCount(0)
  await page.getByRole('link', { name: 'Settings' }).click()
  await expect(page.getByText(/Accepted the Sales Ops License and Data Terms, version 1\.0/)).toBeVisible()
  await expect(page.getByText('licence.accepted')).toHaveCount(0)
  await expect(page.getByTestId('extension-licence')).toContainText('Sales Ops License and Data Terms')
})

test('password reset by link', async ({ page }) => {
  const email = `reset-${run}@shop.test`
  await signup(page, email, `Reset ${run}`)
  await page.getByRole('button', { name: 'Sign out' }).click()
  await page.goto('/forgot')
  await page.getByLabel('Email').fill(email)
  await page.getByRole('button', { name: 'Send reset link' }).click()
  const link = page.getByTestId('dev-reset-link')
  await expect(link).toBeVisible()
  await page.goto(new URL((await link.getAttribute('href'))!).pathname)
  await page.getByLabel('New password (10+ characters)').fill('a-brand-new-password')
  await page.getByRole('button', { name: 'Save password' }).click()
  await expect(page).toHaveURL(/\/app$/)
})
