import { expect, test } from '@playwright/test'

test('Flask serves the SPA and anonymous visitors land on sign-in', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
})

test('client-side routes fall back to index.html', async ({ page }) => {
  await page.goto('/some/client/route')
  await expect(page.getByText('Not found.')).toBeVisible()
})

test('the footer is on the sign-in page and opens the user agreement and privacy pages', async ({ page }) => {
  await page.goto('/login')
  const footer = page.getByRole('contentinfo')
  await expect(footer).toContainText("Sales Ops is built to follow Whatnot's policies")
  await expect(footer.getByRole('link', { name: /Whatnot's terms and policies/ })).toHaveAttribute('href', 'https://www.whatnot.com/terms')
  await footer.getByRole('link', { name: 'User agreement' }).click()
  await expect(page).toHaveURL(/\/terms$/)
  await expect(page.getByRole('heading', { name: 'User agreement' })).toBeVisible()
  await page.getByRole('contentinfo').getByRole('link', { name: 'Privacy' }).click()
  await expect(page).toHaveURL(/\/privacy$/)
  await expect(page.getByRole('heading', { name: 'Privacy', exact: true })).toBeVisible()
  // Both are real addresses: Flask hands a direct visit to the SPA.
  await page.goto('/terms')
  await expect(page.getByRole('heading', { name: 'User agreement' })).toBeVisible()
  await page.getByRole('link', { name: 'Back to Sales Ops' }).click()
  await expect(page).toHaveURL(/\/login$/)
})

test('the API is healthy and unknown /api paths are 404', async ({ request }) => {
  expect((await request.get('/api/health')).ok()).toBe(true)
  expect((await request.get('/api/nope')).status()).toBe(404)
})
