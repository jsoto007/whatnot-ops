import { expect, test, type Page } from '@playwright/test'

const PW = 'correct-horse-battery'
const run = Date.now().toString(36)
const SHOW = new Date().toISOString()

async function signup(page: Page) {
  await page.goto('/signup')
  await page.getByLabel('Shop name').fill(`Dash ${run}`)
  await page.getByLabel('Your name').fill('Olive Owner')
  await page.getByLabel('Email').fill(`dash-${run}@shop.test`)
  await page.getByLabel('Password (10+ characters)').fill(PW)
  await page.getByRole('button', { name: 'Create shop' }).click()
  await expect(page).toHaveURL(/\/app$/)
}

function row(seq: number, username: string, body: string) {
  return {
    id: (seq + 0x3000).toString(16).padStart(16, '0'),
    showId: SHOW,
    username,
    body,
    rawText: `${username} ${body}`.trim(),
    capturedAt: new Date(Date.parse(SHOW) + seq * 1000).toISOString(),
    seq,
  }
}

test('dashboard tiles, show analytics and the Excel report agree', async ({ page, request }) => {
  await signup(page)
  await expect(page.getByText('No shows yet')).toBeVisible()

  await page.getByRole('link', { name: 'Products' }).click()
  await page.getByLabel('Product name').fill('Red bundle')
  await page.getByLabel('SKU code (optional)').fill('RED')
  await page.getByLabel('Price (USD, optional)').fill('25')
  await page.getByRole('button', { name: 'Add product' }).click()
  await page.getByRole('link', { name: 'Inventory' }).click()
  await page.getByLabel('Adjust RED').fill('+4')
  await page.getByTestId('level-RED').getByRole('button', { name: 'Apply' }).click()
  await expect(page.getByTestId('level-RED').locator('td').nth(2)).toHaveText('4')
  await page.getByRole('link', { name: 'Settings' }).click()
  await page.getByLabel('Laptop name').fill('Laptop')
  await page.getByRole('button', { name: 'Create key' }).click()
  const key = (await page.getByTestId('new-key').locator('code').textContent())!.trim()
  const res = await request.post('/api/ingest', {
    data: {
      version: 1,
      show: { id: SHOW, startedAt: SHOW },
      rows: [row(1, '', 'lani808 won Red bundle x2'), row(2, '', 'kat.c won Red bundle'), row(3, '', 'kat.c won the giveaway!'), row(4, '', 'No bids')],
    },
    headers: { Authorization: 'Bearer ' + key },
  })
  expect(res.ok()).toBe(true)

  await page.getByRole('link', { name: 'Dashboard' }).click()
  await expect(page.getByTestId('live-pill')).toContainText('4 messages')
  await expect(page.getByTestId('tile-items')).toContainText('3')
  await expect(page.getByTestId('tile-buyers')).toContainText('2')
  await expect(page.getByTestId('tile-red')).toContainText('3')
  await expect(page.getByTestId('tile-gifts')).toContainText('1')
  await expect(page.getByTestId('tile-nopicks')).toContainText('1')
  await expect(page.getByTestId('tile-paid')).toContainText('$75.00 awaiting payment')
  await expect(page.getByTestId('shows-table')).toContainText('3')
  await expect(page.locator('.bars').first()).toContainText('lani808')

  await page.getByTestId('shows-table').getByRole('link').first().click()
  await expect(page.getByTestId('tile-items')).toContainText('3')
  await page.getByRole('tab', { name: 'Analytics' }).click()
  await expect(page.locator('.bars').nth(1)).toContainText('75% sold through')

  const xlsx = await page.request.get(page.url().replace(/\/app\/shows\/(\d+).*/, '/api/reports/show/$1.xlsx'))
  expect(xlsx.ok()).toBe(true)
  expect(xlsx.headers()['content-type']).toContain('spreadsheetml')
  expect((await xlsx.body()).subarray(0, 2).toString()).toBe('PK')
  await page.getByRole('link', { name: 'Reports' }).click()
  await page.getByLabel('Report show').selectOption({ index: 1 })
  await expect(page.getByTestId('download-show')).toHaveAttribute('href', /\/api\/reports\/show\/\d+\.xlsx/)
})
