import { expect, test, type Page } from '@playwright/test'
import { cell } from './stock'

const PW = 'correct-horse-battery'
const run = Date.now().toString(36)

async function signup(page: Page) {
  await page.goto('/signup')
  await page.getByLabel('Shop name').fill(`Inv ${run}`)
  await page.getByLabel('Your name').fill('Olive Owner')
  await page.getByLabel('Email').fill(`inv-${run}@shop.test`)
  await page.getByLabel('Password (10+ characters)').fill(PW)
  await page.getByRole('button', { name: 'Create shop' }).click()
  await expect(page).toHaveURL(/\/app$/)
}

test('products, receiving, the item panel, a shelf count and the sheet import', async ({ page }) => {
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

  // Receive a delivery: 5 of the known item plus a brand-new item created on the way in.
  await page.getByRole('link', { name: 'Inventory' }).click()
  await expect(page.getByTestId('level-RED')).toContainText('Red bundle')
  await page.getByRole('button', { name: 'Receive stock', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Receive stock' })
  const item = dialog.getByLabel('Item, line 1')
  await item.selectOption((await item.locator('option', { hasText: '· RED (' }).getAttribute('value'))!)
  await dialog.getByLabel('Quantity, line 1').fill('5')
  await dialog.getByRole('button', { name: 'New item' }).click()
  await dialog.getByLabel('New item name, line 2').fill('Holo slab')
  await dialog.getByLabel('SKU code, line 2').fill('HOLO')
  await dialog.getByLabel('Price, line 2').fill('15')
  await dialog.getByLabel('Low-stock alert at, line 2').fill('2')
  await dialog.getByLabel('Quantity, line 2').fill('8')
  await dialog.getByLabel('Note (supplier, invoice, box)').fill('initial count')
  await expect(page.getByTestId('receive-total')).toHaveText('2 items · 13 units')
  await page.getByTestId('receive-submit').click()
  await expect(page.getByRole('status')).toContainText('Received 13 units')
  await expect(page.getByRole('status')).toContainText('8 Holo slab (new)')
  await expect(cell(page, 'RED', 'on-hand')).toHaveText('5')
  await expect(cell(page, 'HOLO', 'on-hand')).toHaveText('8')

  // The item panel: removing more than the shelf holds is refused; a reasoned removal is recorded.
  await page.getByTestId('level-RED').getByRole('button', { name: 'Red bundle' }).click()
  const panel = page.getByRole('dialog', { name: 'Red bundle' })
  await panel.getByRole('radio', { name: 'Remove stock' }).click()
  const howMany = panel.getByRole('textbox', { name: 'How many to remove' })
  await howMany.fill('7')
  await expect(panel.getByTestId('adjust-preview')).toHaveText('Only 5 on the shelf')
  await expect(panel.getByRole('button', { name: 'Remove 7 from the shelf' })).toBeDisabled()
  await howMany.fill('1')
  await panel.getByRole('button', { name: 'How many to remove: one more' }).click()
  await expect(howMany).toHaveValue('2')
  await expect(panel.getByTestId('adjust-preview')).toContainText('On shelf 5')
  await expect(panel.getByTestId('adjust-preview')).toContainText('3')
  await panel.getByRole('button', { name: 'Damaged' }).click()
  await panel.getByRole('button', { name: 'Remove 2 from the shelf' }).click()
  await expect(panel.getByTestId('panel-on-hand')).toHaveText('3')
  await expect(panel.getByTestId('item-history')).toContainText('Removed 2 · Damaged')
  await expect(panel.getByTestId('item-history')).toContainText('Received 5 · initial count')
  await expect(panel.getByRole('button', { name: 'Save' })).toHaveCount(0) // nothing changed yet
  await panel.getByRole('textbox', { name: 'Warn me when available drops to' }).fill('3')
  await panel.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByRole('status')).toContainText('alerts at 3')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(cell(page, 'RED', 'available')).toContainText('low')
  await page.getByRole('tab', { name: /^Low/ }).click()
  await expect(page.getByTestId('level-HOLO')).toHaveCount(0)
  await page.getByRole('tab', { name: /^All/ }).click()
  await page.getByLabel('Search inventory').fill('holo')
  await expect(page.getByTestId('level-RED')).toHaveCount(0)
  await page.getByLabel('Search inventory').fill('')

  // A shelf count: only typed rows count; the difference shows before saving.
  await page.getByRole('button', { name: 'Count stock' }).click()
  const count = page.getByRole('dialog', { name: 'Count stock' })
  await count.getByLabel('Counted RED').fill('1')
  await count.getByLabel('Counted HOLO').fill('8')
  await expect(count.locator('tr', { hasText: 'Red bundle' })).toContainText('−2')
  await expect(count.locator('tr', { hasText: 'Holo slab' })).toContainText('matches')
  await page.getByTestId('count-submit').click()
  await expect(page.getByRole('status')).toContainText('1 item changed (net −2)')
  await expect(cell(page, 'RED', 'on-hand')).toHaveText('1')
  await expect(page.getByTestId('activity')).toContainText('Counted: shelf set to 1')

  // Sheet import: preview first, nothing changes; then apply.
  await page.getByTestId('toggle-import').click()
  await page.locator('input[type=file]').setInputFiles({
    name: 'sheet.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('Item,Start of Show Inventory,Current Stock\nRed bundle,12,9\nUltra Pack,4,4\n'),
  })
  await page.getByRole('button', { name: 'Preview' }).click()
  await expect(page.getByTestId('import-summary')).toContainText('1 set')
  await expect(page.getByTestId('import-summary')).toContainText('1 create')
  await expect(cell(page, 'RED', 'on-hand')).toHaveText('1')
  await page.getByTestId('apply-import').click()
  await expect(cell(page, 'RED', 'on-hand')).toHaveText('9')
  await expect(cell(page, 'ULTRA-PACK', 'on-hand')).toHaveText('4')
})
