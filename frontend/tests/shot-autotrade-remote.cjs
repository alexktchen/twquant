// 遙控按鈕截圖（手動檢查用，autotrade A-016、A-017）：node tests/shot-autotrade-remote.cjs <baseUrl> <outDir> <mockDir>
// mockDir 同 shot-autotrade.cjs（主 checkout 的 data/autotrade/status/，至少一個 sim_<日>.json）。
// 全部是假的回應，不會碰任何資料庫：按鈕能按／不能按、確認、等它收到、完成、作廢、暫停中。
const { chromium } = require('playwright')
const fs = require('node:fs')
const path = require('node:path')

const iso = (ms) => new Date(ms + 8 * 3600e3).toISOString().replace(/\.\d+Z$/, '+08:00')
const HEARING = { relay_age_s: 1, engine_age_s: 1.4, poll_s: 2, seen_today: true }
const ADMIN = { age_s: 1, reader_age_s: 1, poll_s: 2, seen_today: true }

// 假的遙控現況：rows 是 [{id, action, target, press, ack?: {at, code, note}, done?: {at, code, note}}]
const remote = (state) => ({
  requests: state.rows.map((r) => ({
    id: String(r.id),
    action: r.action,
    target: r.target,
    created_at: iso(r.press),
    age_s: (Date.now() - r.press) / 1000,
    acked_at: r.ack ? iso(r.ack.at) : null,
    ack_age_s: r.ack ? (Date.now() - r.ack.at) / 1000 : null,
    code: r.ack ? r.ack.code : null,
    note: r.ack ? r.ack.note : null,
    done_at: r.done ? iso(r.done.at) : null,
    done_code: r.done ? r.done.code : null,
    done_note: r.done ? r.done.note : null,
  })),
  remote: state.admin,
})

;(async () => {
  const [base, out, mock] = process.argv.slice(2)
  const all = fs
    .readdirSync(mock)
    .filter((f) => /^sim_.*\.json$/.test(f))
    .map((f) => JSON.parse(fs.readFileSync(path.join(mock, f), 'utf8')))
    .sort((a, b) => (a.day < b.day ? 1 : -1))
  if (all.length === 0) throw new Error(`${mock} 裡沒有 sim_<日>.json`)
  const days = all.map((s) => ({ day: s.day, status: s.status, pnl: s.day_pnl_twd, fills: s.fills.length }))
  const live = (more) => ({
    ...all[0],
    status: 'live',
    source: 'live',
    updated_at: iso(Date.now() - 3000),
    control: { listening: true, ok: true, checked_at: iso(Date.now() - 2000), poll_s: 2, last_command_id: 0 },
    ...more,
  })
  const b = await chromium.launch()

  const open = async (w, h, m, state) => {
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
    await page.route(/\/api\/futures(\?.*)?$/, (r) => r.fulfill({ status: 404, json: { error: '沒有紀錄' } }))
    await page.route('**/api/autotrade?mode=*', (r) => {
      const mode = new URL(r.request().url()).searchParams.get('mode')
      const current = mode === 'live' ? null : state.current
      r.fulfill({ json: { days: mode === 'live' ? [] : days, current, stop: null, listeners: state.beats, ...remote(state) } })
    })
    await page.route('**/api/autotrade/command', (r) => {
      const asked = JSON.parse(r.request().postData() || '{}')
      state.rows.unshift({ id: 60 + state.rows.length, action: asked.confirm, target: asked.target, press: Date.now() })
      r.fulfill({ json: { ok: true, reused: false, ...remote(state) } })
    })
    await page.goto(`${base}/?tab=futures`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(1500)
    const btn = page.getByRole('button', { name: '同意並進入' })
    if ((await btn.count()) > 0) {
      await page.locator('input[type="checkbox"]').first().check()
      await btn.click()
    }
    await page.getByRole('button', { name: '程式交易' }).click()
    await page.waitForSelector('.cq-at-remote-btns', { timeout: 15000 })
    await page.waitForTimeout(400)
    return { ctx, page }
  }
  const check = async (page, label) => {
    const wide = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
    const btns = await page.$$eval('.cq-at-remote-btn', (els) => els.map((e) => `${e.innerText.trim()}${e.disabled ? '（不能按）' : ''}`))
    console.log(label, '| overflow:', wide, '|', btns.join(' / '))
  }
  const shot = (page, name) => page.locator('.cq-at-remote').screenshot({ path: path.join(out, `${name}.png`) })

  for (const [w, h, m, name] of [[390, 844, true, 'm-remote'], [1360, 900, false, 'd-remote']]) {
    // 1. 模擬在跑、後台在聽：三顆都能按 → 按平倉 → 確認 → 等它收到 → 已收到 → 完成
    {
      const state = { current: live({}), beats: { sim: HEARING }, admin: ADMIN, rows: [] }
      const { ctx, page } = await open(w, h, m, state)
      await page.screenshot({ path: path.join(out, `${name}-1-page.png`), fullPage: true })
      await check(page, `${name} running`)
      await page.getByRole('button', { name: '平倉' }).click()
      await shot(page, `${name}-2-confirm`)
      await page.getByRole('button', { name: '確定平倉' }).click()
      await page.waitForSelector('.cq-at-remote-lines li.wait')
      await shot(page, `${name}-3-waiting`)
      state.rows[0].ack = { at: Date.now(), code: 'flattening', note: '已送出平倉；帳上多 1 口（09:30:05 按下，09:30:05 執行）' }
      state.rows[0].done = { at: Date.now(), code: 'flat', note: '已確認沒有部位、沒有委託；帳上沒有部位' }
      await page.waitForSelector('.cq-at-remote-lines li.done', { timeout: 12000 })
      await shot(page, `${name}-4-done`)
      await ctx.close()
    }
    // 2. 暫停中：第二顆變「恢復自動交易」；一筆作廢、一筆被拒絕
    {
      const old = Date.now() - 95_000
      const rows = [
        { id: 71, action: 'start', target: 'sim', press: Date.now() - 20_000, ack: { at: Date.now() - 19_000, code: 'accepted', note: '後台收到了' }, done: { at: Date.now() - 19_000, code: 'refused', note: '交易程式已經在跑' } },
        { id: 70, action: 'flatten', target: 'sim', press: old },
      ]
      const { ctx, page } = await open(w, h, m, { current: live({ paused: true }), beats: { sim: HEARING }, admin: ADMIN, rows })
      await check(page, `${name} paused`)
      await shot(page, `${name}-5-paused`)
      await ctx.close()
    }
    // 3. 什麼都沒在聽（現在的樣子）：三顆都不能按，寫原因；真單分頁：模擬在跑也不能平真單
    {
      const { ctx, page } = await open(w, h, m, { current: all[0], beats: {}, admin: null, rows: [] })
      await check(page, `${name} nobody`)
      await shot(page, `${name}-6-nobody`)
      await ctx.close()
      const two = await open(w, h, m, { current: live({}), beats: { sim: HEARING }, admin: ADMIN, rows: [] })
      await two.page.getByRole('button', { name: '真單' }).click()
      await two.page.waitForTimeout(600)
      await check(two.page, `${name} live-tab`)
      await shot(two.page, `${name}-7-live-tab`)
      await two.ctx.close()
    }
  }
  await b.close()
})()
