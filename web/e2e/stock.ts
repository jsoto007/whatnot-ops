import { expect, type Page } from '@playwright/test'

/** Receive `qty` of an existing SKU through the Inventory page's "Receive stock" form. */
export async function receive(page: Page, code: string, qty: number) {
  await page.getByRole('link', { name: 'Inventory' }).click()
  await page.getByRole('button', { name: 'Receive stock', exact: true }).first().click()
  const item = page.getByLabel('Item, line 1')
  const value = await item.locator('option', { hasText: `· ${code} (` }).getAttribute('value')
  await item.selectOption(value!)
  await page.getByLabel('Quantity, line 1').fill(String(qty))
  await page.getByTestId('receive-submit').click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
}

export const cell = (page: Page, code: string, col: 'on-hand' | 'held' | 'available') => page.getByTestId(`level-${code}`).getByTestId(col)
