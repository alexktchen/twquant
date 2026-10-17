import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { GlobalHub, FRESH_MS, FUGLE_COOLDOWN_MS, MIN_GAP_MS, STALE_MAX_MS, nearAsOf, type Fetchers } from '../functions/_lib/global-live.ts'
import { LEAN, globalLabel, globalMood, latestSession } from '../functions/_lib/mood.ts'
import {
  fugleNearSymbol,
  fugleQuoteUrl,
  fugleTickersUrl,
  parseFugleFut,
  parseTaifex,
  readFugleJson,
  readTaifex,
  taifexBody,
  type TaifexQuotes,
  type TxQuote,
} from '../functions/_lib/taifex.ts'
import { US_INDICES, parseYahooChart, readYahoo, yahooUrl, zoned, type UsIndex } from '../functions/_lib/yahoo.ts'
import { globalTone, globalView, nextDay, pts, txOpen, txWhen, type GlobalMood as WebGlobal, type TxQuote as WebTx } from '../src/cloud/mood.ts'

const fixture = (name: string): unknown => JSON.parse(readFileSync(new URL(`./fixtures/${name}`, import.meta.url), 'utf8'))
const near = (x: number, y: number, tol = 1e-9) => assert.ok(Math.abs(x - y) < tol, `${x} ≠ ${y}`)

const DAY = parseTaifex(fixture('taifex-tx-2026-10-16-day-closed.json'), 'day')
const NIGHT = parseTaifex(fixture('taifex-tx-2026-10-16-night-closed.json'), 'night')

test('T-369 AC1：期交所日盤（實際回應 2026-10-16 收盤後）→ 近月合約與臺指現貨；價差與遠月不算', () => {
  assert.equal(JSON.parse(taifexBody('day')).MarketType, '0')
  assert.equal(JSON.parse(taifexBody('night')).MarketType, '1')
  const q = DAY.near
  assert.ok(q)
  assert.equal(q.symbol, 'TXFJ6')
  assert.equal(q.name, '臺指期106')
  assert.equal(q.session, 'day')
  assert.equal(q.date, '2026-10-16')
  assert.equal(q.time, '13:44:59')
  assert.equal(q.last, 47242)
  assert.equal(q.ref, 46931) // 前一天結算價
  assert.equal(q.settlement, 47284)
  assert.equal(q.volume, 98384)
  assert.equal(q.test, 47110)
  near(q.chg ?? 0, 47242 / 46931 - 1)
  assert.deepEqual(DAY.spot, { date: '2026-10-16', time: '13:33:25', last: 47111.26, prev: 46969.87 })
})

test('T-369 AC2：夜盤——漲跌參考價＝當天日盤結算價、沒有結算價、凌晨成交時間配日盤的日期；現貨只有收盤；還沒成交的合約 last 是 null', () => {
  const q = NIGHT.near
  assert.ok(q)
  assert.equal(q.symbol, 'TXFJ6')
  assert.equal(q.session, 'night')
  assert.equal(q.date, '2026-10-16')
  assert.equal(q.time, '04:59:57')
  assert.equal(q.last, 46140)
  assert.equal(q.ref, 47284)
  assert.equal(DAY.near?.settlement, q.ref)
  assert.equal(q.settlement, null)
  near(q.chg ?? 0, 46140 / 47284 - 1)
  assert.deepEqual(NIGHT.spot, { date: '2026-10-16', time: null, last: null, prev: 47111.26 })
  // 清單裡只剩一個還沒開始交易的月份（實際回應裡的 TXFF7-M 那一列）
  const body = fixture('taifex-tx-2026-10-16-night-closed.json') as { RtData: { QuoteList: { SymbolID: string }[] } }
  const idle = body.RtData.QuoteList.filter((r) => r.SymbolID === 'TXFF7-M')
  const got = parseTaifex({ RtCode: '0', RtMsg: '', RtData: { QuoteList: idle } }, 'night')
  assert.equal(got.spot, null)
  assert.equal(got.near?.last, null)
  assert.equal(got.near?.time, null)
  assert.equal(got.near?.chg, null)
  assert.equal(got.near?.ref, 48635)
})

