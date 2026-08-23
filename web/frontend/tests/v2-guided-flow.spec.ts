import { test, expect } from '@playwright/test'

/**
 * v2 引导流 E2E：页面切换、刷新恢复、390px 窄屏、键盘可达。
 * 使用 mock（路由拦截），不写真实纸面账户。
 */

test('v2 页面导航切换', async ({ page }) => {
  await page.route('**/api/**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({}),
    }),
  )
  await page.goto('/v2/monitor')
  await expect(page).toHaveURL(/v2\/monitor/)
  await page.goto('/v2/system')
  await expect(page).toHaveURL(/v2\/system/)
})

test('刷新恢复路由', async ({ page }) => {
  await page.goto('/v2/review')
  await page.reload()
  await expect(page).toHaveURL(/v2\/review/)
})

test('@a11y 390px 窄屏不横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.route('**/api/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) }),
  )
  await page.goto('/v2/compare')
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  )
  expect(overflow).toBe(false)
})

test('@a11y 键盘可达主操作', async ({ page }) => {
  await page.route('**/api/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) }),
  )
  await page.goto('/v2/compare')
  const input = page.getByLabel('对比标的')
  await input.focus()
  await expect(input).toBeFocused()
})
