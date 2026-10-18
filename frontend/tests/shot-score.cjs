// 績效頁截圖（手動檢查用，不進 npm test）：node tests/shot-score.cjs <baseUrl> <outDir> <prefix> [線別名稱]
// 桌機整頁、手機整頁、手機上半。追蹤股與下單 API 本機拿不到 Cloudflare 權杖 → 用假資料攔下。
const { chromium } = require('playwright')
const path = require('node:path')

;(async () => {
  const [base, out, prefix, lineName] = process.argv.slice(2)
  const browser = await chromium.launch()
  const open = async (w, h, mobile) => {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: mobile ? 2 : 1, isMobile: mobile, hasTouch: mobile })
    const page = await ctx.newPage()
    await page.setExtraHTTPHeaders({ 'cf-access-authenticated-user-email': 'cktalex@gmail.com' })
    await page.route('**/api/trades', (r) => r.fulfill({ json: { today: '2026-10-18', max: 5000, items: [] } }))
    await page.route('**/api/watchlist', (r) => r.fulfill({ json: { max: 100, date: '2026-10-16', items: [] } }))
    await page.route('**/api/layout', (r) => r.fulfill({ json: { order: null, hidden: [] } }))
    page.on('pageerror', (e) => console.error('pageerror', String(e)))
    return page
  }
  const agree = async (page) => {
    const btn = page.getByRole('button', { name: '同意並進入' })
    if ((await btn.count()) === 0) return
    await page.locator('input[type="checkbox"]').first().check()
    await btn.click()
    await page.waitForTimeout(800)
  }
  const go = async (page) => {
    await page.goto(`${base}/`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(1500)
    await agree(page)
    await page.getByRole('button', { name: '績效', exact: true }).click()
    await page.waitForTimeout(3000)
    if (lineName) {
      await page.locator('.cq-score-side .cq-seg button', { hasText: lineName }).click()
      await page.waitForTimeout(3000)
    }
  }
  const d = await open(1360, 900, false)
  await go(d)
  await d.screenshot({ path: path.join(out, `${prefix}-desktop.png`), fullPage: true })
  const m = await open(390, 844, true)
  await go(m)
  await m.screenshot({ path: path.join(out, `${prefix}-m-top.png`) })
  await m.screenshot({ path: path.join(out, `${prefix}-m-full.png`), fullPage: true })
  await browser.close()
  console.log('score shots ok')
})().catch((e) => {
  console.error(e)
  process.exit(1)
})
