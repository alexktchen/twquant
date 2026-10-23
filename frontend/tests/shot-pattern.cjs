// 名單型態標記截圖（T-440／D-215 手動檢查用，不進 npm test）：node tests/shot-pattern.cjs <baseUrl> <outDir>
// 後端不用在線：/api/picks 用這裡湊的假名單（三檔各一種型態，股票全是假的），其他 /api/* 回空的。
// 開發伺服器要用雲端版：VITE_APP_MODE=cloud npx vite --port 5179
const { chromium } = require('playwright')
const path = require('node:path')

const DATE = '2026-10-23'
const recent = [true, false, true, true, false, true]
const pick = (rank, sid, name, close, chg, pattern, extra = {}) => ({
  rank,
  sid,
  name,
  group: null,
  reasons: null,
  similar: null,
  score: 4.4 - rank * 0.6,
  close,
  prev: Math.round((close / (1 + chg)) * 100) / 100,
  chg,
  streak: 1,
  new: true,
  recent,
  chg6: chg + 0.02,
  weak: null,
  insider: null,
  holders: null,
  stop_hit: null,
  risks: [],
  down_risk: null,
  blurb: null,
  review: null,
  pattern,
  ...extra,
})
const picksDay = {
  date: DATE,
  window: ['2026-10-16', '2026-10-17', '2026-10-20', '2026-10-21', '2026-10-22', DATE],
  drop: null,
  lines: [
    { code: 'CORE', name: '大盤精選', picks: [] },
    { code: 'QUIET', name: '潛伏飆股', picks: [] },
    { code: 'QUIETV', name: '高分爆量', note: '驗證中', picks: [] },
    {
      code: 'BREAKOUT',
      name: '帶量突破',
      note: '觀察中：收盤站上前 60 個交易日最高價（高出 0.5% 以上）、而且當天成交量是前 20 天平均的 2 倍以上才列入。',
      picks: [
        pick(1, '4577', '達航科技', 101.5, 0.0972, { kind: 'flag', over: 0.037, pole: 0.38, days: 15 }),
        pick(2, '4566', '時碩工業', 84.1, 0.049, { kind: 'n', over: 0.012, pole: 0.27, days: 9, retrace: 0.62 }),
        pick(3, '3715', '定穎投控', 134.0, 0.019, { kind: 'box', over: 0.021, width: 0.09, days: 60 }),
        pick(4, '4927', '泰鼎-KY', 58.9, 0.0517, null),
      ],
    },
  ],
}

