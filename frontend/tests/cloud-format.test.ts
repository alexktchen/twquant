import assert from 'node:assert/strict'
import test from 'node:test'
import { DEFAULT_VISIBLE_BARS, chooseDay, dateOption, dayCount, dayNav, latestRange, parseKeptBars, pct, pickMarkers, price, quoteBar, signedPrice, sma, trend, type Bar } from '../src/cloud/format.ts'

const bar = (date: string, close: number): Bar => ({ date, open: close, high: close, low: close, close, volume: 1 })

test('T-332 AC8: 漲紅跌綠與格式', () => {
  assert.equal(trend(0.01), 'up')
  assert.equal(trend(-0.01), 'down')
  assert.equal(trend(0), 'flat')
  assert.equal(trend(null), 'flat')
  assert.equal(pct(0.0123), '+1.23%')
  assert.equal(pct(-0.05), '-5.00%')
  assert.equal(pct(null), '—')
  assert.equal(pct(0.2205, 1), '+22.1%')
  assert.equal(price(1085), '1085')
  assert.equal(price(123.45), '123.5')
  assert.equal(price(45.678), '45.68')
  assert.equal(price(null), '—')
})

test('T-332 AC8: 均線前 n−1 根不補值', () => {
  const bars = [10, 20, 30, 40].map((c, i) => bar(`2026-10-0${i + 1}`, c))
  assert.deepEqual(sma(bars, 3), [
    { time: '2026-10-03', value: 20 },
    { time: '2026-10-04', value: 30 },
  ])
})

test('T-332 AC8: 推薦標記只標在 K 線範圍內、依日期排序', () => {
  const bars = [bar('2026-10-07', 1), bar('2026-10-08', 1)]
  const m = pickMarkers({ QUIET: ['2026-10-08', '2026-01-02'], CORE: ['2026-10-07'] }, { CORE: '大盤精選', QUIET: '潛伏飆股' }, bars)
  assert.deepEqual(m, [
    { time: '2026-10-07', text: '大盤精選', line: 'CORE' },
    { time: '2026-10-08', text: '潛伏飆股', line: 'QUIET' },
  ])
})

test('T-332 報價列：歷史名單顯示名單那天的 K 棒，長按時換成那一根，漲跌對前一根收盤', () => {
  const bars = [bar('2026-09-23', 100), bar('2026-09-24', 110), bar('2026-10-08', 121)]
  assert.equal(quoteBar(bars, '2026-09-24', null)?.bar.date, '2026-09-24')
  assert.equal(quoteBar(bars, '2026-09-24', null)?.chg, 110 / 100 - 1)
  assert.equal(quoteBar(bars, undefined, null)?.bar.date, '2026-10-08')
  assert.equal(quoteBar(bars, '2026-01-01', null)?.bar.date, '2026-10-08', '找不到日期就用最後一根')
  assert.equal(quoteBar(bars, '2026-09-24', 0)?.bar.date, '2026-09-23', '長按優先')
  assert.equal(quoteBar(bars, '2026-09-24', 0)?.chg, null, '第一根沒有前一根')
  assert.equal(quoteBar(bars, '2026-09-24', 9)?.bar.date, '2026-09-24', '超出範圍的長按不算')
  assert.equal(quoteBar([], '2026-09-24', null), null)
})

test('T-332 長按浮框：漲跌點數帶正負號、位數跟股價一樣', () => {
  const bars = [bar('2026-09-23', 100), bar('2026-09-24', 102.5), bar('2026-09-25', 99)]
  assert.equal(quoteBar(bars, undefined, 1)?.diff, 2.5)
  assert.equal(signedPrice(quoteBar(bars, undefined, 1)?.diff ?? null), '+2.50')
  assert.equal(signedPrice(quoteBar(bars, undefined, 2)?.diff ?? null), '-3.50')
  assert.equal(signedPrice(0), '0.00')
  assert.equal(signedPrice(null), '—')
  assert.equal(quoteBar(bars, undefined, 0)?.diff, null)
})

