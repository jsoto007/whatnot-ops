import { expect, test, type Page } from '@playwright/test'
import { connectLaptop } from './laptop'
import { cell, receive } from './stock'

const PW = 'correct-horse-battery'
const run = Date.now().toString(36)
const SHOW = new Date().toISOString()

/** E2E_SHOTS=<folder> saves a picture of each new screen (for a write-up); off by default. */
const shot = async (page: Page, name: string) => {
  if (process.env.E2E_SHOTS) await page.screenshot({ path: `${process.env.E2E_SHOTS}/${name}.png`, fullPage: true })
}

function row(seq: number, body: string) {
  return {
    id: (seq + 0x9000).toString(16).padStart(16, '0'),
    showId: SHOW,
    username: '',
    body,
    rawText: body,
    capturedAt: new Date(Date.parse(SHOW) + seq * 1000).toISOString(),
    seq,
  }
}

test('buyers: who bought what, a flag and a note from the team, and the flag wherever the buyer is shown', async ({ page, request }) => {
  await page.goto('/signup')
  await page.getByLabel('Shop name').fill(`Buyers ${run}`)
  await page.getByLabel('Your name').fill('Olive Owner')
  await page.getByLabel('Email').fill(`buyers-${run}@shop.test`)
  await page.getByLabel('Password (10+ characters)').fill(PW)
  await page.getByRole('button', { name: 'Create shop' }).click()
  await expect(page).toHaveURL(/\/app$/)

  await page.getByRole('link', { name: 'Products' }).click()
  await page.getByLabel('Product name').fill('Red bundle')
  await page.getByLabel('SKU code (optional)').fill('RED')
  await page.getByLabel('Price (USD, optional)').fill('25')
  await page.getByRole('button', { name: 'Add product' }).click()
  await receive(page, 'RED', 5)
  await expect(cell(page, 'RED', 'on-hand')).toHaveText('5')

  const key = await connectLaptop(page, request)
  const sent = await request.post('/api/ingest', {
    data: {
      version: 1,
      show: { id: SHOW, startedAt: SHOW },
      rows: [row(1, 'lani808 won Red bundle'), row(2, 'lani808 won Red bundle x2'), row(3, 'kat.c won Red bundle')],
    },
    headers: { Authorization: 'Bearer ' + key },
  })
  expect(sent.ok()).toBe(true)

  // Everyone who won, the biggest spender first. Nobody is flagged yet.
  await page.getByLabel('Sections').getByRole('link', { name: 'Buyers' }).click()
  const lani = page.getByTestId('buyer-lani808')
  await expect(lani).toContainText('$75.00')
  await expect(page.getByTestId('buyer-kat.c')).toContainText('$25.00')
  await expect(page.getByTestId('buyers').locator('tbody tr').first()).toContainText('lani808')
  await expect(page.getByTestId('buyer-flag')).toHaveCount(0)
  await shot(page, '7-buyers')

  // One buyer's page: figures, then what the team says about them.
  await lani.getByRole('link', { name: 'lani808' }).click()
  const figures = page.getByTestId('buyer-figures')
  await expect(figures).toContainText('$75.00')
  await expect(figures).toContainText('Nothing fell through')
  await expect(page.getByTestId('buyer-orders')).toContainText('$75.00')
  await page.getByLabel('Flag').selectOption('watch')
  await page.getByLabel('Note').fill('Check the address before shipping.')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Saved.')).toBeVisible()
  await expect(page.getByRole('heading', { name: /lani808/ })).toContainText('Watch list')
  await shot(page, '8-buyer-page')

  // The flag is beside the buyer on Orders, and leads back to their page. The note is not.
  await page.getByLabel('Sections').getByRole('link', { name: /^Orders/ }).click()
  const order = page.getByTestId('order-lani808')
  await expect(order.getByTestId('buyer-flag')).toHaveText('Watch list · note')
  await expect(page.getByTestId('order-kat.c').getByTestId('buyer-flag')).toHaveCount(0)
  await expect(page.locator('body')).not.toContainText('Check the address before shipping.')
  await shot(page, '9-orders-with-flag')
  await order.getByTestId('buyer-flag').click()
  await expect(page).toHaveURL(/\/app\/buyers\/lani808$/)
  await expect(page.getByLabel('Note')).toHaveValue('Check the address before shipping.')

  // The list filters by flag and finds a buyer by name; the activity log says it in words.
  await page.getByRole('link', { name: '← Buyers' }).click()
  await page.getByRole('button', { name: /Watch list/ }).click()
  await expect(page.getByTestId('buyers').locator('tbody tr')).toHaveCount(1)
  await expect(page.getByTestId('buyer-lani808')).toContainText('Watch list')
  await page.getByRole('button', { name: /Everyone/ }).click()
  await page.getByLabel('Find a buyer').fill('KAT')
  await expect(page.getByTestId('buyers').locator('tbody tr')).toHaveCount(1)
  await expect(page.getByTestId('buyer-kat.c')).toBeVisible()

  // The show laptop's question, as the extension asks it: no note, no money.
  const who = await request.post('/api/ingest/buyers', { data: { show: SHOW, names: ['lani808'] }, headers: { Authorization: 'Bearer ' + key } })
  expect(await who.json()).toEqual({ buyers: [{ name: 'lani808', flag: 'watch', firstTime: true, shows: 0, items: 0 }] })
})