;(async () => {
  const [base, out] = process.argv.slice(2)
  const b = await chromium.launch()
  for (const [w, h, m, name] of [[390, 844, true, 'm-pattern'], [1360, 900, false, 'd-pattern']]) {
    const ctx = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: m ? 2 : 1, isMobile: m, hasTouch: m })
    const page = await ctx.newPage()
    // 登入狀態（T-439）：存一個假的 Supabase 權杖，/api/me 又被攔下回 email，畫面就當成已登入
    await page.addInitScript(() =>
      localStorage.setItem('twquant.session', JSON.stringify({ access_token: 'x', refresh_token: 'y', expires_at: 4102444800, email: 'cktalex@gmail.com' })),
    )
    page.on('pageerror', (e) => console.error('pageerror', (e && e.stack) || String(e)))
    page.on('console', (m) => m.type() === 'error' && console.error('console', m.text().slice(0, 200)))
    await page.route('**/api/**', (r) => r.fulfill({ json: {} }))
    await page.route('**/pub/**', (r) => r.fulfill({ json: {} }))
    await page.route('**/api/layout', (r) => r.fulfill({ json: { layout: null } }))
    await page.route('**/api/me', (r) => r.fulfill({ json: { email: 'cktalex@gmail.com' } }))
    await page.route('**/api/access', (r) => r.fulfill({ json: { features: ['picks', 'score', 'line'] } }))
    await page.route('**/api/watchlist', (r) => r.fulfill({ json: { max: 20, date: DATE, items: [] } }))
    await page.route('**/api/trades', (r) => r.fulfill({ json: { today: DATE, items: [], max: 50 } }))
    await page.route('**/api/picks**', (r) => r.fulfill({ json: picksDay }))
    // 後登記的先比對：dates 要排在 picks** 後面，不然會被它接走
    await page.route('**/api/picks/dates', (r) => r.fulfill({ json: { dates: [DATE], counts: { [DATE]: { BREAKOUT: 4 } }, since: { BREAKOUT: '2026-10-23' } } }))
    await page.route('**/api/stocks/**', (r) => r.fulfill({ json: {} }))
    await page.route('**/api/stocks/*/ohlc**', (r) => r.fulfill({ json: { rows: [] } }))
    await page.route('**/api/stocks/*/picks', (r) => r.fulfill({ json: { days: {} } }))
    await page.route('**/api/stocks/*/calendar', (r) => r.fulfill({ json: { sid: '', today: DATE, items: [] } }))
    await page.route('**/api/stocks/*/stophit', (r) => r.fulfill({ json: { sid: '', today: DATE, hit: null } }))
    await page.route('**/api/stocks/*/warrants', (r) => r.fulfill({ json: { sid: '', today: DATE, days: [] } }))
    await page.route('**/api/stocks/*/holders', (r) => r.fulfill({ json: { sid: '', today: DATE, weeks: [] } }))
    await page.route('**/api/stocks/*/scores', (r) => r.abort()) // 模型評分沒有假資料：讓它顯示讀不到
    await page.route('**/api/quotes**', (r) => r.fulfill({ json: { quotes: {} } }))
    await page.route('**/api/scorecard**', (r) => r.fulfill({ json: null }))
    const newsHealth = { now: '2026-10-23T10:30:00Z', last_ok_at: '2026-10-23T10:30:00Z', stale: false, sources: [] }
    const newsDigest = { as_of: '2026-10-23T10:30:00Z', base_days: 7, topics: { items: [], everywhere: [] }, multi: [], hot: [] }
    for (const pre of ['**/api/news', '**/pub/news']) {
      await page.route(`${pre}/items**`, (r) => r.fulfill({ json: { items: [], next: null } }))
      await page.route(`${pre}/health`, (r) => r.fulfill({ json: newsHealth }))
      await page.route(`${pre}/digest`, (r) => r.fulfill({ json: newsDigest }))
      await page.route(`${pre}/counts**`, (r) => r.fulfill({ json: { counts: {} } }))
      await page.route(`${pre}/stock-days**`, (r) => r.fulfill({ json: { id: '', days: [] } }))
      await page.route(`${pre}/stock**`, (r) => r.fulfill({ json: { items: [], days: [] } }))
      await page.route(`${pre}/stock-days**`, (r) => r.fulfill({ json: { id: '', days: [] } }))
    }
    await page.route('**/api/warroom**', (r) => r.fulfill({ json: null }))
    await page.goto(`${base}/?tab=picks&line=BREAKOUT`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(1500)
    const btn = page.getByRole('button', { name: '同意並進入' })
    if ((await btn.count()) > 0) {
      await page.locator('input[type="checkbox"]').first().check()
      await btn.click()
    }
    // 網址的 line= 不會選線：點「帶量突破」那顆
    await page.getByRole('button', { name: /帶量突破/ }).first().click()
    await page.waitForTimeout(500)
    try {
      await page.waitForSelector('.cq-list li', { timeout: 20000 })
    } catch (e) {
      console.error('list not rendered:', (await page.locator('body').innerText()).slice(0, 400))
    }
    await page.waitForTimeout(1200)
    await page.screenshot({ path: path.join(out, `${name}.png`) })
    console.log(name, 'rows', await page.locator('.cq-list li').count())
    await ctx.close()
  }
  await b.close()
})()
