// 星雲圖搜尋與牽連截圖（手動檢查用，T-436）：node tests/shot-galaxy.cjs <baseUrl> <outDir>
// 新聞服務不用在線：/api/news/galaxy 用這裡湊的假快照（12 個主題、400 則、40 個亮點），其他 /api/* 回空的。
const { chromium } = require('playwright')
const path = require('node:path')

function fakeGalaxy() {
  let s = 7
  const rnd = () => ((s = (s * 48271) % 2147483647) / 2147483647)
  const hubs = [
    ['tw:2330', '台積電', 'tw', '2330'], ['tw:2454', '聯發科', 'tw', '2454'], ['tw:2317', '鴻海', 'tw', '2317'], ['tw:3231', '緯創', 'tw', '3231'],
    ['tw:2382', '廣達', 'tw', '2382'], ['tw:2303', '聯電', 'tw', '2303'], ['tw:3017', '奇鋐', 'tw', '3017'], ['tw:2408', '南亞科', 'tw', '2408'],
    ['tw:3711', '日月光投控', 'tw', '3711'], ['tw:2881', '富邦金', 'tw', '2881'], ['tw:2603', '長榮', 'tw', '2603'], ['tw:1101', '台泥', 'tw', '1101'],
    ['us:NVDA', '輝達', 'us', null], ['us:AAPL', '蘋果', 'us', null], ['us:AMD', '超微', 'us', null], ['us:TSLA', '特斯拉', 'us', null],
    ['co:samsung', '三星', 'company', null], ['co:openai', 'OpenAI', 'company', null], ['co:micron', '美光', 'company', null],
    ['th:ai', 'AI', 'theme', null], ['th:memory', '記憶體', 'theme', null], ['th:server', 'AI伺服器', 'theme', null], ['th:yield', '美債殖利率', 'theme', null],
    ['th:fed', '聯準會', 'theme', null], ['th:tariff', '關稅', 'theme', null], ['th:oil', '油價', 'theme', null],
    ['grp:foundry', '晶圓代工', 'group', null], ['grp:cooling', '散熱', 'group', null], ['grp:shipping', '航運', 'group', null], ['grp:finance', '金融', 'group', null],
  ].map(([id, label, type, sub]) => ({ id, label, type, sub, n: 0, x: 0, y: 0, z: 0 }))
  // 每個主題群常提到哪些亮點（前面的機率高）
  const topics = [
    ['台積電先進製程', ['tw:2330', 'grp:foundry', 'us:NVDA', 'th:ai', 'tw:3711']],
    ['AI伺服器出貨', ['th:server', 'tw:2382', 'tw:3231', 'us:NVDA', 'tw:2317', 'th:ai']],
    ['記憶體漲價', ['th:memory', 'tw:2408', 'co:micron', 'co:samsung', 'th:ai']],
    ['輝達財報', ['us:NVDA', 'th:ai', 'tw:2330', 'us:AMD', 'co:openai']],
    ['散熱族群', ['grp:cooling', 'tw:3017', 'th:server', 'us:NVDA']],
    ['聯發科手機晶片', ['tw:2454', 'tw:2330', 'co:samsung', 'us:AAPL']],
    ['美債與聯準會', ['th:yield', 'th:fed', 'grp:finance', 'tw:2881']],
    ['關稅談判', ['th:tariff', 'tw:2317', 'us:AAPL', 'us:TSLA']],
    ['航運運價', ['grp:shipping', 'tw:2603', 'th:oil']],
    ['金融股配息', ['grp:finance', 'tw:2881']],
    ['水泥與原物料', ['tw:1101', 'th:oil']],
    ['成熟製程', ['tw:2303', 'grp:foundry', 'co:samsung']],
  ]
  const clusters = topics.map(([name], k) => {
    const th = (k / topics.length) * Math.PI * 2, ph = rnd() * Math.PI
    return { k, name, n: 0, x: 0.7 * Math.cos(th) * Math.sin(ph), y: 0.7 * Math.sin(th) * Math.sin(ph), z: 0.7 * Math.cos(ph), top: [] }
  })
  const srcs = ['中央社', '經濟日報', '鉅亨網', 'Reuters', '自由財經', 'Bloomberg']
  const items = []
  for (let i = 0; i < 400; i++) {
    const k = Math.floor(rnd() * topics.length), c = clusters[k], pool = topics[k][1]
    const m = []
    for (const id of pool) if (rnd() < (m.length === 0 ? 0.9 : 0.45)) m.push(id)
    const src = srcs[Math.floor(rnd() * srcs.length)], intl = src === 'Reuters' || src === 'Bloomberg'
    const t = new Date(Date.parse('2026-10-22T10:00:00Z') - Math.floor(rnd() * 7 * 86400) * 1000).toISOString()
    items.push({ id: `n${i}`, x: c.x + (rnd() - 0.5) * 0.4, y: c.y + (rnd() - 0.5) * 0.4, z: c.z + (rnd() - 0.5) * 0.4, k, title: `${topics[k][0]}：第 ${i} 則新聞的標題`, src, kind: intl ? 'intl' : 'tw', tr: intl, t, url: `https://example.test/${i}`, ev: rnd() < 0.3 ? `e${k}-${i % 7}` : null, m, ml: m.map((id) => hubs.find((h) => h.id === id).label) })
    c.n++
  }
  for (const h of hubs) {
    const its = items.filter((it) => it.m.includes(h.id))
    h.n = its.length
    if (its.length) {
      h.x = its.reduce((s, it) => s + it.x, 0) / its.length
      h.y = its.reduce((s, it) => s + it.y, 0) / its.length
      h.z = its.reduce((s, it) => s + it.z, 0) / its.length
    }
  }
  return { built: '2026-10-22T10:30:00Z', days: 7, items, clusters, hubs: hubs.filter((h) => h.n >= 3), links: items.reduce((s, it) => s + it.m.length, 0) }
}

