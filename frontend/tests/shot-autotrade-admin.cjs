// 本機後台截圖與操作（手動檢查用，autotrade A-012）：node tests/shot-autotrade-admin.cjs <後台網址> <outDir> <pressCmd>
// 後台要先開好（python -m autotrade admin …，最好用一份設定的複本）。pressCmd 是「模擬網站按停止」的指令（例：node stop_e2e.ts press），
// 演練開起來、買進之後執行一次。會真的在後台存一次設定、開一場演練再停掉。
const { chromium } = require('playwright')
const path = require('node:path')
const { execSync } = require('node:child_process')

;(async () => {
  const [base, out, pressCmd] = process.argv.slice(2)
  const b = await chromium.launch()
  for (const [w, h, m, name] of [[390, 844, true, 'm-admin'], [1280, 900, false, 'd-admin']]) {
    const ctx = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: m ? 2 : 1 })
    const page = await ctx.newPage()
    page.on('pageerror', (e) => console.log('pageerror:', String(e.stack || e).slice(0, 400)))
    await page.goto(base, { waitUntil: 'domcontentloaded' })
    await page.waitForSelector('#cfg')
    await page.screenshot({ path: path.join(out, `${name}-1-settings.png`), fullPage: true })
    const wide = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
    console.log(name, 'horizontal-overflow:', wide)
    if (!m) {
      // 存一次設定：一個不合格的（應該被擋）、一個合格的
      await page.fill('[data-id="risk.force_flat_time"]', '12:00:00')
      await page.click('#save')
      await page.waitForSelector('.msg.bad')
      await page.screenshot({ path: path.join(out, `${name}-2-rejected.png`) })
      await page.click('#undo')
      await page.fill('[data-id="risk.daily_loss_limit_twd"]', '3000')
      await page.click('#save')
      await page.waitForSelector('.msg.ok')
      console.log('saved:', await page.textContent('.msg.ok'))
      await page.screenshot({ path: path.join(out, `${name}-3-saved.png`) })
      // 開演練：拿 11-12 的價格、8 倍速、中繼程式連本機資料庫
      await page.click('#tab-drill')
      await page.waitForSelector('#d-start')
      await page.selectOption('#d-ticks', { label: '2026-11-12' })
      await page.fill('#d-from', '09:12:00')
      await page.fill('#d-min', '3')
      await page.fill('#d-speed', '8')
      await page.selectOption('#d-dsn', 'TWQUANT_PG_DSN')
      await page.screenshot({ path: path.join(out, `${name}-4-drill-form.png`), fullPage: true })
      await page.click('#d-start')
      await page.waitForFunction(() => /BUY 1 口 \d/.test(document.body.innerText), null, { timeout: 60000 })
      console.log('bought; pressing stop as the website would')
      console.log(execSync(pressCmd, { encoding: 'utf8' }).trim().slice(0, 300))
      await page.waitForFunction(() => document.body.innerText.includes('收到遠端緊急停止'), null, { timeout: 60000 })
      await page.waitForTimeout(6000)
      await page.screenshot({ path: path.join(out, `${name}-5-drill-stopped.png`), fullPage: true })
      await page.click('#d-stop')
      await page.waitForSelector('.lamp.off')
      await page.screenshot({ path: path.join(out, `${name}-6-drill-ended.png`), fullPage: true })
    } else {
      await page.click('#tab-drill')
      await page.waitForSelector('.lamp')
      await page.waitForTimeout(500)
      await page.screenshot({ path: path.join(out, `${name}-4-drill.png`), fullPage: true })
    }
    await ctx.close()
  }
  await b.close()
})()
