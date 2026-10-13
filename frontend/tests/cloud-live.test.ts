import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { MIS_BATCH, QUOTE_MAX, misUrls, parseMis, parseSids } from '../functions/_lib/quote.ts'
import { liveBar, liveLabel, rowPrice, shouldPoll, taipeiNow, type Quote } from '../src/cloud/live.ts'
import type { Bar } from '../src/cloud/format.ts'

// 2026-10-13 從證交所 getStockInfo 錄下的兩份回應（不連網）：
// 13:29 收盤前集中撮合中（z 是 "-"；這份當時只錄到 msgArray，尾端 queryTime 沒錄，rtcode／rtmessage 依同一天其他回應補上）；
// 13:35 收盤後（另含三個問錯市場或不存在的頻道，回的是空殼）。
const fixture = (name: string) => JSON.parse(readFileSync(new URL(`./fixtures/${name}`, import.meta.url), 'utf8'))
const AUCTION = fixture('mis-2026-10-13-1329-auction.json')
const CLOSE = fixture('mis-2026-10-13-1335-close.json')

test('T-347 AC1: 收盤後：開高低收、昨收、漲跌停；成交量從「張」換成「股」；空殼頻道略過', () => {
  const qs = parseMis(CLOSE)
  assert.deepEqual(qs.map((q) => q.sid), ['2330', '5340'])
  const [tsmc, otc] = qs
  assert.deepEqual(
    { date: tsmc.date, time: tsmc.time, open: tsmc.open, high: tsmc.high, low: tsmc.low, last: tsmc.last, prev: tsmc.prev, auction: tsmc.auction },
    { date: '2026-10-13', time: '13:30:00', open: 2500, high: 2510, low: 2490, last: 2490, prev: 2540, auction: false },
  )
  assert.equal(tsmc.volume, 34420 * 1000)
  assert.equal(tsmc.limit_up, 2790)
  assert.equal(tsmc.limit_down, 2290)
  assert.equal(otc.last, 92.6)
  assert.equal(otc.volume, 7319 * 1000)
})

test('T-347 AC1: 收盤撮合中證交所不給即時價：用撮合前最後一筆成交，標 auction', () => {
  const [tsmc, otc] = parseMis(AUCTION)
  assert.equal(tsmc.last, 2495)
  assert.equal(tsmc.auction, true)
  assert.equal(tsmc.time, '13:29:00')
  assert.equal(otc.last, 93.7)
})

test('T-347 AC1: 回應異常（例：一次問太多檔回 9999）丟錯，不當成沒有報價', () => {
  assert.throws(() => parseMis({ rtcode: '9999', msgArray: [] }), /rtcode=9999/)
  assert.throws(() => parseMis(null), /異常/)
  // 還沒成交的欄位是 "-"：價格是 null，不補 0
  const [q] = parseMis({ rtcode: '0000', msgArray: [{ c: '2330', d: '20261014', t: '08:59:55', z: '-', o: '-', h: '-', l: '-', y: '2490.0000', v: '-' }] })
  assert.equal(q.last, null)
  assert.equal(q.open, null)
  assert.equal(q.volume, null)
  assert.equal(q.auction, false)
})

test('T-347 AC2: 代號清單檢查；每檔上市、上櫃都問，每 MIS_BATCH 檔一個請求', () => {
  assert.deepEqual(parseSids('2330, 5340,2330'), ['2330', '5340'])
  assert.equal(parseSids(''), null)
  assert.equal(parseSids("2330,x';--"), null)
  assert.equal(parseSids(Array.from({ length: QUOTE_MAX + 1 }, (_, i) => String(1000 + i)).join(',')), null)
  const one = misUrls(['2330'])
  assert.equal(one.length, 1)
  assert.ok(decodeURIComponent(one[0]).includes('ex_ch=tse_2330.tw|otc_2330.tw'))
  const many = misUrls(Array.from({ length: QUOTE_MAX }, (_, i) => String(1000 + i)))
  assert.equal(many.length, Math.ceil(QUOTE_MAX / MIS_BATCH))
})

test('T-347 AC3: 台北時間與星期不受裝置時區影響', () => {
  // 2026-10-13 05:29:59 UTC ＝ 台北週二 13:29:59
  assert.deepEqual(taipeiNow(new Date(Date.UTC(2026, 9, 13, 5, 29, 59))), { date: '2026-10-13', time: '13:29:59', weekday: 2 })
  // UTC 週五 16:30 ＝ 台北週六 00:30
  assert.deepEqual(taipeiNow(new Date(Date.UTC(2026, 9, 16, 16, 30, 0))), { date: '2026-10-17', time: '00:30:00', weekday: 6 })
})

