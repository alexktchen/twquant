import assert from 'node:assert/strict'
import test from 'node:test'
import { lineOk, rawJson } from '../functions/_lib/scorecard.ts'
import { filterRows, md, navLines, ratio, statusText, wan, type NavPoint, type TradeRow } from '../src/cloud/scorecard.ts'

const nav: NavPoint[] = [
  { d: '2026-10-06', v: 100, b: 200, x: 0 },
  { d: '2026-10-07', v: 110, b: 210, x: 1 },
  { d: '2026-10-08', v: 99, b: 220, x: 1 },
  { d: '2026-10-09', v: 121, b: null, x: 1 },
]

test('D-176 AC10: 淨值曲線換成累積報酬 %，事後補與即時在即時第一天接起來；頁面不畫 0050', () => {
  const l = navLines(nav, '2026-10-08')
  assert.deepEqual(
    l.backfill.map((p) => p.time),
    ['2026-10-06', '2026-10-07', '2026-10-08'],
  )
  assert.deepEqual(
    l.live.map((p) => p.time),
    ['2026-10-08', '2026-10-09'],
  )
  assert.ok(Math.abs(l.live[1].value - 21) < 1e-9) // 121 / 100 − 1
  assert.ok(Math.abs(l.backfill[2].value - -1) < 1e-9)
  assert.deepEqual(Object.keys(l).sort(), ['backfill', 'live']) // D-176 修訂：不放 0050
  // 沒有即時段：全部是事後補
  const all = navLines(nav, null)
  assert.equal(all.backfill.length, 4)
  assert.equal(all.live.length, 0)
  assert.deepEqual(navLines([], null), { backfill: [], live: [] })
})

const row = (over: Partial<TradeRow>): TradeRow => ({
  signal: '2026-10-08',
  sid: '2330',
  name: '台積電',
  status: 'closed',
  entry_date: '2026-10-09',
  entry_price: 100,
  exit_date: '2026-11-07',
  exit_price: 110,
  held: 20,
  ret: 0.09,
  reason: 'time_stop',
  live: true,
  ...over,
})

test('D-176 AC10: 逐筆狀態的白話與篩選', () => {
  assert.equal(statusText(row({})), '已賣')
  assert.equal(statusText(row({ reason: 'price_jump' })), '已賣（價格異常）')
  assert.equal(statusText(row({ status: 'open' })), '持有中')
  assert.equal(statusText(row({ status: 'pending', reason: null })), '明天開盤買')
  assert.equal(statusText(row({ status: 'unfilled', reason: 'limit_up' })), '買不到（開盤漲停）')
  assert.equal(statusText(row({ status: 'unfilled', reason: 'weird' })), '買不到（weird）')
  const rows = [row({ sid: 'A', live: true }), row({ sid: 'B', live: false })]
  assert.deepEqual(
    filterRows(rows, 'live').map((r) => r.sid),
    ['A'],
  )
  assert.deepEqual(
    filterRows(rows, 'backfill').map((r) => r.sid),
    ['B'],
  )
  assert.equal(filterRows(rows, 'all').length, 2)
})

test('D-176 AC10: 日期、比例、金額格式', () => {
  assert.equal(md('2026-05-04'), '5/4')
  assert.equal(md(null), '—')
  assert.equal(ratio(0.456), '46%')
  assert.equal(ratio(null), '—')
  assert.equal(wan(10_234_567), '1,023 萬')
})

test('D-176 AC10: API 只收登記過的線別，回應直接用資料庫的 JSON 文字', async () => {
  assert.ok(lineOk('CORE') && lineOk('QUIET') && lineOk('QUIETV'))
  assert.ok(!lineOk(null) && !lineOk('core') && !lineOk("CORE';--") && !lineOk(''))
  const a = rawJson('{"code":"CORE"}')
  assert.equal(a.headers.get('content-type'), 'application/json; charset=utf-8')
  assert.equal(a.headers.get('cache-control'), 'private, no-store')
  assert.deepEqual(await a.json(), { code: 'CORE' })
  assert.deepEqual(await rawJson('[{"sid":"2330"}]', 'rows').json(), { rows: [{ sid: '2330' }] })
})
