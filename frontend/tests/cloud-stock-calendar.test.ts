import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { STOCK_CAL_FUTURE_DAYS, STOCK_CAL_PAST_DAYS, stockCalendar } from '../functions/_lib/stock-calendar.ts'
import { calDate, calDetail, daysAway, splitByToday } from '../src/cloud/stockcal.ts'

const div = (date: string, kind: string, cash: number | null, stock: number | null = 0) => ({ date, kind, cash_dividend: cash, stock_ratio: stock, rights_ratio: 0 })

test('使用者 2026-10-17：個股行事曆——預告、法說會、已發生的合成一串；同一天預告優先（有金額）；新到舊', () => {
  const items = stockCalendar(
    [div('2026-10-28', 'both', 0.5, 0.1), div('2026-07-02', 'cash', 11.5)],
    [{ date: '2026-11-12', conf_time: '14:00', location: '台北', summary: '公布第三季財報' }],
    [
      { date: '2026-07-02', event_type: 'cash_dividend' }, // 同一天有預告 → 不重複
      { date: '2025-06-10', event_type: 'cash_dividend' },
      { date: '2025-09-01', event_type: 'reverse_split' },
      { date: '2025-08-01', event_type: '看不懂的' }, // 不認得 → 不列
    ],
  )
  assert.deepEqual(
    items.map((x) => `${x.date} ${x.kind}`),
    ['2026-11-12 conf', '2026-10-28 both', '2026-07-02 cash', '2025-09-01 reduce', '2025-06-10 cash'],
  )
  assert.equal(items[0].summary, '台北；公布第三季財報')
  assert.equal(items[0].time, '14:00')
  assert.equal(items[2].cash_dividend, 11.5, '同一天用預告那筆（有金額）')
  assert.equal(items[4].cash_dividend, null, '已發生的只有日期與種類')
  assert.deepEqual(stockCalendar([], [], []), [])
  assert.ok(STOCK_CAL_PAST_DAYS >= 365 && STOCK_CAL_FUTURE_DAYS >= 90)
})

test('使用者 2026-10-17：個股行事曆——今天以後由近到遠、以前由新到舊；日期跨年加年份；說明與倒數', () => {
  const items = stockCalendar(
    [div('2026-10-28', 'cash', 1.2), div('2026-10-17', 'stock', 0, 0.05)],
    [{ date: '2026-11-12', conf_time: '14:00', location: null, summary: null }],
    [{ date: '2025-06-10', event_type: 'cash_dividend' }, { date: '2026-07-02', event_type: 'split' }],
  )
  const { upcoming, past } = splitByToday(items, '2026-10-17')
  assert.deepEqual(upcoming.map((x) => x.date), ['2026-10-17', '2026-10-28', '2026-11-12'])
  assert.deepEqual(past.map((x) => x.date), ['2026-07-02', '2025-06-10'])
  assert.equal(calDate('2026-10-28', '2026-10-17'), '10/28（三）')
  assert.equal(calDate('2025-06-10', '2026-10-17'), '2025/6/10（二）')
  assert.equal(calDetail(upcoming[1]), '現金 1.2 元')
  assert.equal(calDetail(upcoming[2]), '14:00')
  assert.equal(calDetail(past[0]), '', '分割沒有金額')
  assert.equal(daysAway('2026-10-17', '2026-10-17'), '今天')
  assert.equal(daysAway('2026-10-18', '2026-10-17'), '明天')
  assert.equal(daysAway('2026-10-28', '2026-10-17'), '11 天後')
  assert.equal(daysAway('2026-10-01', '2026-10-17'), '')
})

test('使用者 2026-10-17：個股行事曆端點驗代號、三個來源都容許表還沒建；個股頁有放', () => {
  const api = readFileSync(new URL('../functions/api/stocks/[sid]/calendar.ts', import.meta.url), 'utf8')
  assert.ok(api.includes('SID_RE.test(sid)') && api.includes('fail(400'))
  assert.equal(api.match(/readRows\(/g)?.length, 3)
  for (const t of ['serving.dividend_calendar', 'serving.conference_calendar', 'serving.corporate_events']) assert.ok(api.includes(t), t)
  const app = readFileSync(new URL('../src/cloud/CloudApp.tsx', import.meta.url), 'utf8')
  assert.ok(app.includes('<StockCalendar sid={sid} refresh={refresh} />'))
})
