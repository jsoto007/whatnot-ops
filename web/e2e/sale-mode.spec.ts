import { expect, test, type Page } from '@playwright/test'
import { connectLaptop } from './laptop'

const PW = 'correct-horse-battery'
const run = Date.now().toString(36)
const SHOW = new Date().toISOString()

async function signup(page: Page) {
  await page.goto('/signup')
  await page.getByLabel('Shop name').fill(`Modes ${run}`)
  await page.getByLabel('Your name').fill('Olive Owner')
  await page.getByLabel('Email').fill(`modes-${run}@shop.test`)
  await page.getByLabel('Password (10+ characters)').fill(PW)
  await page.getByLabel('I have read and accept the Sales Ops License and Data Terms').check()
  await page.getByRole('button', { name: 'Create shop' }).click()
  await expect(page).toHaveURL(/\/app$/)
}

test('the shop has a usual way of selling; one show can differ, and go back', async ({ page, request }) => {
  await signup(page)
  const key = await connectLaptop(page, request)
  const laptop = { Authorization: 'Bearer ' + key }
  // A recorded show: one chat line is enough. The buyer is made up.
  const res = await request.post('/api/ingest', {
    data: {
      version: 1,
      show: { id: SHOW, startedAt: SHOW },
      rows: [{ id: (0x3001).toString(16).padStart(16, '0'), showId: SHOW, username: 'buyer_one', body: 'hello', rawText: 'buyer_one hello', capturedAt: SHOW, seq: 1 }],
    },
    headers: laptop,
  })
  expect(res.ok()).toBe(true)
  const needs = async () => ((await (await request.get(`/api/ingest/stock?show=${encodeURIComponent(SHOW)}`, { headers: laptop })).json()) as { show?: unknown }).show

  // A new shop sells as "winners pick in chat", and its show follows it.
  expect(await needs()).toEqual({ saleMode: 'winners_pick', winnersFromWhatnot: false })
  await page.getByRole('link', { name: 'Shows', exact: true }).click()
  await page.getByRole('link', { name: /show|\d{4}/ }).first().click()
  const mode = page.getByRole('combobox', { name: 'Sold as' })
  await expect(mode).toHaveValue('usual')
  await expect(page.getByTestId('show-mode')).toContainText('The winner types a shelf spot in chat, like A5')
  await expect(page.getByRole('tab', { name: 'Shelves' })).toBeVisible()

  // This one show sells one product per listing: no shelf spots, and the laptop is told.
  await mode.selectOption('listing')
  await expect(page.getByTestId('show-mode')).toContainText('Each listing is one product.')
  await expect(page.getByRole('tab', { name: 'Shelves' })).toHaveCount(0)
  expect(await needs()).toEqual({ saleMode: 'listing', winnersFromWhatnot: false })
  await page.reload()
  await expect(page.getByRole('combobox', { name: 'Sold as' })).toHaveValue('listing')

  // Back to the shop's usual way.
  await page.getByRole('combobox', { name: 'Sold as' }).selectOption('usual')
  await expect(page.getByRole('tab', { name: 'Shelves' })).toBeVisible()
  expect(await needs()).toEqual({ saleMode: 'winners_pick', winnersFromWhatnot: false })

  // The owner changes the shop's usual way in Settings: the show follows, and both changes are in the log.
  await page.getByRole('link', { name: 'Settings' }).click()
  await page.getByRole('radio', { name: /One product per listing/ }).check()
  await page.getByRole('button', { name: 'Save how you sell' }).click()
  await expect.poll(needs).toEqual({ saleMode: 'listing', winnersFromWhatnot: false })
  await page.reload()
  await expect(page.getByRole('radio', { name: /One product per listing/ })).toBeChecked()
  await expect(page.getByText('Set how the shop usually sells to “one product per listing”')).toBeVisible()
  await expect(page.getByText('Set how a show sells to “one product per listing”')).toBeVisible()
  await expect(page.getByText(/Set a show back to the shop's usual way of selling/)).toBeVisible()

  // The owner lists the moderators whose chat lines may set an offer; the list is kept, cleaned, and in the log.
  await page.getByLabel('Trusted moderators').fill('Mod_One, @other.mod  mod_one')
  const saved = page.waitForResponse((r) => r.url().endsWith('/api/team/organization') && r.request().method() === 'PATCH')
  await page.getByRole('button', { name: 'Save how you sell' }).click()
  expect((await saved).ok()).toBe(true)
  await page.reload()
  await expect(page.getByLabel('Trusted moderators')).toHaveValue('mod_one, other.mod')
  await expect(page.getByText(/Set the trusted moderators \(2 names\)/)).toBeVisible()

  await page.getByRole('link', { name: 'Shows', exact: true }).click()
  await page.getByRole('link', { name: /show|\d{4}/ }).first().click()
  await expect(page.getByRole('combobox', { name: 'Sold as' })).toHaveValue('usual')
  await expect(page.getByRole('combobox', { name: 'Sold as' }).getByRole('option', { name: 'The shop’s usual way: One product per listing' })).toBeAttached()
  await expect(page.getByRole('tab', { name: 'Shelves' })).toHaveCount(0)
})
