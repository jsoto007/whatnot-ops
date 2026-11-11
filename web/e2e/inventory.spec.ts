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
  await page.getByLabel('I have read and accept the Sales Ops License and Data Terms').check()
  await page.getByRole('button', { name: 'Create shop' }).click()
  await expect(page).toHaveURL(/\/app$/)
}

test('products, adding an item, the shelf stepper, the item panel, sorting and the sheet import', async ({ page }) => {
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

  // Add a brand-new item from the centred dialog: product, SKU, shelf stock and an alias in one go.
  await page.getByRole('link', { name: 'Inventory' }).click()
  await expect(page.getByTestId('level-RED')).toContainText('Red bundle')
  await page.getByRole('button', { name: 'Add new item' }).click()
  const add = page.getByRole('dialog', { name: 'Add new item' })
  await add.getByRole('button', { name: 'Add to inventory' }).click()
  await expect(add.getByRole('alert')).toHaveText('Give the item a name.')
  await add.getByLabel('Item name').fill('Holo slab')
  await add.getByLabel('SKU code (optional)').fill('HOLO')
  await add.getByLabel('Price (USD)').fill('15')
  await add.getByLabel('Quantity on shelf').fill('8')
  await add.getByLabel('Low-stock alert at').fill('2')
  await add.getByLabel('Weight of one unit (optional)', { exact: true }).fill('1.5')
  await add.getByLabel('Weight of one unit (optional): unit').selectOption('lb')
  await add.getByLabel(/^Chat aliases/).fill('holo, slab')
  await add.getByRole('button', { name: 'Add to inventory' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByRole('status')).toContainText('Added Holo slab with 8 units on the shelf')
  await expect(cell(page, 'HOLO', 'on-hand')).toHaveText('8')
  await expect(cell(page, 'HOLO', 'weight')).toHaveText('· 1 lb 8 oz')

  // The row stepper: type a number (or − / +), then ✓ saves it as a one-line count.
  const red = page.getByTestId('level-RED')
  await expect(red.getByRole('button', { name: /^Save / })).toHaveCount(0)
  await red.getByRole('spinbutton', { name: 'Red bundle on shelf' }).fill('4')
  await red.getByRole('button', { name: 'One more Red bundle' }).click()
  await expect(red.getByRole('spinbutton')).toHaveValue('5')
  await red.getByRole('button', { name: 'Save Red bundle at 5' }).click()
  await expect(page.getByRole('status')).toContainText('Red bundle: shelf set to 5 (+5)')
  await expect(cell(page, 'RED', 'on-hand')).toHaveText('5')
  await expect(red.getByRole('button', { name: /^Save / })).toHaveCount(0)

  // The item panel: removing more than the shelf holds is refused; a reasoned removal is recorded.
  await page.getByTestId('level-RED').getByRole('button', { name: 'Red bundle', exact: true }).click()
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
  await expect(panel.getByTestId('item-history')).toContainText('Counted: shelf set to 5')
  await expect(panel.getByRole('button', { name: 'Save' })).toHaveCount(0) // nothing changed yet
  await panel.getByRole('textbox', { name: 'Warn me when available drops to' }).fill('3')
  await panel.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByRole('status')).toContainText('alerts at 3')

  // Weight: typed in ounces or pounds, read back the way a postal scale shows it.
  await expect(panel.getByTestId('weight-hint')).toContainText('Not weighed yet')
  const weighs = panel.getByLabel('One unit weighs', { exact: true })
  await weighs.fill('0')
  await panel.getByRole('button', { name: 'Save weight' }).click()
  await expect(panel.getByRole('alert')).toHaveText('Weight must be more than 0.')
  await weighs.fill('4.5')
  await panel.getByRole('button', { name: 'Save weight' }).click()
  await expect(page.getByRole('status')).toContainText('Red bundle weighs 4.5 oz')
  await expect(panel.getByTestId('weight-hint')).toContainText('4.5 oz per unit')
  await expect(panel.getByRole('button', { name: 'Save weight' })).toHaveCount(0)
  await expect(cell(page, 'RED', 'weight')).toHaveText('· 4.5 oz')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(cell(page, 'RED', 'available')).toContainText('low')
  await page.getByRole('tab', { name: /^Low/ }).click()
  await expect(page.getByTestId('level-HOLO')).toHaveCount(0)
  await page.getByRole('tab', { name: /^All/ }).click()
  await page.getByLabel('Search inventory').fill('slab')
  await expect(page.getByTestId('level-RED')).toHaveCount(0)
  await expect(page.getByTestId('level-HOLO')).toBeVisible()
  await page.getByRole('button', { name: 'Clear search' }).click()
  await expect(page.getByTestId('level-RED')).toBeVisible()

  // Stepping down and Enter saves; Escape throws an edit away.
  await red.getByRole('button', { name: 'One fewer Red bundle' }).click()
  await red.getByRole('button', { name: 'One fewer Red bundle' }).click()
  await red.getByRole('spinbutton').press('Enter')
  await expect(page.getByRole('status')).toContainText('Red bundle: shelf set to 1 (−2)')
  await expect(cell(page, 'RED', 'on-hand')).toHaveText('1')
  await red.getByRole('spinbutton').fill('9')
  await red.getByRole('spinbutton').press('Escape')
  await expect(red.getByRole('spinbutton')).toHaveValue('1')
  await expect(red.getByRole('button', { name: /^Save / })).toHaveCount(0)

  // Recent activity is folded away until asked for.
  await expect(page.getByTestId('activity')).toBeHidden()
  await page.getByTestId('toggle-activity').click()
  await expect(page.getByTestId('activity')).toContainText('Counted: shelf set to 1')

  // Sort: by shelf, high to low, then flip it.
  const rows = page.getByTestId('levels').locator('tbody tr')
  await page.getByLabel('Sort by').selectOption('onHand')
  await expect(rows.first()).toHaveAttribute('data-testid', 'level-HOLO')
  await page.getByRole('button', { name: /switch to low to high/ }).click()
  await expect(rows.first()).toHaveAttribute('data-testid', 'level-RED')

  // Sheet import: preview first, nothing changes; then apply.
  await page.getByTestId('toggle-import').click()
  await page.locator('input[type=file]').setInputFiles({
    name: 'sheet.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('Item,Start of Show Inventory,Current Stock\nRed bundle,12,9\nUltra Pack,4,4\n'),
  })
  await page.getByRole('button', { name: 'Preview' }).click()
  await expect(page.getByTestId('import-summary')).toContainText('1 update')
  await expect(page.getByTestId('import-summary')).toContainText('1 new')
  await expect(cell(page, 'RED', 'on-hand')).toHaveText('1')
  await page.getByTestId('apply-import').click()
  await expect(cell(page, 'RED', 'on-hand')).toHaveText('9')
  await expect(cell(page, 'ULTRA-PACK', 'on-hand')).toHaveText('4')

  // Rename from the item panel: Escape puts the name back, another item's name is refused.
  await page.getByTestId('level-HOLO').getByRole('button', { name: 'Holo slab', exact: true }).click()
  const holo = page.getByRole('dialog')
  const itemName = holo.getByRole('textbox', { name: 'Item name' })
  await expect(itemName).toHaveValue('Holo slab')
  await itemName.fill('Holo slab PSA 10')
  await itemName.press('Escape')
  await expect(itemName).toHaveValue('Holo slab')
  await expect(holo).toBeVisible()
  await itemName.fill('red BUNDLE')
  await holo.getByRole('button', { name: 'Save name' }).click()
  await expect(holo.getByRole('alert')).toHaveText('a product with that name already exists')
  await itemName.fill('Holo slab PSA 10')
  await holo.getByRole('button', { name: 'Save name' }).click()
  await expect(page.getByRole('status')).toContainText('Renamed Holo slab to Holo slab PSA 10')
  await expect(page.getByRole('dialog', { name: 'Holo slab PSA 10' })).toBeVisible()
  await expect(holo.getByRole('button', { name: 'Save name' })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByTestId('level-HOLO')).toContainText('Holo slab PSA 10')
})
