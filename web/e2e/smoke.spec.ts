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

test('the API is healthy and unknown /api paths are 404', async ({ request }) => {
  expect((await request.get('/api/health')).ok()).toBe(true)
  expect((await request.get('/api/nope')).status()).toBe(404)
})
