// 台指期「網友原版」截圖（手動檢查用，T-456）：node tests/shot-futures-ms.cjs <baseUrl> <outDir> <mockDir>
// mockDir：fubon_live.quote_day 用期交所逐筆重播 2026-10-20～10-27 產生的 <日>_{closed,seconds}.json、2026-10-27_live.json、days.json，
// 大台／微台同名檔前面加 TX_／TMF_。其他 /api/* 回空的。
const { chromium } = require('playwright')
const fs = require('node:fs')
const path = require('node:path')

;(async () => {
  const [base, out, mock] = process.argv.slice(2)
  const load = (f) => JSON.parse(fs.readFileSync(path.join(mock, f), 'utf8'))
  const fresh = (j) => ({ ...j, updated_at: new Date(Date.now() - 8000).toISOString() })
  const b = await chromium.launch()
  for (const [w, h, m, name] of [[390, 844, true, 'm-ms'], [1360, 900, false, 'd-ms']]) {
    const ctx = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: m ? 2 : 1, isMobile: m, hasTouch: m })
    const page = await ctx.newPage()
    page.on('pageerror', (e) => console.log('pageerror:', String(e.stack || e).slice(0, 600)))
    await page.route('**/api/**', (r) => r.fulfill({ json: {} }))
    await page.route('**/api/layout', (r) => r.fulfill({ json: { layout: null } }))
    await page.route('**/api/me', (r) => r.fulfill({ json: { email: 'cktalex@gmail.com' } }))
    await page.route('**/api/access', (r) => r.fulfill({ json: { features: ['futures'] } }))
    await page.route('**/api/watchlist', (r) => r.fulfill({ json: { max: 20, date: null, items: [] } }))
    await page.route('**/api/trades', (r) => r.fulfill({ json: { today: '2026-10-27', items: [], max: 50 } }))
    await page.route('**/news/**', (r) => r.fulfill({ json: { items: [], next: null } }))
    await page.route('**/api/futures/days', (r) => r.fulfill({ json: load('days.json') }))
    await page.route('**/api/futures?day=*', (r) => r.fulfill({ json: load(`${new URL(r.request().url()).searchParams.get('day')}_closed.json`) }))
    await page.route('**/api/futures', (r) => r.fulfill({ json: fresh(load('2026-10-27_live.json')) }))
    await page.route('**/api/futures/seconds?day=*', (r) => r.fulfill({ json: load(`${new URL(r.request().url()).searchParams.get('day')}_seconds.json`) }))
    await page.route('**/api/futures/quotes?product=*', (r) => {
      const u = new URL(r.request().url())
      const p = u.searchParams.get('product'), d = u.searchParams.get('day')
      r.fulfill({ json: d ? load(`${p}_${d}_closed.json`) : fresh(load(`${p}_2026-10-27_live.json`)) })
    })
    await page.route('**/api/futures/quotes/days?product=*', (r) => r.fulfill({ json: load(`${new URL(r.request().url()).searchParams.get('product')}_days.json`) }))
    await page.route('**/api/futures/quotes/seconds?product=*', (r) => {
      const u = new URL(r.request().url())
      r.fulfill({ json: load(`${u.searchParams.get('product')}_${u.searchParams.get('day')}_seconds.json`) })
    })
    await page.goto(`${base}/?tab=futures`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(1500)
    const btn = page.getByRole('button', { name: '同意並進入' })
    if ((await btn.count()) > 0) {
      await page.locator('input[type="checkbox"]').first().check()
      await btn.click()
    }
    await page.waitForSelector('.cq-fu-chart canvas', { timeout: 15000 })
    await page.waitForTimeout(1200)
    await page.screenshot({ path: path.join(out, `${name}-1-live.png`), fullPage: true })
    await page.getByRole('button', { name: /復盤/ }).click()
    await page.waitForSelector('.cq-fu-scrub input', { timeout: 15000 })
    await page.waitForTimeout(1000)
    // 日曆切「網友原版」，點 10-20 那天
    await page.locator('.cq-fu-cal-view button', { hasText: '網友原版' }).click()
    await page.waitForTimeout(300)
    await page.locator('.cq-fu-cal').screenshot({ path: path.join(out, `${name}-2-calendar-ms.png`) })
    await page.locator('.cq-fu-cal-cell', { hasText: '20' }).first().click()
    await page.waitForTimeout(1500)
    await page.screenshot({ path: path.join(out, `${name}-3-replay-1020.png`), fullPage: true })
    // 拉回開盤 → 下一個買賣點，直到網友原版進場
    await page.locator('.cq-fu-scrub input').fill('0')
    await page.waitForTimeout(400)
    for (let i = 0; i < 6; i++) {
      await page.getByRole('button', { name: /下一個買賣點/ }).click()
      await page.waitForTimeout(250)
      const txt = await page.locator('.cq-fu-now').innerText()
      if (txt.includes('網友原版') && txt.includes('買進')) break
    }
    await page.waitForTimeout(600)
    await page.screenshot({ path: path.join(out, `${name}-4-replay-ms-entry.png`), fullPage: true })
    // 微台也有
    await page.getByRole('button', { name: '微台' }).click()
    await page.waitForTimeout(2000)
    await page.screenshot({ path: path.join(out, `${name}-5-tmf.png`), fullPage: true })
    await ctx.close()
  }
  await b.close()
})()