;(async () => {
  const [base, out] = process.argv.slice(2)
  const galaxy = fakeGalaxy()
  const b = await chromium.launch()
  for (const [w, h, m, name] of [[390, 844, true, 'm-galaxy'], [1360, 900, false, 'd-galaxy']]) {
    const ctx = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: m ? 2 : 1, isMobile: m, hasTouch: m })
    const page = await ctx.newPage()
    await page.setExtraHTTPHeaders({ 'cf-access-authenticated-user-email': 'cktalex@gmail.com' })
    await page.route('**/api/**', (r) => r.fulfill({ json: {} }))
    await page.route('**/api/layout', (r) => r.fulfill({ json: { layout: null } }))
    await page.route('**/api/me', (r) => r.fulfill({ json: { email: 'cktalex@gmail.com' } }))
    await page.route('**/api/access', (r) => r.fulfill({ json: { features: [] } }))
    await page.route('**/api/watchlist', (r) => r.fulfill({ json: { max: 20, date: null, items: [] } }))
    await page.route('**/api/trades', (r) => r.fulfill({ json: { today: '2026-10-22', items: [], max: 50 } }))
    await page.route('**/api/news/items**', (r) => r.fulfill({ json: { items: [], next: null } }))
    await page.route('**/api/news/health', (r) => r.fulfill({ json: { now: '2026-10-22T10:30:00Z', last_ok_at: '2026-10-22T10:30:00Z', stale: false, sources: [] } }))
    await page.route('**/api/news/digest', (r) => r.fulfill({ json: { as_of: '2026-10-22T10:30:00Z', base_days: 7, topics: { items: [], everywhere: [] }, multi: [], hot: [] } }))
    await page.route('**/api/news/galaxy', (r) => r.fulfill({ json: galaxy }))
    await page.goto(`${base}/?tab=news&view=galaxy`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(1500)
    const btn = page.getByRole('button', { name: '同意並進入' })
    if ((await btn.count()) > 0) {
      await page.locator('input[type="checkbox"]').first().check()
      await btn.click()
    }
    await page.waitForSelector('.gx-search input', { timeout: 15000 })
    await page.waitForTimeout(2500)
    const stage = page.locator('.gx-stage')
    await stage.screenshot({ path: path.join(out, `${name}-0.png`) })
    const input = page.locator('.gx-search input')
    await input.click()
    await input.fill('台積')
    await page.waitForTimeout(300)
    await stage.screenshot({ path: path.join(out, `${name}-1-typing.png`) })
    await input.press('Enter')
    await page.waitForTimeout(2200)
    await stage.screenshot({ path: path.join(out, `${name}-2-web.png`) })
    await input.fill('聯發科 記憶體')
    await page.waitForTimeout(300)
    await stage.screenshot({ path: path.join(out, `${name}-3-two.png`) })
    await input.press('Enter')
    await page.waitForTimeout(2200)
    await stage.screenshot({ path: path.join(out, `${name}-4-path.png`) })
    await input.fill('台泥 輝達')
    await input.press('Enter')
    await page.waitForTimeout(600)
    await stage.screenshot({ path: path.join(out, `${name}-5-nopath.png`) })
    await ctx.close()
  }
  await b.close()
  console.log('galaxy ok')
})().catch((e) => {
  console.error(e)
  process.exit(1)
})
