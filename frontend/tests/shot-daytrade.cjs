// 「當沖」分頁截圖（手動檢查用，T-458）：node tests/shot-daytrade.cjs <baseUrl> <outDir> <mockDir>
// mockDir：stock_live.py --replay-1m 產生的 payload.json、history.json（放在 <mockDir>/<日>/），加上 days.json（{days: [...]}）。
// 盤中畫面用最新一天的 payload 改成 status=live。其他 /api/* 回空的。
const { chromium } = require('playwright')
const fs = require('node:fs')
const path = require('node:path')

;(async () => {
  const [base, out, mock] = process.argv.slice(2)
  const load = (f) => JSON.parse(fs.readFileSync(path.join(mock, f), 'utf8'))
  const days = load('days.json')
  const latest = days.days[0].day
  const b = await chromium.launch()
  for (const [w, h, m, name] of [[390, 844, true, 'm-dt'], [1360, 900, false, 'd-dt']]) {
    const ctx = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: m ? 2 : 1, isMobile: m, hasTouch: m })
    const page = await ctx.newPage()
    page.on('pageerror', (e) => console.log('pageerror:', String(e.stack || e).slice(0, 600)))
    await page.route('**/api/**', (r) => r.fulfill({ json: {} }))
    await page.route('**/api/layout', (r) => r.fulfill({ json: { layout: null } }))
    await page.route('**/api/me', (r) => r.fulfill({ json: { email: 'cktalex@gmail.com' } }))
    await page.route('**/api/access', (r) => r.fulfill({ json: { features: ['futures'] } }))
    await page.route('**/api/watchlist', (r) => r.fulfill({ json: { max: 20, date: null, items: [] } }))
    await page.route('**/api/trades', (r) => r.fulfill({ json: { today: latest, items: [], max: 50 } }))
    await page.route('**/news/**', (r) => r.fulfill({ json: { items: [], next: null } }))
    await page.route('**/api/daytrade/days', (r) => r.fulfill({ json: days }))
    await page.route('**/api/daytrade/history?day=*', (r) => r.fulfill({ json: load(`${new URL(r.request().url()).searchParams.get('day')}/history.json`) }))
    await page.route('**/api/daytrade?day=*', (r) => r.fulfill({ json: load(`${new URL(r.request().url()).searchParams.get('day')}/payload.json`) }))
    await page.route('**/api/daytrade', (r) => r.fulfill({ json: { ...load(`${latest}/payload.json`), status: 'live' } }))
    await page.goto(`${base}/?tab=daytrade`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(1500)
    const btn = page.getByRole('button', { name: '同意並進入' })
    if ((await btn.count()) > 0) {
      await page.locator('input[type="checkbox"]').first().check()
      await btn.click()
    }
    await page.waitForSelector('.cq-dt-board, .cq-empty', { timeout: 15000 })
    await page.waitForTimeout(800)
    await page.screenshot({ path: path.join(out, `${name}-1-live.png`), fullPage: true })
    await page.getByRole('button', { name: /復盤/ }).click()
    await page.waitForSelector('.cq-dt-scrub input, .cq-empty', { timeout: 15000 })
    await page.waitForTimeout(1200)
    await page.screenshot({ path: path.join(out, `${name}-2-replay-close.png`), fullPage: true })
    // 拉到 10:00 左右
    const slider = page.locator('.cq-dt-scrub input')
    if ((await slider.count()) > 0) {
      const max = Number(await slider.getAttribute('max'))
      await slider.fill(String(Math.round(max * 0.2)))
      await page.waitForTimeout(800)
      await page.screenshot({ path: path.join(out, `${name}-3-replay-1000.png`), fullPage: true })
    }
    await ctx.close()
  }
  await b.close()
})()