test('T-369 AC3：期交所回應格式不對就丟錯（RtCode 不是 0、不是 JSON、HTTP 錯誤），不給空殼', async () => {
  assert.throws(() => parseTaifex({ RtCode: '1', RtMsg: '系統維護', RtData: {} }, 'day'), /RtCode 1：系統維護/)
  assert.throws(() => parseTaifex(null, 'day'), /回應異常/)
  await assert.rejects(readTaifex({ ok: true, status: 200, text: async () => '<html>擋</html>' }, 'day'), /不是 JSON/)
  await assert.rejects(readTaifex({ ok: false, status: 503, text: async () => '' }, 'night'), /HTTP 503/)
  // 清單是空的（放假、代號問錯）→ 不丟錯，但兩個都 null
  assert.deepEqual(parseTaifex({ RtCode: '0', RtData: { QuoteList: [] } }, 'day'), { session: 'day', spot: null, near: null })
})

test('T-369 AC4：Yahoo 日線（實際回應 2026-10-16 收盤後）→ 最新一根對前一根，不用 chartPreviousClose（那是 5 天前）', async () => {
  assert.equal(yahooUrl('^GSPC'), 'https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=5d&interval=1d')
  const spx = parseYahooChart(fixture('yahoo-gspc-2026-10-16-closed.json'))
  assert.equal(spx.symbol, '^GSPC')
  assert.equal(spx.name, 'S&P 500')
  assert.equal(spx.date, '2026-10-16')
  near(spx.last, 7659.44, 1e-2)
  near(spx.prev, 7744.06, 1e-2)
  near(spx.chg, 7659.43994140625 / 7744.06005859375 - 1)
  assert.ok(Math.abs(spx.chg - -0.01093) < 2e-5) // Yahoo 自己算的 regularMarketChangePercent −1.093
  assert.equal(spx.open, false) // 16:02 紐約，正常時段已過
  assert.equal(spx.time, '04:42') // 紐約 16:42 收盤後的最後更新 → 台北隔天凌晨
  const sox = parseYahooChart(fixture('yahoo-sox-2026-10-16-closed.json'), '費半')
  assert.equal(sox.name, '費半')
  near(sox.chg, 11137.287109375 / 11581.2099609375 - 1)
  // 收盤 null 的那根跳過；不到兩根丟錯；盤中（最後更新在交易時段內）open 為 true
  const mk = (closes: (number | null)[], at: number, start: number, end: number) => ({
    chart: {
      result: [
        {
          meta: { symbol: '^X', regularMarketTime: at, exchangeTimezoneName: 'America/New_York', currentTradingPeriod: { regular: { start, end } } },
          timestamp: closes.map((_, i) => 1792000000 + i * 86400),
          indicators: { quote: [{ close: closes }] },
        },
      ],
    },
  })
  const live = parseYahooChart(mk([100, 102, null], 1792100000, 1792090000, 1792110000))
  assert.equal(live.open, true)
  near(live.chg, 0.02)
  assert.throws(() => parseYahooChart(mk([100], 0, 0, 0)), /不到兩根/)
  assert.throws(() => parseYahooChart({ chart: { result: null, error: { description: 'No data found' } } }), /No data found/)
  await assert.rejects(readYahoo({ ok: false, status: 429, text: async () => '' }), /HTTP 429/)
  await assert.rejects(readYahoo({ ok: true, status: 200, text: async () => 'nope' }), /不是 JSON/)
  assert.deepEqual(zoned(1792183328, 'Asia/Taipei'), { date: '2026-10-17', time: '04:42' })
  assert.deepEqual(zoned(1792183328, 'America/New_York'), { date: '2026-10-16', time: '16:42' })
  assert.equal(US_INDICES.length, 4)
})

