// 2026-10-18 改版截圖（手動檢查用，不進 npm test）：node tests/shot-v2.cjs <baseUrl> <outDir> <listDate> <line> <sid>
// 桌機名單＋個股、桌機個股整頁、手機名單、手機名單＋圖示說明、手機個股整頁、手機追蹤。
// 追蹤股與下單 API 本機拿不到 Cloudflare 權杖 → 用假資料攔下（只為了看畫面）。
const { chromium } = require('playwright')
const path = require('node:path')

;(async () => {
  const [base, out, date, line, sid] = process.argv.slice(2)
  const browser = await chromium.launch()
  const shots = []
  const open = async (w, h, mobile) => {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: mobile ? 2 : 1, isMobile: mobile, hasTouch: mobile })
    const page = await ctx.newPage()
    await page.setExtraHTTPHeaders({ 'cf-access-authenticated-user-email': 'cktalex@gmail.com' })
    await page.route('**/api/trades', (r) =>
      r.fulfill({ json: { today: '2026-10-18', max: 5000, items: [{ sid, date, side: 'buy', price: null, qty: null, source: 'pick', line: 'CORE', created_at: '' }] } }),
    )
    await page.route('**/api/watchlist', (r) => r.fulfill({ json: { max: 100, date, items: [] } }))
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
  const snap = async (page, name, full = false) => {
    const file = path.join(out, name)
    await page.screenshot({ path: file, fullPage: full })
    shots.push(file)
  }
  const gotoPick = async (page) => {
    await page.goto(`${base}/?tab=picks`, { waitUntil: 'domcontentloaded', timeout: 60000 })
    await page.waitForSelector('.cq-list li', { timeout: 60000 })
    await agree(page)
    await page.selectOption('select[aria-label="名單日期"]', date)
    await page.waitForTimeout(2000)
    await page.locator('.cq-seg button', { hasText: line }).click()
    await page.waitForTimeout(1200)
    await page.locator('.cq-list li', { hasText: sid }).first().click()
    await page.waitForTimeout(3500)
  }
  // 桌機
  let page = await open(1440, 1000, false)
  await gotoPick(page)
  await snap(page, 'd1-picks.png')
  await page.close()
  page = await open(1440, 2200, false)
  await gotoPick(page)
  await snap(page, 'd2-detail-tall.png')
  await page.close()
  // 手機
  page = await open(390, 844, true)
  await gotoPick(page)
  await page.evaluate(() => window.scrollTo(0, 0))
  await snap(page, 'm1-list.png')
  await page.getByRole('button', { name: /圖示說明/ }).click()
  await page.waitForTimeout(500)
  await snap(page, 'm2-legend.png')
  await page.getByRole('button', { name: '關閉' }).click()
  await page.locator('.cq-detail').scrollIntoViewIfNeeded()
  await page.waitForTimeout(800)
  await snap(page, 'm3-detail.png')
  await snap(page, 'm4-full.png', true)
  await page.close()
  page = await open(390, 844, true)
  await page.goto(`${base}/?tab=watch`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.waitForTimeout(2500)
  await agree(page)
  await snap(page, 'm5-watch.png')
  await page.close()
  await browser.close()
  console.log(JSON.stringify({ shots }))
})()
