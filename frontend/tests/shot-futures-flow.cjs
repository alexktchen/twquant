// 台指期「盤口與大單」截圖（手動檢查用，T-455）：node tests/shot-futures-flow.cjs <baseUrl> <outDir> <mockDir>
// mockDir：C:/t/make_flow_mock.py 用 2026-10-28 富邦錄到的原始訊息做的 2026-10-28_{live,closed,seconds}.json（含 flow）、TX／TMF 同名檔、days.json。
// /api/futures/flow 由這裡從那些檔的 flow 拼：照 min 篩大單、live=1 不給 books。其他 /api/* 回空的。
const { chromium } = require('playwright')
const fs = require('node:fs')
const path = require('node:path')

;(async () => {
  const [base, out, mock] = process.argv.slice(2)
  const load = (f) => JSON.parse(fs.readFileSync(path.join(mock, f), 'utf8'))
  const days = load('days.json')
  const fresh = (j) => ({ ...j, updated_at: new Date(Date.now() - 8000).toISOString() })
  const strip = (j) => {
    const { flow: _flow, ...rest } = j
    return rest
  }
  const flowOf = (product, day, min, live) => {
    const j = load(product === 'MTX' ? `${day}_closed.json` : `${product}_${day}_closed.json`)
    const f = j.flow
    if (!f) return null
    return { ...f, day, product, big_min: min, big: f.big.filter((b) => b[2] >= min), books: live ? null : f.books }
  }
  const b = await chromium.launch()
  for (const [w, h, m, name] of [[390, 844, true, 'm-flow'], [1360, 900, false, 'd-flow']]) {
    const ctx = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: m ? 2 : 1, isMobile: m, hasTouch: m })
    const page = await ctx.newPage()
    page.on('pageerror', (e) => console.log('pageerror:', String(e.stack || e).slice(0, 600)))
    await page.route('**/api/**', (r) => r.fulfill({ json: {} }))
    await page.route('**/api/layout', (r) => r.fulfill({ json: { layout: null } }))
    await page.route('**/api/me', (r) => r.fulfill({ json: { email: 'cktalex@gmail.com' } }))
    await page.route('**/api/access', (r) => r.fulfill({ json: { features: ['futures'] } }))
    await page.route('**/api/watchlist', (r) => r.fulfill({ json: { max: 20, date: null, items: [] } }))
    await page.route('**/api/trades', (r) => r.fulfill({ json: { today: '2026-10-28', items: [], max: 50 } }))
    await page.route('**/news/**', (r) => r.fulfill({ json: { items: [], next: null } }))
    await page.route('**/api/futures/days', (r) => r.fulfill({ json: days }))
    await page.route('**/api/futures?day=*', (r) => r.fulfill({ json: strip(load(`${new URL(r.request().url()).searchParams.get('day')}_closed.json`)) }))
    await page.route('**/api/futures', (r) => r.fulfill({ json: fresh(strip(load('2026-10-28_live.json'))) }))
    await page.route('**/api/futures/seconds?day=*', (r) => r.fulfill({ json: load(`${new URL(r.request().url()).searchParams.get('day')}_seconds.json`) }))
    await page.route('**/api/futures/quotes?product=*', (r) => {
      const u = new URL(r.request().url())
      const p = u.searchParams.get('product'), d = u.searchParams.get('day')
      r.fulfill({ json: d ? strip(load(`${p}_${d}_closed.json`)) : fresh(strip(load(`${p}_2026-10-28_live.json`))) })
    })
    await page.route('**/api/futures/quotes/days?product=*', (r) => r.fulfill({ json: load(`${new URL(r.request().url()).searchParams.get('product')}_days.json`) }))
    await page.route('**/api/futures/quotes/seconds?product=*', (r) => {
      const u = new URL(r.request().url())
      r.fulfill({ json: load(`${u.searchParams.get('product')}_${u.searchParams.get('day')}_seconds.json`) })
    })
    await page.route('**/api/futures/flow?*', (r) => {
      const u = new URL(r.request().url())
      const f = flowOf(u.searchParams.get('product'), u.searchParams.get('day') ?? '2026-10-28', Number(u.searchParams.get('min') ?? 5), u.searchParams.get('live') === '1')
      if (f) r.fulfill({ json: f })
      else r.fulfill({ status: 404, json: { error: '這一天沒有盤口資料' } })
    })
    await page.goto(`${base}/?tab=futures`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(1500)
    const btn = page.getByRole('button', { name: '同意並進入' })
    if ((await btn.count()) > 0) {
      await page.locator('input[type="checkbox"]').first().check()
      await btn.click()
    }
    await page.waitForSelector('.cq-fu-chart canvas', { timeout: 15000 })
    // 盤中：展開「盤口與大單」
    await page.getByText('盤口與大單').click()
    await page.waitForSelector('.cq-fu-flow-chart canvas', { timeout: 15000 })
    await page.waitForTimeout(1200)
    const sec = page.locator('details', { hasText: '盤口與大單' })
    await sec.screenshot({ path: path.join(out, `${name}-1-live-10.png`) })
    await page.locator('.cq-fu-flow-head button', { hasText: '30 口' }).click()
    await page.waitForTimeout(800)
    await sec.screenshot({ path: path.join(out, `${name}-2-live-30.png`) })
    await page.screenshot({ path: path.join(out, `${name}-3-live-page.png`), fullPage: true })
    // 復盤：10-28 拉到約三分之一（09:3x），展開
    await page.getByRole('button', { name: /復盤/ }).click()
    await page.waitForSelector('.cq-fu-scrub input', { timeout: 15000 })
    const slider = page.locator('.cq-fu-scrub input')
    await slider.fill(String(Math.floor(Number(await slider.getAttribute('max')) * 0.35)))
    await page.waitForTimeout(600)
    const sec2 = page.locator('details', { hasText: '盤口與大單' })
    if (!(await sec2.getAttribute('open'))) await page.getByText('盤口與大單').click()
    await page.waitForSelector('.cq-fu-flow-chart canvas', { timeout: 15000 })
    await page.waitForTimeout(1200)
    await sec2.screenshot({ path: path.join(out, `${name}-4-replay.png`) })
    // 微台盤中（預設 20 口）
    await page.getByRole('button', { name: '微台' }).click()
    await page.getByRole('button', { name: /盤中/ }).click()
    await page.waitForSelector('.cq-fu-chart canvas', { timeout: 15000 })
    await page.getByText('盤口與大單').click()
    await page.waitForSelector('.cq-fu-flow-chart canvas', { timeout: 15000 })
    await page.waitForTimeout(1200)
    await page.locator('details', { hasText: '盤口與大單' }).screenshot({ path: path.join(out, `${name}-5-tmf-live.png`) })
    await ctx.close()
  }
  await b.close()
  console.log('done')
})()
