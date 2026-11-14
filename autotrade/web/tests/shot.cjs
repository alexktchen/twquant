// 截圖（手動檢查用，autotrade A-018）：node shot.cjs <baseUrl> <outDir> <mockDir>
// mockDir 放 `autotrade replay --status-out` 寫出來的 sim_<日>.json（主 checkout 的 data/autotrade/status/）。
// 全部是假的回應，不碰任何資料庫。要用裝了 playwright 的地方跑（例：複製到 frontend/tests 底下再執行）。
const { chromium } = require('playwright')
const fs = require('node:fs')
const path = require('node:path')

const iso = (ms) => new Date(ms + 8 * 3600e3).toISOString().replace(/\.\d+Z$/, '+08:00')
const HEARING = { relay_age_s: 1, engine_age_s: 1.4, poll_s: 2, seen_today: true }
const ADMIN = { age_s: 1, reader_age_s: 1, poll_s: 2, seen_today: true }

;(async () => {
  const [base, out, mock] = process.argv.slice(2)
  const all = fs
    .readdirSync(mock)
    .filter((f) => /^sim_.*\.json$/.test(f))
    .map((f) => JSON.parse(fs.readFileSync(path.join(mock, f), 'utf8')))
    .sort((a, b) => (a.day < b.day ? 1 : -1))
  const days = all.map((s) => ({ day: s.day, status: s.status, pnl: s.day_pnl_twd, fills: s.fills.length }))
  const live = { ...all[0], status: 'live', source: 'live', updated_at: iso(Date.now() - 3000), control: { listening: true, ok: true, checked_at: iso(Date.now() - 2000), poll_s: 2, last_command_id: 0 } }
  const b = await chromium.launch()

  const open = async (w, h, m, me, state) => {
    const ctx = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: m ? 2 : 1, isMobile: m, hasTouch: m })
    const page = await ctx.newPage()
    page.on('pageerror', (e) => console.log('pageerror:', String(e.stack || e).slice(0, 600)))
    await page.route('**/api/me', (r) => r.fulfill(me))
    await page.route('**/api/autotrade?mode=*', (r) => {
      const mode = new URL(r.request().url()).searchParams.get('mode')
      r.fulfill({ json: { days: mode === 'live' ? [] : days, current: mode === 'live' ? null : state.current, stop: null, listeners: state.beats, requests: state.rows, remote: state.admin } })
    })
    await page.route('**/api/autotrade/command', (r) => {
      const asked = JSON.parse(r.request().postData() || '{}')
      state.rows.unshift({ id: String(60 + state.rows.length), action: asked.confirm, target: asked.target, created_at: iso(Date.now()), age_s: 0, acked_at: null, ack_age_s: null, code: null, note: null, done_at: null, done_code: null, done_note: null })
      r.fulfill({ json: { ok: true, reused: false, requests: state.rows, remote: state.admin } })
    })
    await page.goto(base, { waitUntil: 'domcontentloaded' })
    return { ctx, page }
  }
  const overflow = (page) => page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)

  for (const [w, h, m, name] of [[390, 844, true, 'm'], [1360, 900, false, 'd']]) {
    {
      const state = { current: live, beats: { sim: HEARING }, admin: ADMIN, rows: [] }
      const { ctx, page } = await open(w, h, m, { json: { email: 'someone@example.com' } }, state)
      await page.waitForSelector('.cq-at-remote-btns', { timeout: 15000 })
      await page.waitForTimeout(500)
      await page.screenshot({ path: path.join(out, `${name}-1-page.png`), fullPage: true })
      const btns = await page.$$eval('.cq-at-remote-btn', (els) => els.map((e) => `${e.innerText.trim()}${e.disabled ? '（不能按）' : ''}`))
      console.log(`${name} page | overflow:`, await overflow(page), '|', await page.locator('.cq-who').innerText(), '|', btns.join(' / '))
      await page.getByRole('button', { name: '暫停自動交易' }).click()
      await page.getByRole('button', { name: '確定暫停自動交易' }).click()
      await page.waitForSelector('.cq-at-remote-lines li.wait')
      await page.screenshot({ path: path.join(out, `${name}-2-sent.png`), fullPage: false })
      await ctx.close()
    }
    for (const [tag, me] of [
      ['not-set-up', { status: 503, json: { detail: '這個網站的登入還沒設定好（ACCESS_TEAM、ACCESS_AUD、AUTOTRADE_ADMINS 三個都要有）' } }],
      ['no-access', { status: 403, json: { detail: '沒有權限' } }],
    ]) {
      const { ctx, page } = await open(w, h, m, me, { current: live, beats: {}, admin: null, rows: [] })
      await page.waitForSelector('.cq-gate', { timeout: 15000 })
      const buttons = await page.locator('.cq-at-stop-btn, .cq-at-remote-btn').count()
      console.log(`${name} ${tag} | overflow:`, await overflow(page), '| 交易按鈕數:', buttons, '|', (await page.locator('.cq-gate').innerText()).replace(/\n/g, ' ／ '))
      await page.screenshot({ path: path.join(out, `${name}-3-${tag}.png`), fullPage: true })
      await ctx.close()
    }
  }
  await b.close()
})()
