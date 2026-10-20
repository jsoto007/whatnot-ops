import { expect, type APIRequestContext, type Page } from '@playwright/test'

/**
 * Settings → Laptops → Connect a laptop, then redeem the code the way the
 * extension does (POST /api/pair, no session). Returns the device key.
 */
export async function connectLaptop(page: Page, request: APIRequestContext, name = 'Laptop'): Promise<string> {
  await page.getByRole('link', { name: 'Settings' }).click()
  await page.getByRole('button', { name: 'Connect a laptop' }).click()
  const sheet = page.getByRole('dialog', { name: 'Connect a laptop' })
  await sheet.getByLabel('Which laptop is this?').fill(name)
  await sheet.getByRole('button', { name: 'Get a code' }).click()
  const code = (await sheet.getByTestId('pairing-code').textContent())!.trim()
  expect(code).toMatch(/^[A-Z0-9]{4}-[A-Z0-9]{4}$/)
  const res = await request.post('/api/pair', { data: { code } })
  expect(res.status()).toBe(201)
  await expect(sheet.getByText(`${name} is connected`)).toBeVisible()
  await sheet.getByRole('button', { name: 'Done' }).click()
  return ((await res.json()) as { key: string }).key
}
