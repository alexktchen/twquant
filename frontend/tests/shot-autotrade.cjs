// 程式交易截圖（手動檢查用，autotrade A-005、A-011）：node tests/shot-autotrade.cjs <baseUrl> <outDir> <mockDir>
// mockDir 放 `autotrade replay --status-out` 寫出來的 sim_<日>.json（至少一個；主 checkout 的 data/autotrade/status/）。其他 /api/* 回空的。
// 「盤中停手」是把最新那天的狀態改幾個欄位做出來的假資料，只為了看紅燈、部位與大事的版面。
// 遠端緊急停止（A-011）的幾張都是假的回應，不會碰任何資料庫：
//   按下 → 確認 → 等兩支程式收到 → 模擬先回報、真單還在平 → 兩支都確認平掉；
//   以及最要緊的一張：模擬回報「好了」、真單沒回應——真單那一行必須是紅的。
const { chromium } = require('playwright')
const fs = require('node:fs')
const path = require('node:path')

// 交易程式與端點給的時間一律帶 +08:00（畫面直接取字串裡的時分秒）
const iso = (ms) => new Date(ms + 8 * 3600e3).toISOString().replace(/\.\d+Z$/, '+08:00')
const secondsSince = (ms) => (Date.now() - ms) / 1000

// 假的「遠端停止現況」：press 是按下的時間（毫秒），acks 是 { live: {at, code, note, done?: {at, code, note}} }，beats 是每支程式的心跳
const control = (state) => {
  const stop =
    state.press === null
      ? null
      : {
          id: '42',
          day: state.day,
          created_at: iso(state.press),
          age_s: secondsSince(state.press),
          acks: Object.fromEntries(
            Object.entries(state.acks).map(([who, a]) => [
              who,
              {
                acked_at: iso(a.at),
                ack_age_s: secondsSince(a.at),
                code: a.code,
                note: a.note,
                done_at: a.done ? iso(a.done.at) : null,
                done_code: a.done ? a.done.code : null,
                done_note: a.done ? a.done.note : null,
              },
            ]),
          ),
        }
  return { stop, listeners: state.beats }
}
const HEARING = { relay_age_s: 1, engine_age_s: 1.4, poll_s: 2, seen_today: true }
const GONE = { relay_age_s: 540, engine_age_s: 541, poll_s: 2, seen_today: true }

