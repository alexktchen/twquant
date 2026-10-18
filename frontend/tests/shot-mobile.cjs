// 手機版截圖（2026-10-18 使用者嫌頁面太雜，改版後對照用；不進 npm test）：
//   node tests/shot-mobile.cjs <baseUrl> <outDir> <listDate> <line> <sid>
// 名單分頁（指定線與個股，含下方個股頁整頁）＋追蹤分頁（我的下單收起）。追蹤股與下單 API 本機拿不到權杖，用假資料攔下。
const { chromium } = require('playwright')
const path = require('node:path')

;(async () => {
  const [base, out, date, line, sid] = process.argv.slice(2)
  const browser = await chromium.launch()
  const shots = []
  const open = async () => {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })
    const page = await ctx.newPage()
    await page.setExtraHTTPHeaders({ 'cf-access-authenticated-user-email': 'cktalex@gmail.com' })
    await page.route('**/api/trades', (r) =>
      r.fulfill({ json: { today: '2026-10-18', max: 5000, items: [{ sid, date, side: 'buy', price: null, qty: null, source: 'pick', line, created_at: '' }] } }),
    )
    await page.route('**/api/watchlist', (r) => r.fulfill({ json: { max: 100, date, items: [] } }))
    return page
  }
  const agree = async (page) => {
    const btn = page.getByRole('button', { name: '同意並進入' })
    if ((await btn.count()) === 0) return
    await page.locator('input[type="checkbox"]').first().check()
    await btn.click()
    await page.waitForTimeout(800)
  }
  const snap = async (page, name, full) => {
    const file = path.join(out, name)
    await page.screenshot({ path: file, fullPage: full })
    shots.push(file)
  }
  let page = await open()
  await page.goto(`${base}/?tab=picks`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.waitForSelector('.cq-list li', { timeout: 60000 })
  await agree(page)
  await page.selectOption('select[aria-label="名單日期"]', date)
  await page.waitForTimeout(2000)
  await page.locator('.cq-seg button', { hasText: line }).click()
  await page.waitForTimeout(1500)
  await page.locator('.cq-list li', { hasText: sid }).first().click()
  await page.waitForTimeout(3500)
  await snap(page, `m1-picks-${line}.png`, false)
  await snap(page, `m2-picks-${line}-full.png`, true)
  await page.close()
  page = await open()
  await page.goto(`${base}/?tab=watch`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.waitForTimeout(2500)
  await agree(page)
  await page.waitForTimeout(800)
  await snap(page, 'm3-watch.png', false)
  await page.close()
  await browser.close()
  console.log(JSON.stringify({ shots }))
})()
