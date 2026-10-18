// 首頁今日一句話截圖（手動檢查用）：node tests/shot-home-brief.cjs <baseUrl> <outDir>
const { chromium } = require('playwright')
const path = require('node:path')
;(async () => {
  const [base, out] = process.argv.slice(2)
  const b = await chromium.launch()
  for (const [w, h, m, name] of [[390, 844, true, 'm-brief'], [1360, 900, false, 'd-home']]) {
    const ctx = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: m ? 2 : 1, isMobile: m, hasTouch: m })
    const page = await ctx.newPage()
    await page.setExtraHTTPHeaders({ 'cf-access-authenticated-user-email': 'cktalex@gmail.com' })
    await page.route('**/api/trades', (r) => r.fulfill({ json: { today: '2026-10-18', max: 5000, items: [] } }))
    await page.route('**/api/watchlist', (r) => r.fulfill({ json: { max: 100, date: '2026-10-16', items: [] } }))
    await page.route('**/api/layout', (r) => r.fulfill({ json: { order: null, hidden: [] } }))
    await page.goto(`${base}/`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(1500)
    const btn = page.getByRole('button', { name: '同意並進入' })
    if ((await btn.count()) > 0) { await page.locator('input[type="checkbox"]').first().check(); await btn.click() }
    await page.waitForTimeout(4000)
    const card = page.locator('.cq-hc-view').first()
    await card.screenshot({ path: path.join(out, `${name}-card.png`) })
    await page.screenshot({ path: path.join(out, `${name}.png`) })
    await ctx.close()
  }
  await b.close()
  console.log('home ok')
})().catch((e) => { console.error(e); process.exit(1) })