const tx = (session: 'day' | 'night', date: string, last: number | null, ref: number): TxQuote => ({
  session,
  symbol: 'TXFJ6',
  name: '臺指期106',
  date,
  time: last === null ? null : '10:00:00',
  last,
  ref,
  settlement: null,
  volume: null,
  test: null,
  chg: last === null ? null : last / ref - 1,
})
const us = (chgs: number[]): UsIndex[] =>
  US_INDICES.slice(0, chgs.length).map((x, i) => ({ ...x, date: '2026-10-16', time: '04:02', open: false, last: 100 * (1 + chgs[i]), prev: 100, chg: chgs[i] }))

test('T-369 AC5：最新一節——同一天夜盤在日盤後面；開盤前日盤還沒成交就是昨晚的夜盤；期現價差對加權最新（夜盤對收盤）', () => {
  assert.equal(latestSession(DAY.near, NIGHT.near), 'night')
  assert.equal(latestSession(tx('day', '2026-10-19', 47000, 47284), tx('night', '2026-10-16', 46140, 47284)), 'day')
  assert.equal(latestSession(tx('day', '2026-10-19', null, 47284), tx('night', '2026-10-16', 46140, 47284)), 'night')
  assert.equal(latestSession(tx('day', '2026-10-19', null, 47284), tx('night', '2026-10-19', null, 47284)), null)
  assert.equal(latestSession(null, null), null)

  const g = globalMood(DAY, NIGHT, us([-0.01093, -0.0214, -0.0034, -0.0383]))
  assert.equal(g.latest, 'night')
  assert.deepEqual(g.spot, DAY.spot) // 日盤那份有現貨點數就用它
  near(g.gap?.pts ?? 0, 46140 - 47111.26)
  near(g.gap?.pct ?? 0, 46140 / 47111.26 - 1)
  near(g.us_avg ?? 0, (-0.01093 - 0.0214 - 0.0034 - 0.0383) / 4)
  assert.equal(g.label, '夜盤大跌・美股大跌・偏空')
  assert.deepEqual(
    g.us.map((u) => u.name),
    ['S&P 500', '那斯達克', '道瓊', '費半'],
  )

  // 開盤前：日盤那份現貨還沒有點數 → 用夜盤那份的參考價（加權收盤）
  const pre = globalMood({ spot: { date: '2026-10-19', time: null, last: null, prev: 47111.26 }, near: tx('day', '2026-10-19', null, 47284) }, NIGHT, [])
  assert.deepEqual(pre.spot, { date: '2026-10-16', time: null, last: 47111.26, prev: null })
  assert.equal(pre.latest, 'night')
  near(pre.gap?.pts ?? 0, 46140 - 47111.26)
  // 少一個指數就不算平均（也就沒有美股那一票）
  const three = globalMood(DAY, NIGHT, us([-0.03, -0.03, -0.03]))
  assert.equal(three.us_avg, null)
  assert.equal(three.us.length, 3)
  assert.equal(three.label, '夜盤大跌・偏空')
  // 期交所整個沒拿到、只有美股
  const usOnly = globalMood(null, null, us([0.012, 0.011, 0.008, 0.02]))
  assert.equal(usOnly.latest, null)
  assert.equal(usOnly.gap, null)
  assert.equal(usOnly.label, '美股大漲・偏多')
})

test('T-369 AC6：多空標籤門檻——±1% 大漲大跌、±0.5% 算一票，一多一空分歧、都沒方向平淡、都沒資料 null', () => {
  assert.equal(LEAN, 0.005)
  assert.equal(globalLabel({ session: 'night', chg: -0.0242 }, -0.019), '夜盤大跌・美股大跌・偏空')
  assert.equal(globalLabel({ session: 'day', chg: 0.003 }, 0.007), '偏多')
  assert.equal(globalLabel({ session: 'day', chg: 0.012 }, 0.002), '日盤大漲・偏多')
  assert.equal(globalLabel({ session: 'night', chg: 0.008 }, -0.008), '多空分歧')
  assert.equal(globalLabel({ session: 'night', chg: 0.001 }, -0.002), '多空平淡')
  assert.equal(globalLabel({ session: 'night', chg: -0.005 }, null), '偏空') // 剛好在門檻上算
  assert.equal(globalLabel(null, 0.006), '偏多')
  assert.equal(globalLabel(null, -0.01), '美股大跌・偏空')
  assert.equal(globalLabel(null, null), null)
})

