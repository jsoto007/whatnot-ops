import { expect, test, type Page } from '@playwright/test'

const PW = 'correct-horse-battery'
const run = Date.now().toString(36)

async function signup(page: Page) {
  await page.goto('/signup')
  await page.getByLabel('Shop name').fill(`Inv ${run}`)
  await page.getByLabel('Your name').fill('Olive Owner')
  await page.getByLabel('Email').fill(`inv-${run}@shop.test`)
  await page.getByLabel('Password (10+ characters)').fill(PW)
  await page.getByRole('button', { name: 'Create shop' }).click()
  await expect(page).toHaveURL(/\/app\/shows$/)
}

test('products, aliases, adjustments, history and sheet import', async ({ page }) => {
  await signup(page)

  await page.getByRole('link', { name: 'Products' }).click()
  await page.getByLabel('Product name').fill('Red bundle')
  await page.getByLabel('SKU code (optional)').fill('RED')
  await page.getByLabel('Price (USD, optional)').fill('25')
  await page.getByRole('button', { name: 'Add product' }).click()
  await expect(page.locator('.product')).toContainText('Red bundle')
  await expect(page.locator('.product')).toContainText('$25.00')
  await page.getByLabel('New alias for RED').fill('red')
  await page.getByLabel('New alias for RED').press('Enter')
  await expect(page.locator('.chip', { hasText: /^red/ })).toBeVisible()

  await page.getByRole('link', { name: 'Inventory' }).click()
  const row = page.getByTestId('level-RED')
  await expect(row).toContainText('Red bundle')
  await page.getByLabel('Adjust RED').fill('+5')
  await page.getByLabel('Reason for RED').fill('initial count')
  await row.getByRole('button', { name: 'Apply' }).click()
  await expect(row.locator('td').nth(2)).toHaveText('5')
  await page.getByLabel('Adjust RED').fill('-7')
  await row.getByRole('button', { name: 'Apply' }).click()
  await expect(row).toContainText('cannot adjust below')
  await row.getByRole('button', { name: 'History' }).click()
  await expect(page.locator('.table.small')).toContainText('initial count')

  // Sheet import: preview first, nothing changes; then apply.
  await page.locator('input[type=file]').setInputFiles({
    name: 'sheet.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('Item,Start of Show Inventory,Current Stock\nRed bundle,12,9\nUltra Pack,4,4\n'),
  })
  await page.getByRole('button', { name: 'Preview' }).click()
  await expect(page.getByTestId('import-summary')).toContainText('1 set')
  await expect(page.getByTestId('import-summary')).toContainText('1 create')
  await expect(row.locator('td').nth(2)).toHaveText('5')
  await page.getByTestId('apply-import').click()
  await expect(page.getByTestId('level-RED').locator('td').nth(2)).toHaveText('9')
  await expect(page.getByTestId('level-ULTRA-PACK').locator('td').nth(2)).toHaveText('4')
})
