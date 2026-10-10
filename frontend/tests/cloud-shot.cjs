// 雲端版截圖（T-332 手動檢查用，不進 npm test）：node tests/cloud-shot.cjs <url> <out.png> [width] [height]
const { chromium } = require('playwright')

;(async () => {
  const [url, out, w = '1440', h = '900'] = process.argv.slice(2)
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: Number(w), height: Number(h) } })
  // 網站在 Cloudflare Access 後面時，用服務權杖通過登入（環境變數 CF_ACCESS_CLIENT_ID / SECRET）
  if (process.env.CF_ACCESS_CLIENT_ID && process.env.CF_ACCESS_CLIENT_SECRET) {
    await page.setExtraHTTPHeaders({
      'CF-Access-Client-Id': process.env.CF_ACCESS_CLIENT_ID,
      'CF-Access-Client-Secret': process.env.CF_ACCESS_CLIENT_SECRET,
    })
  }
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
  await page.goto(url, { waitUntil: 'networkidle' })
  await page.waitForSelector('.cq-chart canvas', { timeout: 30000 })
  await page.waitForTimeout(800)
  await page.screenshot({ path: out })
  console.log(JSON.stringify({ rows: await page.locator('.cq-list li').count(), errors }))
  await browser.close()
})()