/** 假的來源：可以指定哪幾個失敗。 */
function fakeFetchers(log: string[], failing: Set<string> = new Set()): Fetchers {
  return {
    taifex: async (s) => {
      log.push(`taifex:${s}`)
      if (failing.has(`taifex:${s}`)) throw new Error(`期交所 ${s} 擋下`)
      return (s === 'day' ? DAY : NIGHT) as TaifexQuotes
    },
    yahoo: async (symbol, name) => {
      log.push(`yahoo:${symbol}`)
      if (failing.has(`yahoo:${symbol}`)) throw new Error(`Yahoo ${symbol} HTTP 429`)
      return { symbol, name, date: '2026-10-16', time: '04:02', open: false, last: 99, prev: 100, chg: -0.01 }
    },
  }
}

test('T-369 AC7：共用抓取——整輪六個請求、兩輪至少隔 15 秒；哪個來源沒拿到就留它上一份並標 stale；全部都沒有才丟錯', async () => {
  let t = 1_000_000
  const log: string[] = []
  const failing = new Set<string>()
  const hub = new GlobalHub(fakeFetchers(log, failing), () => t)
  const first = await hub.get()
  assert.equal(log.length, 6)
  assert.equal(first.stale, false)
  assert.deepEqual(first.errors, [])
  assert.equal(first.global.latest, 'night')
  assert.equal(first.global.us.length, 4)
  // 15 秒內再問：不重抓
  t += MIN_GAP_MS - 1
  await hub.get()
  assert.equal(log.length, 6)
  // 到時間重抓：期交所夜盤與一個指數失敗 → 用上一份、stale、錯誤列出來
  t += 1
  failing.add('taifex:night').add('yahoo:^SOX')
  const second = await hub.get()
  assert.equal(log.length, 12)
  assert.equal(second.stale, false) // 上一份才 15 秒前，還算新
  assert.deepEqual(second.errors, ['期交所 night 擋下', 'Yahoo ^SOX HTTP 429'])
  assert.equal(second.global.night?.last, 46140)
  assert.equal(second.global.us.length, 4)
  // 一直失敗到超過 FRESH_MS → stale
  t += FRESH_MS
  const third = await hub.get()
  assert.equal(third.stale, true)
  assert.equal(third.global.night?.last, 46140)
  // 全部失敗、而且上一份都太舊 → 丟第一個錯
  for (const k of ['taifex:day', 'yahoo:^GSPC', 'yahoo:^IXIC', 'yahoo:^DJI']) failing.add(k)
  t += STALE_MAX_MS
  await assert.rejects(hub.get(), /期交所 day 擋下/)
})

test('T-369 AC8：端點進 60 秒暫存、用共用的 GlobalHub（不另外 new）；證交所的 QuoteHub 不碰期交所（靜態）', () => {
  const read = (p: string) => readFileSync(new URL(`../functions/${p}`, import.meta.url), 'utf8')
  const api = read('api/mood/global.ts')
  assert.ok(api.includes('cached(cacheKey('))
  assert.ok(api.includes("from '../../_lib/global-live'"))
  assert.ok(!api.includes('new GlobalHub'))
  const live = read('_lib/global-live.ts')
  assert.ok(!live.includes('QuoteHub'))
  assert.ok(live.includes('Promise.allSettled'))
  assert.ok(read('_lib/mood.ts').includes('export function globalMood'))
})

// ── 網站卡片（src/cloud/mood.ts） ──────────────────────────────────────────

