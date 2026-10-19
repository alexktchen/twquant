// 首頁大盤體檢（盤中版，T-397）截圖（手動檢查用）：node tests/shot-market-live.cjs <baseUrl> <outDir>
// 本機 `wrangler pages dev` 的 KV 要先灌今天的 universe／slice:0..2（wrangler kv key put --local）。
const { chromium } = require('playwright')
const path = require('node:path')
;(async () => {
  const [base, out] = process.argv.slice(2)
  const b = await chromium.launch()
  for (const [w, h, m, name] of [[390, 844, true, 'm-market'], [1360, 900, false, 'd-market']]) {
    const ctx = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: m ? 2 : 1, isMobile: m, hasTouch: m })
    const page = await ctx.newPage()
    await page.setExtraHTTPHeaders({ 'cf-access-authenticated-user-email': 'cktalex@gmail.com' })
    await page.route('**/api/trades', (r) => r.fulfill({ json: { today: '2026-10-19', max: 5000, items: [] } }))
    await page.route('**/api/watchlist', (r) => r.fulfill({ json: { max: 100, date: '2026-10-16', items: [] } }))
    await page.route('**/api/layout', (r) => r.fulfill({ json: { layout: null } }))
    await page.goto(`${base}/`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(1500)
    const btn = page.getByRole('button', { name: '同意並進入' })
    if ((await btn.count()) > 0) { await page.locator('input[type="checkbox"]').first().check(); await btn.click() }
    await page.waitForTimeout(5000)
    const card = page.locator('[data-card="market"]').first()
    await card.scrollIntoViewIfNeeded()
    await card.screenshot({ path: path.join(out, `${name}-card.png`) })
    // 收起時的輪播
    await card.locator('.cq-hc-title').click()
    await page.waitForTimeout(500)
    await card.screenshot({ path: path.join(out, `${name}-folded.png`) })
    await ctx.close()
  }
  await b.close()
  console.log('market ok')
})().catch((e) => { console.error(e); process.exit(1) })
