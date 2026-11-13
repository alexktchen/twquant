// 程式交易截圖（手動檢查用，autotrade A-005）：node tests/shot-autotrade.cjs <baseUrl> <outDir> <mockDir>
// mockDir 放 `autotrade replay --status-out` 寫出來的 sim_<日>.json（至少一個；主 checkout 的 data/autotrade/status/）。其他 /api/* 回空的。
// 第二張「盤中停手」是把最新那天的狀態改幾個欄位做出來的假資料，只為了看紅燈、部位與大事的版面。
const { chromium } = require('playwright')
const fs = require('node:fs')
const path = require('node:path')

;(async () => {
  const [base, out, mock] = process.argv.slice(2)
  const all = fs
    .readdirSync(mock)
    .filter((f) => /^sim_.*\.json$/.test(f))
    .map((f) => JSON.parse(fs.readFileSync(path.join(mock, f), 'utf8')))
    .sort((a, b) => (a.day < b.day ? 1 : -1))
  if (all.length === 0) throw new Error(`${mock} 裡沒有 sim_<日>.json`)
  const days = all.map((s) => ({ day: s.day, status: s.status, pnl: s.day_pnl_twd, fills: s.fills.length }))
  const halted = () => {
    // 交易程式寫出來的時間一律帶 +08:00（畫面直接取字串裡的時分秒）
    const now = new Date(Date.now() - 8000 + 8 * 3600e3).toISOString().replace(/\.\d+Z$/, '+08:00')
    const s = { ...all[0], status: 'live', state: 'halted', halt: 'mismatch', corrected: true, updated_at: now }
    s.position = 1
    s.avg_price = s.last_price - 12
    s.open_pnl_twd = 120
    s.day_pnl_twd = -1880
    s.realized_twd = -2000
    s.limits = { ...s.limits, daily_loss_limit_twd: 3000 }
    s.events = [
      { ts: `${s.day}T10:02:11+08:00`, kind: 'link', text: '券商連線中斷：暫停送單與刪單' },
      { ts: `${s.day}T10:02:40+08:00`, kind: 'link', text: '券商連線恢復：補回報並比對委託與部位' },
      { ts: `${s.day}T10:02:40+08:00`, kind: 'correction', text: '部位對不上：帳上 0 口、券商 1 口；帳改成券商的數字，停手並平倉' },
      { ts: `${s.day}T10:02:40+08:00`, kind: 'halt', text: '部位對不上，停手（mismatch）' },
    ]
    return s
  }
  const b = await chromium.launch()
  for (const [w, h, m, name] of [[390, 844, true, 'm-autotrade'], [1360, 900, false, 'd-autotrade']]) {
    for (const [tag, current] of [['closed', all[0]], ['halted', halted()]]) {
      const ctx = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: m ? 2 : 1, isMobile: m, hasTouch: m })
      const page = await ctx.newPage()
      page.on('pageerror', (e) => console.log('pageerror:', String(e.stack || e).slice(0, 600)))
      await page.addInitScript(() =>
        localStorage.setItem('twquant.session', JSON.stringify({ access_token: 'x', refresh_token: 'y', expires_at: 4102444800, email: 'someone@example.com' })),
      )
      await page.route('**/api/**', (r) => r.fulfill({ json: {} }))
      await page.route('**/pub/**', (r) => r.fulfill({ json: {} }))
      await page.route('**/api/picks**', (r) => r.fulfill({ json: { date: all[0].day, lines: [] } }))
      await page.route('**/api/picks/dates', (r) => r.fulfill({ json: { dates: [all[0].day], counts: {}, since: {} } }))
      await page.route('**/api/quotes**', (r) => r.fulfill({ json: { quotes: {} } }))
      await page.route('**/api/scorecard**', (r) => r.fulfill({ json: null }))
      await page.route('**/api/warroom**', (r) => r.fulfill({ json: null }))
      await page.route('**/api/layout', (r) => r.fulfill({ json: { layout: null } }))
      await page.route('**/api/me', (r) => r.fulfill({ json: { email: 'someone@example.com' } }))
      await page.route('**/api/access', (r) => r.fulfill({ json: { features: ['futures'] } }))
      await page.route('**/api/watchlist', (r) => r.fulfill({ json: { max: 20, date: null, items: [] } }))
      await page.route('**/api/trades', (r) => r.fulfill({ json: { today: all[0].day, items: [], max: 50 } }))
      await page.route('**/news/**', (r) => r.fulfill({ json: { items: [], next: null } }))
      await page.route('**/api/futures/days', (r) => r.fulfill({ json: { days: [] } }))
      // 預設先開的小台模擬沒有假資料：回 404 讓它顯示「還沒有紀錄」（回 {} 會讓那一頁壞掉）
      await page.route(/\/api\/futures(\?.*)?$/, (r) => r.fulfill({ status: 404, json: { error: '沒有紀錄' } }))
      await page.route('**/api/autotrade?mode=*', (r) => {
        const q = new URL(r.request().url()).searchParams
        if (q.get('mode') === 'live') return r.fulfill({ json: { days: [], current: null } })
        const d = q.get('day')
        r.fulfill({ json: { days, current: d ? all.find((s) => s.day === d) : current } })
      })
      await page.goto(`${base}/?tab=futures`, { waitUntil: 'domcontentloaded' })
      await page.waitForTimeout(1500)
      const btn = page.getByRole('button', { name: '同意並進入' })
      if ((await btn.count()) > 0) {
        await page.locator('input[type="checkbox"]').first().check()
        await btn.click()
      }
      await page.getByRole('button', { name: '程式交易' }).click()
      await page.waitForSelector('.cq-at-lamp', { timeout: 15000 })
      await page.waitForTimeout(400)
      await page.screenshot({ path: path.join(out, `${name}-${tag}.png`), fullPage: true })
      if (tag === 'closed') {
        await page.getByRole('button', { name: '真單' }).click()
        await page.waitForTimeout(600)
        await page.screenshot({ path: path.join(out, `${name}-live-empty.png`) })
      }
      const wide = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
      console.log(name, tag, 'horizontal-overflow:', wide)
      await ctx.close()
    }
  }
  await b.close()
})()