const webTx = (session: 'day' | 'night', date: string, last: number | null, ref: number, test: number | null = null): WebTx => ({
  ...tx(session, date, last, ref),
  test,
})

test('T-369 AC9：卡片——日盤 8:45～13:45、夜盤 15:00～隔天 5:00 算盤中，其他「M/D 收」，沒成交「尚未成交」', () => {
  assert.equal(nextDay('2026-10-16'), '2026-10-17')
  assert.equal(nextDay('2026-12-31'), '2027-01-01')
  const day = webTx('day', '2026-10-16', 47242, 46931)
  const night = webTx('night', '2026-10-16', 46140, 47284)
  assert.equal(txOpen(day, { date: '2026-10-16', time: '10:32:00' }), true)
  assert.equal(txOpen(day, { date: '2026-10-16', time: '13:45:00' }), false)
  assert.equal(txOpen(day, { date: '2026-10-16', time: '08:44:59' }), false)
  assert.equal(txOpen(night, { date: '2026-10-16', time: '20:00:00' }), true)
  assert.equal(txOpen(night, { date: '2026-10-17', time: '03:00:00' }), true)
  assert.equal(txOpen(night, { date: '2026-10-17', time: '05:00:00' }), false)
  assert.equal(txOpen(night, { date: '2026-10-16', time: '14:59:00' }), false)
  assert.equal(txWhen(day, { date: '2026-10-16', time: '10:32:00' }), '盤中 10:00')
  assert.equal(txWhen(day, { date: '2026-10-17', time: '10:32:00' }), '10/16 收')
  assert.equal(txWhen(webTx('day', '2026-10-19', null, 47284, 47110), { date: '2026-10-19', time: '08:40:00' }), '尚未成交')
  assert.equal(pts(47242.5), '47,243')
  assert.equal(pts(-971.26), '-971')
})

test('T-369 AC10：卡片——標籤顏色、兩節與價差、美股時間；沒有任何資料不畫', () => {
  assert.equal(globalTone('夜盤大跌・美股大跌・偏空'), 'bear')
  assert.equal(globalTone('偏多'), 'bull')
  assert.equal(globalTone('多空分歧'), 'flat')
  assert.equal(globalTone(null), 'flat')
  const g: WebGlobal = {
    spot: { date: '2026-10-16', time: '13:33:25', last: 47111.26, prev: 46969.87 },
    day: webTx('day', '2026-10-16', 47242, 46931),
    night: webTx('night', '2026-10-16', 46140, 47284),
    latest: 'night',
    gap: { pts: 46140 - 47111.26, pct: 46140 / 47111.26 - 1 },
    us: [{ symbol: '^GSPC', name: 'S&P 500', date: '2026-10-16', time: '04:02', open: false, last: 7659.44, prev: 7744.06, chg: -0.0109 }],
    us_avg: null,
    label: '夜盤大跌・偏空',
  }
  const now = { date: '2026-10-17', time: '09:00:00' }
  const v = globalView(g, now)
  assert.ok(v)
  assert.equal(v.tone, 'bear')
  assert.deepEqual(
    v.tx.map((q) => [q.name, q.when]),
    [
      ['日盤', '10/16 收'],
      ['夜盤', '10/16 收'],
    ],
  )
  assert.equal(v.gap?.session, 'night')
  near(v.gap?.pts ?? 0, -971.26)
  assert.equal(v.usWhen, '10/16 收')
  assert.equal(globalView({ ...g, us: [{ ...g.us[0], open: true }] }, now)?.usWhen, '盤中')
  assert.equal(globalView(null, now), null)
  assert.equal(globalView({ ...g, day: null, night: null, us: [], gap: null, latest: null, label: null }, now), null)
  // 只有美股（期交所整個沒拿到）照樣畫
  const usOnly = globalView({ ...g, day: null, night: null, gap: null, latest: null }, now)
  assert.equal(usOnly?.tx.length, 0)
  assert.equal(usOnly?.us.length, 1)
})

