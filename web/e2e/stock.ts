import { expect, type Page } from '@playwright/test'

export const cell = (page: Page, code: string, col: 'on-hand' | 'held' | 'available') => page.getByTestId(`level-${code}`).getByTestId(col)

/** Put `qty` more of an existing SKU on the shelf with the Inventory row's − [n] + ✓ stepper. */
export async function receive(page: Page, code: string, qty: number) {
  await page.getByRole('link', { name: 'Inventory' }).click()
  const row = page.getByTestId(`level-${code}`)
  await expect(row).toBeVisible()
  const before = Number(await cell(page, code, 'on-hand').innerText())
  await row.getByRole('spinbutton').fill(String(before + qty))
  await row.getByRole('button', { name: /^Save / }).click()
  await expect(cell(page, code, 'on-hand')).toHaveText(String(before + qty))
}
