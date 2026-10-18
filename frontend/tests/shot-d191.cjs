// D-191～D-193 截圖（手動檢查用，不進 npm test）：node tests/shot-d191.cjs <baseUrl> <outDir> <listDate> [sid]
// 接本機 Pages 模擬器＋本機 PG；用 cf-access-authenticated-user-email 標頭讓 /api/me 當成已登入。
// 追蹤股與我的下單要 Cloudflare Access 簽的權杖，本機拿不到 → 這兩支 API 用假資料攔下（只為了看畫面；畫面上的數字都是真的，
// 只有「我的下單」兩筆與空的追蹤清單是假的）。
// 名單分頁每 10 秒抓即時報價，networkidle 永遠等不到 → 用 domcontentloaded 加固定等待。outDir 用 Windows 寫法（C:/…）。
const { chromium } = require('playwright')
const path = require('node:path')

;(async () => {
  const [base, out, date, sid] = process.argv.slice(2)
  const browser = await chromium.launch()
  const shots = []
  const fakeTrades = {
    today: '2026-10-18',
    max: 5000,
    items: [
      { sid: '1303', date, side: 'buy', price: null, qty: null, source: 'pick', line: 'CORE', created_at: '2026-10-18 10:00:00+08' },
      { sid: '2330', date: '2026-10-15', side: 'buy', price: 1460, qty: 1, source: 'manual', line: null, created_at: '2026-10-18 09:00:00+08' },
    ],
  }
  const open = async (w, h) => {
    const page = await browser.newPage({ viewport: { width: w, height: h } })
    await page.setExtraHTTPHeaders({ 'cf-access-authenticated-user-email': 'cktalex@gmail.com' })
    page.on('pageerror', (e) => console.error('pageerror', String(e)))
    await page.route('**/api/trades', (r) =>
      r.request().method() === 'GET' ? r.fulfill({ json: fakeTrades }) : r.fulfill({ json: { ok: true } }),
    )
    await page.route('**/api/watchlist', (r) => r.fulfill({ json: { max: 100, date, items: [] } }))
    return page
  }
  // 投資風險警語：勾同意、進入（記在瀏覽器；每個新 page 都要做一次）
  const agree = async (page) => {
    const btn = page.getByRole('button', { name: '同意並進入' })
    if ((await btn.count()) === 0) return
    await page.locator('input[type="checkbox"]').first().check()
    await btn.click()
    await page.waitForTimeout(800)
  }
  const snap = async (page, name) => {
    const file = path.join(out, name)
    await page.screenshot({ path: file })
    shots.push(file)
  }
  const gotoPicks = async (page) => {
    await page.goto(`${base}/?tab=picks`, { waitUntil: 'domcontentloaded', timeout: 60000 })
    await page.waitForSelector('.cq-list li', { timeout: 60000 })
    await agree(page)
    await page.selectOption('select[aria-label="名單日期"]', date)
    await page.waitForTimeout(3000)
  }

  // 1. 每日名單（桌機）
  let page = await open(1440, 1000)
  await gotoPicks(page)
  if (sid) await page.locator('.cq-list li', { hasText: sid }).first().click()
  await page.waitForTimeout(2500)
  await snap(page, `01-picks-${date}.png`)
  await page.close()
  // 2. 個股頁拉長視窗，整頁看到底（停損基礎率、行事曆、千張大戶、權證）
  page = await open(1440, 2300)
  await gotoPicks(page)
  if (sid) await page.locator('.cq-list li', { hasText: sid }).first().click()
  await page.waitForTimeout(3500)
  await snap(page, `02-stock-detail-${date}.png`)
  await page.close()
  // 3. 追蹤分頁（我的下單）
  page = await open(1440, 1000)
  await page.goto(`${base}/?tab=watch`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.waitForTimeout(2500)
  await agree(page)
  await page.waitForTimeout(1000)
  await snap(page, '03-watch-trades.png')
  await page.close()
  // 4. 手機寬度的名單
  page = await open(390, 844)
  await gotoPicks(page)
  await snap(page, `04-picks-mobile-${date}.png`)
  await page.close()
  await browser.close()
  console.log(JSON.stringify({ shots }))
})()