test('T-347 AC3: 只在週一到五 9:00～13:35 每 10 秒抓；開盤後還只拿到前一天的報價＝休市，停', () => {
  const at = (time: string, weekday = 2) => ({ date: '2026-10-13', time, weekday })
  assert.equal(shouldPoll(at('08:59:59'), []), false)
  assert.equal(shouldPoll(at('09:00:00'), []), true)
  assert.equal(shouldPoll(at('13:35:00'), ['2026-10-13']), true)
  assert.equal(shouldPoll(at('13:35:01'), ['2026-10-13']), false)
  assert.equal(shouldPoll(at('10:00:00', 6), []), false)
  assert.equal(shouldPoll(at('09:02:00'), ['2026-10-12']), true) // 剛開盤，證交所可能還沒換日
  assert.equal(shouldPoll(at('09:10:00'), ['2026-10-12']), false)
  assert.equal(shouldPoll(at('09:10:00'), ['2026-10-12', '2026-10-13']), true)
})

const quote = (over: Partial<Quote> = {}): Quote => ({
  sid: '2330',
  date: '2026-10-13',
  time: '10:15:03',
  open: 2500,
  high: 2510,
  low: 2490,
  last: 2495,
  prev: 2540,
  volume: 20_000_000,
  limit_up: 2790,
  limit_down: 2290,
  auction: false,
  ...over,
})
const bar = (date: string, close: number): Bar => ({ date, open: close, high: close, low: close, close, volume: 1 })

test('T-347 AC4: 資料庫還沒有今天才接上今天這根；資料庫有了（傍晚更新）就用資料庫的', () => {
  const bars = [bar('2026-10-08', 2550), bar('2026-10-12', 2540)]
  assert.deepEqual(liveBar(bars, quote(), '2026-10-13'), { date: '2026-10-13', open: 2500, high: 2510, low: 2490, close: 2495, volume: 20_000_000 })
  assert.equal(liveBar([...bars, bar('2026-10-13', 2490)], quote(), '2026-10-13'), null)
  // 報價不是今天（假日拿到上一個交易日）、還沒成交、缺量：都不接
  assert.equal(liveBar(bars, quote({ date: '2026-10-12' }), '2026-10-13'), null)
  assert.equal(liveBar(bars, quote({ last: null }), '2026-10-13'), null)
  assert.equal(liveBar(bars, quote({ volume: null }), '2026-10-13'), null)
  assert.equal(liveBar(bars, undefined, '2026-10-13'), null)
})

test('T-347 AC5: 價格旁的狀態：盤中＋時間、收盤撮合中、今日收盤、尚未成交；不是今天就不標', () => {
  assert.equal(liveLabel(quote(), '2026-10-13'), '盤中 10:15:03')
  assert.equal(liveLabel(quote({ auction: true, time: '13:29:00' }), '2026-10-13'), '收盤撮合中')
  assert.equal(liveLabel(quote({ time: '13:30:00' }), '2026-10-13'), '今日收盤')
  assert.equal(liveLabel(quote({ last: null, time: '08:59:55' }), '2026-10-13'), '尚未成交')
  assert.equal(liveLabel(quote({ date: '2026-10-12' }), '2026-10-13'), null)
})

test('T-347 AC6: 追蹤清單：今天有成交而且比資料庫新才用即時價（漲跌對昨收），否則用資料庫的', () => {
  const db = { date: '2026-10-12', close: 2540, chg: -0.0039 }
  assert.deepEqual(rowPrice(db, quote(), '2026-10-13'), { close: 2495, chg: 2495 / 2540 - 1, tag: '盤中' })
  assert.equal(rowPrice(db, quote({ time: '13:30:00', last: 2490 }), '2026-10-13').tag, '今日收盤')
  assert.deepEqual(rowPrice({ ...db, date: '2026-10-13' }, quote(), '2026-10-13'), { ...{ close: 2540, chg: -0.0039 }, tag: null })
  assert.deepEqual(rowPrice(db, quote({ last: null }), '2026-10-13'), { close: 2540, chg: -0.0039, tag: null })
  assert.deepEqual(rowPrice(db, undefined, '2026-10-13'), { close: 2540, chg: -0.0039, tag: null })
})