test('T-332 K 線：預設看最近 60 根，指定根數時從最新一根往回算', () => {
  assert.equal(DEFAULT_VISIBLE_BARS, 60)
  assert.deepEqual(latestRange(500, 120), { from: 381, to: 501 })
  assert.deepEqual(latestRange(500, 30), { from: 471, to: 501 })
  assert.deepEqual(latestRange(50, 120), { from: 0, to: 51 }, '資料比要看的少就從第一根開始')
})

test('T-332 K 線：記住的根數讀回來，沒有或壞掉就用預設 60', () => {
  assert.equal(parseKeptBars('45'), 45)
  assert.equal(parseKeptBars(null), 60)
  assert.equal(parseKeptBars(''), 60)
  assert.equal(parseKeptBars('abc'), 60)
  assert.equal(parseKeptBars('12.5'), 60)
  assert.equal(parseKeptBars('2'), 60, '太小')
  assert.equal(parseKeptBars('99999'), 60, '太大')
})

test('日期選單檔數：有記錄的日子寫幾檔（沒股票寫 0 檔），還沒開始記錄或沒給檔數就只寫日期', () => {
  const c = { counts: { '2026-10-08': { CORE: 15, QUIETV: 2 }, '2026-10-07': { CORE: 15 } }, since: { CORE: '2026-05-04', QUIETV: '2026-10-07' } }
  assert.equal(dayCount(c, '2026-10-08', 'QUIETV'), 2)
  assert.equal(dayCount(c, '2026-10-07', 'QUIETV'), 0) // 已開始記錄、那天沒股票
  assert.equal(dayCount(c, '2026-10-06', 'QUIETV'), null) // 那天還沒有這條線
  assert.equal(dayCount(c, '2026-10-08', 'ZZZ'), null)
  assert.equal(dayCount(null, '2026-10-08', 'CORE'), null) // 舊後端或沒登入
  assert.equal(dateOption('2026-10-08', 2), '2026-10-08　2 檔')
  assert.equal(dateOption('2026-10-08', 0), '2026-10-08　0 檔')
  assert.equal(dateOption('2026-10-06', null), '2026-10-06')
  assert.equal(dateOption('2026-10-13', 15, true), '2026-10-13　15 檔・最新')
})

test('每日名單（今日推薦與歷史名單合併）：沒選日期＝最新一天，選到最新那天就跟著新的一天走', () => {
  const dates = ['2026-10-13', '2026-10-08', '2026-10-07'] // 新到舊
  assert.deepEqual(dayNav(dates, undefined), { shown: '2026-10-13', older: '2026-10-08', newer: null, past: false })
  assert.deepEqual(dayNav(dates, '2026-10-08'), { shown: '2026-10-08', older: '2026-10-07', newer: '2026-10-13', past: true })
  assert.deepEqual(dayNav(dates, '2026-10-07'), { shown: '2026-10-07', older: null, newer: '2026-10-08', past: true })
  assert.equal(dayNav(dates, '2026-10-13').past, false) // 明確選最新那天也不算看過去
  assert.deepEqual(dayNav([], undefined), { shown: undefined, older: null, newer: null, past: false }) // 日期清單還沒到
  assert.equal(chooseDay(dates, '2026-10-13'), undefined)
  assert.equal(chooseDay(dates, '2026-10-08'), '2026-10-08')
})

test('重抓回來的 K 線沒變就不重畫：根數與最後一根的日期、量價都一樣才算相同', async () => {
  const { sameBars } = await import('../src/cloud/format.ts')
  const b = (date: string, close: number) => ({ date, open: 1, high: 2, low: 0.5, close, volume: 10 })
  assert.equal(sameBars([b('2026-10-12', 1), b('2026-10-13', 2)], [b('2026-10-12', 1), b('2026-10-13', 2)]), true)
  assert.equal(sameBars([b('2026-10-12', 1)], [b('2026-10-12', 1), b('2026-10-13', 2)]), false) // 資料庫多了今天
  assert.equal(sameBars([b('2026-10-13', 2)], [b('2026-10-13', 2.1)]), false)
  assert.equal(sameBars([], []), true)
})
