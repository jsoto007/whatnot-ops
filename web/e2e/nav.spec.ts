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
  for (const path of ['/app', '/app/shows', '/app/orders', '/app/inventory', '/app/products', '/app/review', '/app/reports', '/app/team', '/app/settings', '/terms', '/privacy']) {
    await page.goto(path)
    const widths = await page.evaluate(() => ({ page: document.documentElement.scrollWidth, viewport: window.innerWidth }))
    expect(widths.page, path).toBeLessThanOrEqual(widths.viewport)
  }
  // The licence is a long document fetched after the page opens: measure it once it is drawn
  // (its file names and one web address must wrap, not push the page sideways).
  await page.goto('/license')
  await expect(page.getByRole('heading', { level: 3, name: '22. Contact' })).toBeVisible()
  const licence = await page.evaluate(() => ({ page: document.documentElement.scrollWidth, viewport: window.innerWidth }))
  expect(licence.page, '/license').toBeLessThanOrEqual(licence.viewport)
  await page.goto('/app')
  // Signed in, the footer closes the page too, below everything else.
  const footer = page.getByRole('contentinfo')
  await expect(footer.getByRole('link', { name: 'User agreement' })).toBeVisible()
  const below = await page.evaluate(() => {
    const bottom = (sel: string) => document.querySelector(sel)!.getBoundingClientRect().bottom
    return bottom('footer.site-footer') >= bottom('main.content')
  })
  expect(below).toBe(true)
  await page.getByTestId('open-menu').click()
  await page.getByTestId('scrim').click()
  await expect(page.locator('.sidebar')).not.toHaveClass(/open/)
  const csp = await page.request.get('/app')
  expect(csp.headers()['content-security-policy']).toContain("frame-ancestors 'none'")
  expect(violations).toEqual([])
})

test('print: the page fills the paper, landscape or portrait, sidebar collapsed or open, and the footer stays off it', async ({ page }) => {
  await signup(page)
  // Letter paper at 96 px per inch. Landscape is wider than the phone breakpoint, so the desktop
  // grid applies there, and a collapsed sidebar (the default) once left the page in its 76 px column.
  const papers = { landscape: { width: 1056, height: 816 }, portrait: { width: 816, height: 1056 } }
  const seen: Record<string, unknown> = {}
  const wanted: Record<string, unknown> = {}
  for (const sidebar of ['collapsed', 'expanded'] as const) {
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.evaluate((v) => localStorage.setItem('wn.sidebar', v), sidebar)
    await page.reload()
    await expect(page.getByRole('contentinfo')).toBeVisible()
    expect(await page.locator('.app').evaluate((el) => el.classList.contains('collapsed'))).toBe(sidebar === 'collapsed')
    for (const [name, paper] of Object.entries(papers)) {
      await page.setViewportSize(paper)
      await page.emulateMedia({ media: 'print' })
      await page.waitForTimeout(500) // longer than the sidebar's 380 ms slide, should a track still be moving
      seen[`${sidebar}, ${name}`] = await page.evaluate(() => {
        const content = document.querySelector('main.content')!.getBoundingClientRect()
        const shown = (sel: string) => getComputedStyle(document.querySelector(sel)!).display !== 'none'
        return { left: Math.round(content.left), width: Math.round(content.width), sidebar: shown('.sidebar'), footer: shown('footer.site-footer') }
      })
      wanted[`${sidebar}, ${name}`] = { left: 0, width: paper.width, sidebar: false, footer: false }
      await page.emulateMedia({ media: 'screen' })
    }
  }
  expect(seen).toEqual(wanted)
})