// ── 富果備援（T-370／D-186 修訂）：正式站上線第一天台指期整行沒出來，期交所拿不到時改問富果 ─────────────
// 富果的回應形狀照官方 SDK @fugle/marketdata 1.8.0 的型別（futopt/intraday/quote、tickers）；沙盒沒有金鑰，沒有實際回應可錄。

/** 微秒時間戳：台北某日某時。 */
const usAt = (date: string, time: string) => (Date.parse(`${date}T${time}+08:00`) * 1000)

test('T-370 AC1：富果近月——TXF 一般月份裡最後交易日還沒過、最早到期的；價差與別的商品不算；網址', () => {
  const body = {
    type: 'FUTURE',
    data: [
      { symbol: 'TXFL6', name: '臺指期126', endDate: '2026-12-16', settlementDate: '2026-12-16' },
      { symbol: 'TXFK6', name: '臺指期116', endDate: '2026-11-18', settlementDate: '2026-11-18' },
      { symbol: 'TXFJ6', name: '臺指期106', endDate: '2026-10-21', settlementDate: '2026-10-21' },
      { symbol: 'TXFJ6/K6', name: '價差', endDate: '2026-10-21' },
      { symbol: 'MXFJ6', name: '小型臺指', endDate: '2026-10-21' },
    ],
  }
  assert.equal(fugleNearSymbol(body, '2026-10-17'), 'TXFJ6')
  assert.equal(fugleNearSymbol(body, '2026-10-21'), 'TXFJ6') // 結算日當天日盤還在交易
  assert.equal(fugleNearSymbol(body, '2026-10-22'), 'TXFK6')
  assert.throws(() => fugleNearSymbol({ data: [] }, '2026-10-17'), /找不到/)
  assert.throws(() => fugleNearSymbol({ message: 'Unauthorized' }, '2026-10-17'), /沒有 data/)
  assert.ok(fugleTickersUrl('day').endsWith('product=TXF&isSpread=false&session=REGULAR'))
  assert.ok(fugleTickersUrl('night').endsWith('session=AFTERHOURS'))
  assert.equal(fugleQuoteUrl('TXFJ6', 'day'), 'https://api.fugle.tw/marketdata/v1.0/futopt/intraday/quote/TXFJ6')
  assert.equal(fugleQuoteUrl('TXFJ6', 'night'), 'https://api.fugle.tw/marketdata/v1.0/futopt/intraday/quote/TXFJ6?session=afterhours')
  // 結算日晚上的夜盤：到期的那個已經不交易，用隔天找
  assert.equal(nearAsOf('night', Date.parse('2026-10-21T20:00:00+08:00')), '2026-10-22')
  assert.equal(nearAsOf('night', Date.parse('2026-10-22T01:00:00+08:00')), '2026-10-22')
  assert.equal(nearAsOf('day', Date.parse('2026-10-21T10:00:00+08:00')), '2026-10-21')
})

