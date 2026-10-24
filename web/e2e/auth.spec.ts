import { expect, test, type Page } from '@playwright/test'

const PW = 'correct-horse-battery'
const run = Date.now().toString(36)

async function signup(page: Page, email: string, org: string) {
  await page.goto('/signup')
  await page.getByLabel('Shop name').fill(org)
  await page.getByLabel('Your name').fill('Olive Owner')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password (10+ characters)').fill(PW)
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
  await expect(page.getByText('invite.accepted')).toBeVisible()

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