;(async () => {
  const [base, out, mock] = process.argv.slice(2)
  const all = fs
    .readdirSync(mock)
    .filter((f) => /^sim_.*\.json$/.test(f))
    .map((f) => JSON.parse(fs.readFileSync(path.join(mock, f), 'utf8')))
    .map((s) => ({ control: { listening: false, ok: null, checked_at: null, poll_s: null, last_command_id: 0 }, ...s }))
    .sort((a, b) => (a.day < b.day ? 1 : -1))
  if (all.length === 0) throw new Error(`${mock} 裡沒有 sim_<日>.json`)
  const days = all.map((s) => ({ day: s.day, status: s.status, pnl: s.day_pnl_twd, fills: s.fills.length }))
  const halted = () => {
    const now = iso(Date.now() - 8000)
    const s = { ...all[0], status: 'live', state: 'halted', halt: 'mismatch', corrected: true, updated_at: now }
    s.position = 1
    s.avg_price = s.last_price - 12
    s.open_pnl_twd = 120
    s.day_pnl_twd = -1880
    s.realized_twd = -2000
    s.limits = { ...s.limits, daily_loss_limit_twd: 3000 }
    s.control = { listening: true, ok: true, checked_at: now, poll_s: 2, last_command_id: 0 }
    s.events = [
      { ts: `${s.day}T10:02:11+08:00`, kind: 'link', text: '券商連線中斷：暫停送單與刪單' },
      { ts: `${s.day}T10:02:40+08:00`, kind: 'link', text: '券商連線恢復：補回報並比對委託與部位' },
      { ts: `${s.day}T10:02:40+08:00`, kind: 'correction', text: '部位對不上：帳上 0 口、券商 1 口；帳改成券商的數字，停手並平倉' },
      { ts: `${s.day}T10:02:40+08:00`, kind: 'halt', text: '部位對不上，停手（mismatch）' },
    ]
    return s
  }
  const b = await chromium.launch()

  // 開一頁、進到「程式交易」。state 是假的遠端停止現況，可以在過程中改。
  const open = async (w, h, m, current, state) => {
    state.day = all[0].day
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
      if (q.get('mode') === 'live') return r.fulfill({ json: { days: [], current: null, ...control(state) } })
      const d = q.get('day')
      r.fulfill({ json: { days, current: d ? all.find((s) => s.day === d) : current, ...control(state) } })
    })
    await page.route('**/api/autotrade/stop', (r) => {
      if (r.request().method() !== 'PUT') return r.fulfill({ status: 405, json: { detail: '只收 PUT' } })
      state.press = Date.now()
      state.acks = {}
      r.fulfill({ json: { ok: true, reused: false, ...control(state) } })
    })
    await page.goto(`${base}/?tab=futures`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(1500)
    const btn = page.getByRole('button', { name: '同意並進入' })
    if ((await btn.count()) > 0) {
      await page.locator('input[type="checkbox"]').first().check()
      await btn.click()
    }
    await page.getByRole('button', { name: '程式交易' }).click()
    await page.waitForSelector('.cq-at-stop-btn', { timeout: 15000 })
    await page.waitForTimeout(400)
    return { ctx, page }
  }
  const noOverflow = async (page, label) => {
    const wide = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
    console.log(label, 'horizontal-overflow:', wide)
  }
  const line = (page, i, tone) => page.waitForSelector(`.cq-at-stop-lines li:nth-child(${i}).${tone}`, { timeout: 12000 })

  for (const [w, h, m, name] of [[390, 844, true, 'm-autotrade'], [1360, 900, false, 'd-autotrade']]) {
    // 1. 現在的樣子：已收盤的真實資料，沒有任何程式在聽（按之前就要看得出來）＋真單分頁是空的
    {
      const { ctx, page } = await open(w, h, m, all[0], { press: null, acks: {}, beats: {} })
      await page.screenshot({ path: path.join(out, `${name}-closed.png`), fullPage: true })
      await noOverflow(page, `${name} closed`)
      await page.getByRole('button', { name: '真單' }).click()
      await page.waitForTimeout(600)
      await page.screenshot({ path: path.join(out, `${name}-live-empty.png`) })
      await ctx.close()
    }
    // 2. 盤中（假資料）兩支程式都在聽：按下 → 確認 → 等收到 → 模擬先好、真單還在平 → 都確認平掉
    {
      const state = { press: null, acks: {}, beats: { live: HEARING, sim: HEARING } }
      const { ctx, page } = await open(w, h, m, halted(), state)
      await page.screenshot({ path: path.join(out, `${name}-halted.png`), fullPage: true })
      await page.locator('.cq-at-stop-btn').click()
      await page.waitForSelector('.cq-at-stop-confirm')
      await page.screenshot({ path: path.join(out, `${name}-stop-1-confirm.png`) })
      await page.locator('.cq-at-stop-yes').click()
      await line(page, 1, 'wait')
      await page.screenshot({ path: path.join(out, `${name}-stop-2-wait.png`) })
      state.acks = {
        sim: { at: Date.now(), code: 'flat', note: '已停手；帳上沒有部位', done: { at: Date.now(), code: 'flat', note: '已確認沒有部位、沒有委託；帳上沒有部位' } },
        live: { at: Date.now(), code: 'flattening', note: '已停手，已送出平倉；帳上多 1 口' },
      }
      await line(page, 2, 'done') // 送出後每 3 秒更新
      await page.screenshot({ path: path.join(out, `${name}-stop-3-live-still-closing.png`) })
      state.acks.live.done = { at: Date.now(), code: 'flat', note: '已確認沒有部位、沒有委託；帳上沒有部位' }
      await line(page, 1, 'done')
      await page.screenshot({ path: path.join(out, `${name}-stop-4-both-flat.png`) })
      await noOverflow(page, `${name} stop`)
      await ctx.close()
    }
    // 3. 最要緊的一張：模擬回報「好了」，真單今天跑過但現在沒回應——真單那一行必須是紅的，還要提醒先看券商 App
    {
      const press = Date.now() - 40000
      const state = {
        press,
        acks: { sim: { at: press + 2000, code: 'flat', note: '已停手；帳上沒有部位', done: { at: press + 2000, code: 'flat', note: '已確認沒有部位、沒有委託；帳上沒有部位' } } },
        beats: { live: GONE, sim: HEARING },
      }
      const { ctx, page } = await open(w, h, m, all[0], state)
      await line(page, 1, 'bad')
      await page.screenshot({ path: path.join(out, `${name}-stop-5-live-not-received.png`) })
      await noOverflow(page, `${name} live-not-received`)
      await ctx.close()
    }
    // 4. 真單收到了但平不掉
    {
      const press = Date.now() - 50000
      const state = {
        press,
        acks: {
          live: { at: press + 2000, code: 'flattening', note: '已停手，已送出平倉；帳上多 1 口', done: { at: press + 23000, code: 'stuck', note: '過了 21 秒還沒確認平掉，請手動處理；帳上多 1 口' } },
        },
        beats: { live: HEARING },
      }
      const { ctx, page } = await open(w, h, m, all[0], state)
      await line(page, 1, 'bad')
      await page.screenshot({ path: path.join(out, `${name}-stop-6-live-stuck.png`) })
      await ctx.close()
    }
  }
  await b.close()
})()
