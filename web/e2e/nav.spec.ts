import { expect, test, type Page } from '@playwright/test'

const PW = 'correct-horse-battery'
const run = Date.now().toString(36)

let n = 0
async function signup(page: Page) {
  const id = `${run}-${++n}-${Math.random().toString(36).slice(2, 6)}` // unique per test and per repeat
  await page.goto('/signup')
  await page.getByLabel('Shop name').fill(`Nav ${id}`)
  await page.getByLabel('Your name').fill('Olive Owner')
  await page.getByLabel('Email').fill(`nav-${id}@shop.test`)
  await page.getByLabel('Password (10+ characters)').fill(PW)
  await page.getByLabel('I have read and accept the Sales Ops License and Data Terms').check()
  await page.getByRole('button', { name: 'Create shop' }).click()
  await expect(page).toHaveURL(/\/app$/)
}

test('desktop: the sidebar starts as icons, opens, and remembers the choice after a reload', async ({ page }) => {
  await signup(page)
  const orders = page.getByRole('link', { name: 'Orders' })
  await expect(page.locator('.app')).toHaveClass(/collapsed/)
  await expect(orders).toBeVisible()
  await expect(orders.locator('.label')).toBeHidden()
  await expect(orders).toHaveAttribute('title', 'Orders')
  // the grid track animates for 380 ms
  await expect.poll(() => page.locator('.sidebar').evaluate((el) => el.getBoundingClientRect().width)).toBeLessThan(80)
  await page.getByTestId('collapse-sidebar').click()
  await expect(page.locator('.app')).not.toHaveClass(/collapsed/)
  await expect(orders.locator('.label')).toBeVisible()
  await expect.poll(() => page.locator('.sidebar').evaluate((el) => el.getBoundingClientRect().width)).toBeGreaterThan(200)
  await page.reload()
  await expect(page.locator('.app')).not.toHaveClass(/collapsed/)
  await page.getByTestId('collapse-sidebar').click()
  await expect(page.locator('.app')).toHaveClass(/collapsed/)
  await page.reload()
  await expect(page.locator('.app')).toHaveClass(/collapsed/)
})

test('phone: top bar, drawer navigation, and no page wider than the screen', async ({ page }) => {
  // The production Content-Security-Policy (api/app/__init__.py) must not break any page.
  const violations: string[] = []
  page.on('console', (m) => {
    if (/Content Security Policy|Refused to (load|apply|execute|connect)/i.test(m.text())) violations.push(m.text())
  })
  page.on('pageerror', (e) => violations.push('page error: ' + e.message))
  await page.setViewportSize({ width: 390, height: 844 })
  await signup(page)
  await expect(page.getByTestId('open-menu')).toBeVisible()
  await expect(page.locator('.sidebar')).not.toBeInViewport()
  await page.getByTestId('open-menu').click()
  await expect(page.locator('.sidebar')).toHaveClass(/open/)
  await page.getByRole('link', { name: 'Inventory' }).click()
  await expect(page).toHaveURL(/\/app\/inventory$/)
  await expect(page.locator('.sidebar')).not.toHaveClass(/open/)
  for (const path of ['/app', '/app/shows', '/app/orders', '/app/inventory', '/app/products', '/app/review', '/app/reports', '/app/team', '/app/settings']) {
    await page.goto(path)
    const widths = await page.evaluate(() => ({ page: document.documentElement.scrollWidth, viewport: window.innerWidth }))
    expect(widths.page, path).toBeLessThanOrEqual(widths.viewport)
  }
  await page.goto('/app')
  await page.getByTestId('open-menu').click()
  await page.getByTestId('scrim').click()
  await expect(page.locator('.sidebar')).not.toHaveClass(/open/)
  const csp = await page.request.get('/app')
  expect(csp.headers()['content-security-policy']).toContain("frame-ancestors 'none'")
  expect(violations).toEqual([])
})
