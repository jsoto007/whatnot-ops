import { expect, test } from '@playwright/test'

test('Flask serves the SPA and the SPA reaches the API', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Whatnot Ops' })).toBeVisible()
  await expect(page.getByTestId('api-status')).toHaveText('ok (db ok)')
})

test('client-side routes fall back to index.html', async ({ page }) => {
  await page.goto('/some/client/route')
  await expect(page.getByText('Not found.')).toBeVisible()
})

test('unknown /api paths are 404 JSON-less, not the SPA', async ({ request }) => {
  const res = await request.get('/api/nope')
  expect(res.status()).toBe(404)
})