test('T-370 AC2：富果行情 → 跟期交所同形狀；參考價＝成交價−漲跌；夜盤凌晨的成交算前一天；試撮不當成交', async () => {
  const day = parseFugleFut(
    { date: '2026-10-16', symbol: 'TXFJ6', lastPrice: 47242, closePrice: 47242, change: 311, changePercent: 0.66, total: { tradeVolume: 98384 }, lastTrade: { price: 47242, time: usAt('2026-10-16', '13:44:59') } },
    'day',
  )
  assert.equal(day.source, 'fugle')
  assert.equal(day.symbol, 'TXFJ6')
  assert.equal(day.date, '2026-10-16')
  assert.equal(day.time, '13:44:59')
  assert.equal(day.last, 47242)
  assert.equal(day.ref, 46931)
  assert.equal(day.volume, 98384)
  near(day.chg ?? 0, 47242 / 46931 - 1)
  // 夜盤 04:59 成交（富果的 date 不管標哪天）→ 期交所的標法：接在 10/16 日盤後面那一節
  const night = parseFugleFut({ date: '2026-10-19', symbol: 'TXFJ6', lastPrice: 46140, change: -1144, lastTrade: { price: 46140, time: usAt('2026-10-17', '04:59:57') } }, 'night')
  assert.equal(night.date, '2026-10-16')
  assert.equal(night.ref, 47284)
  assert.equal(night.time, '04:59:57')
  // 夜盤晚上 21:00 的成交 → 當天
  assert.equal(parseFugleFut({ date: '2026-10-19', symbol: 'TXFJ6', lastPrice: 46000, change: -100, lastTrade: { time: usAt('2026-10-19', '21:00:00') } }, 'night').date, '2026-10-19')
  // 試撮中：lastPrice 是試撮價 → 不當成交，放在 test
  const trial = parseFugleFut({ date: '2026-10-19', symbol: 'TXFJ6', lastPrice: 47110, change: -174, isTrial: true, lastTrial: { price: 47110 }, lastUpdated: usAt('2026-10-19', '08:44:55') }, 'day')
  assert.equal(trial.last, null)
  assert.equal(trial.test, 47110)
  assert.equal(trial.chg, null)
  assert.equal(trial.time, null)
  assert.equal(trial.date, '2026-10-19')
  assert.throws(() => parseFugleFut({ message: 'Unauthorized' }, 'day'), /回應異常/)
  await assert.rejects(readFugleJson({ ok: false, status: 429, json: async () => ({}) }), (e: Error & { status?: number }) => e.status === 429)
})

test('T-370 AC3：期交所拿不到 → 用富果補（沿用上一份的加權點數）；富果 429 就停一分鐘不問；兩個都沒有 → 寫出原因', async () => {
  let t = 5_000_000
  const log: string[] = []
  let taifexDown = false
  let fugleStatus = 0
  const hub = new GlobalHub(
    {
      taifex: async (s) => {
        log.push(`taifex:${s}`)
        if (taifexDown) throw new Error('期交所沒有回應（等了 6 秒）')
        return (s === 'day' ? DAY : NIGHT) as TaifexQuotes
      },
      yahoo: async (symbol, name) => ({ symbol, name, date: '2026-10-16', time: '04:42', open: false, last: 99, prev: 100, chg: -0.01 }),
    },
    () => t,
  )
  const fugle = async (s: 'day' | 'night'): Promise<TxQuote> => {
    log.push(`fugle:${s}`)
    if (fugleStatus) throw Object.assign(new Error(`富果期貨行情 HTTP ${fugleStatus}`), { status: fugleStatus })
    return { ...(s === 'day' ? DAY.near : NIGHT.near)!, last: 46000, source: 'fugle' }
  }
  // 期交所正常：不問富果
  await hub.get(fugle)
  assert.ok(!log.some((x) => x.startsWith('fugle')))
  // 期交所掛了：兩節都改問富果，現貨沿用上一份
  taifexDown = true
  t += MIN_GAP_MS
  const r = await hub.get(fugle)
  assert.deepEqual(log.filter((x) => x.startsWith('fugle')).sort(), ['fugle:day', 'fugle:night'])
  assert.equal(r.global.night?.source, 'fugle')
  assert.equal(r.global.night?.last, 46000)
  assert.deepEqual(r.global.spot, DAY.spot)
  assert.equal(r.global.tx_error, undefined)
  assert.ok(r.errors.includes('期交所沒有回應（等了 6 秒）'))
  // 富果 429：這一輪用上一份，接下來一分鐘不問富果
  fugleStatus = 429
  t += MIN_GAP_MS
  await hub.get(fugle)
  const asked = log.filter((x) => x.startsWith('fugle')).length
  t += MIN_GAP_MS
  await hub.get(fugle)
  assert.equal(log.filter((x) => x.startsWith('fugle')).length, asked)
  t += FUGLE_COOLDOWN_MS
  await hub.get(fugle)
  assert.ok(log.filter((x) => x.startsWith('fugle')).length > asked)
  // 期交所與富果一直拿不到、上一份也太舊：美股照給，台指期寫原因
  t += STALE_MAX_MS
  const gone = await hub.get(fugle)
  assert.equal(gone.global.day, null)
  assert.equal(gone.global.night, null)
  assert.equal(gone.global.us.length, 4)
  assert.equal(gone.global.tx_error, '期交所沒有回應（等了 6 秒）')
  // 沒有富果金鑰（不給備援）：一樣寫原因
  const bare = new GlobalHub({ taifex: async () => Promise.reject(new Error('期交所行情 HTTP 522')), yahoo: async (symbol, name) => ({ symbol, name, date: '2026-10-16', time: null, open: false, last: 1, prev: 1, chg: 0 }) }, () => t)
  assert.equal((await bare.get()).global.tx_error, '期交所行情 HTTP 522')
})

test('T-370 AC4：網站——兩節都沒有就照樣畫台指期那一行寫原因；富果補的要註明；每個對外請求有逾時、端點把富果金鑰傳進去（靜態）', () => {
  const now = { date: '2026-10-17', time: '10:34:00' }
  const us1 = [{ symbol: '^GSPC', name: 'S&P 500', date: '2026-10-16', time: '04:42', open: false, last: 7659.44, prev: 7744.06, chg: -0.0109 }]
  const base: WebGlobal = { spot: null, day: null, night: null, latest: null, gap: null, us: us1, us_avg: null, label: '美股大跌・偏空' }
  const v = globalView({ ...base, tx_error: '期交所沒有回應（等了 6 秒）' }, now)
  assert.equal(v?.txError, '期交所沒有回應（等了 6 秒）')
  assert.equal(globalView(base, now)?.txError, '期交所沒有回應')
  const withFugle = globalView({ ...base, night: { ...webTx('night', '2026-10-16', 46140, 47284), source: 'fugle' }, latest: 'night' }, now)
  assert.equal(withFugle?.txError, null)
  assert.equal(withFugle?.fugle, true)
  const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')
  const live = read('functions/_lib/global-live.ts')
  assert.equal((live.match(/signal: timeout\(\)/g) ?? []).length, 4) // 期交所、Yahoo、富果 tickers、富果 quote
  assert.ok(read('functions/api/mood/global.ts').includes('fugleFut(env.FUGLE_API_KEY)'))
  assert.ok(read('src/cloud/MoodCard.tsx').includes('g.txError'))
})

test('使用者 2026-10-17：台指期與美股都有漲跌點數，點數格式帶正負號', async () => {
  const { signedPts, usPrice } = await import('../src/cloud/mood.ts')
  const g: WebGlobal = {
    spot: { date: '2026-10-16', time: '13:33:25', last: 47111.26, prev: 46969.87 },
    day: webTx('day', '2026-10-16', 47242, 46931),
    night: webTx('night', '2026-10-16', 46140, 47284),
    latest: 'night',
    gap: { pts: 46140 - 47111.26, pct: 46140 / 47111.26 - 1 },
    us: [{ symbol: '^GSPC', name: 'S&P 500', date: '2026-10-16', time: '04:02', open: false, last: 7659.44, prev: 7744.06, chg: -0.0109 }],
    us_avg: null,
    label: null,
  }
  const v = globalView(g, { date: '2026-10-17', time: '09:00:00' })
  assert.ok(v)
  assert.deepEqual(v.tx.map((q) => q.diff), [311, -1144])
  near(v.us[0].diff, -84.62)
  assert.equal(signedPts(311), '+311')
  assert.equal(signedPts(-1144), '-1,144')
  assert.equal(signedPts(-84.62, 2), '-84.62')
  assert.equal(signedPts(0), '0')
  assert.equal(signedPts(null), '—')
  assert.equal(usPrice(7659.44), '7,659.44')
})
