import assert from 'node:assert/strict'
import test from 'node:test'
import { NOT_READY, lineOk, missingTable, rawJson, readRows } from '../functions/_lib/scorecard.ts'
import { extraAt, extraLine, filterRows, md, navLines, ratio, statusCounts, statusText, stockTitle, tradeReason, viewRows, wan, type NavPoint, type TradeRow } from '../src/cloud/scorecard.ts'

const nav: NavPoint[] = [
  { d: '2026-10-06', v: 100, b: 200, x: 0 },
  { d: '2026-10-07', v: 110, b: 210, x: 1 },
  { d: '2026-10-08', v: 99, b: 220, x: 1 },
  { d: '2026-10-09', v: 121, b: null, x: 1 },
]

test('D-176 AC10: 淨值曲線換成累積報酬 %，事後補與即時在即時第一天接起來；0050 從第一天起算（D-176 修訂三）', () => {
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
  // 0050：200 → 210 → 220，第四天沒有值就跳過
  assert.deepEqual(
    l.bench.map((p) => [p.time, Math.round(p.value * 100) / 100]),
    [
      ['2026-10-06', 0],
      ['2026-10-07', 5],
      ['2026-10-08', 10],
    ],
  )
  // 沒有即時段：全部是事後補
  const all = navLines(nav, null)
  assert.equal(all.backfill.length, 4)
  assert.equal(all.live.length, 0)
  assert.deepEqual(navLines([], null), { backfill: [], live: [], bench: [] })
  // 第一天就沒有 0050（雲端還沒補行情）→ 不畫
  assert.deepEqual(navLines(nav.map((p) => ({ ...p, b: null })), null).bench, [])
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

test('逐筆明細的大字是中文股名，查不到名字才用代號', () => {
  assert.equal(stockTitle({ sid: '2330', name: '台積電' }), '台積電')
  assert.equal(stockTitle({ sid: '3661', name: '世芯-KY' }), '世芯-KY')
  assert.equal(stockTitle({ sid: '9999', name: null }), '9999')
  assert.equal(stockTitle({ sid: '9999', name: '  ' }), '9999')
})

test('D-176 AC10: 日期、比例、金額格式', () => {
  assert.equal(md('2026-05-04'), '5/4')
  assert.equal(md(null), '—')
  assert.equal(ratio(0.456), '46%')
  assert.equal(ratio(null), '—')
  assert.equal(wan(10_234_567), '1,023 萬')
})

test('D-176 AC10: API 只收登記過的線別，回應直接用資料庫的 JSON 文字', async () => {
  assert.ok(lineOk('CORE') && lineOk('QUIET') && lineOk('QUIETV') && lineOk('BREAKOUT'))
  assert.ok(!lineOk(null) && !lineOk('core') && !lineOk("CORE';--") && !lineOk(''))
  const a = rawJson('{"code":"CORE"}')
  assert.equal(a.headers.get('content-type'), 'application/json; charset=utf-8')
  assert.equal(a.headers.get('cache-control'), 'private, no-store')
  assert.deepEqual(await a.json(), { code: 'CORE' })
  assert.deepEqual(await rawJson('[{"sid":"2330"}]', 'rows').json(), { rows: [{ sid: '2330' }] })
})

test('新功能上線、第一次排程還沒跑：表不存在當成「還沒算出來」，不把資料庫錯誤丟給使用者', async () => {
  const undefinedTable = Object.assign(new Error('relation "serving.scorecard" does not exist'), { code: '42P01' })
  assert.ok(missingTable(undefinedTable))
  assert.ok(!missingTable(new Error('timeout')) && !missingTable(null) && !missingTable({ code: '28P01' }))
  assert.deepEqual(await readRows(() => Promise.reject(undefinedTable)), [])
  assert.deepEqual(await readRows(() => Promise.resolve([{ body: '{}' }])), [{ body: '{}' }])
  // 其他錯誤（連不上、密碼錯）照樣丟出去，由 guarded 回 503
  await assert.rejects(readRows(() => Promise.reject(Object.assign(new Error('x'), { code: '28P01' }))))
  assert.ok(NOT_READY.includes('還沒算出來') && !NOT_READY.includes('relation'))
})

test('逐筆明細預設只列已賣出、依賣出日新到舊；還沒賣的另外看（以前照公布日排，最上面全是還沒賣的）', () => {
  const rows = [
    row({ sid: 'P', signal: '2026-10-13', status: 'pending', exit_date: null, ret: null }),
    row({ sid: 'O', signal: '2026-10-08', status: 'open', exit_date: null }),
    row({ sid: 'A', signal: '2026-09-01', exit_date: '2026-09-30', live: false }),
    row({ sid: 'B', signal: '2026-09-05', exit_date: '2026-10-06', live: false }),
    row({ sid: 'U', signal: '2026-09-03', status: 'unfilled', exit_date: null, ret: null, live: false }),
  ]
  assert.deepEqual(
    viewRows(rows, 'closed', 'all').map((r) => r.sid),
    ['B', 'A'],
  )
  assert.deepEqual(
    viewRows(rows, 'open', 'all').map((r) => r.sid),
    ['P', 'O'],
  )
  assert.equal(viewRows(rows, 'all', 'all').length, 5)
  assert.deepEqual(
    viewRows(rows, 'closed', 'live').map((r) => r.sid),
    [],
  )
  assert.deepEqual(statusCounts(rows), { closed: 2, open: 2, all: 5 })
})

test('帳戶買賣的原因：補位線的 rebalance 只會是價格異常出場；換股日已持有的是加碼／減碼', () => {
  assert.equal(tradeReason({ side: 'buy', reason: 'entry', move: 'open' }, 'slots'), '空位補進')
  assert.equal(tradeReason({ side: 'buy', reason: 'rebalance', move: 'open' }, 'rebalance'), '換股買進')
  assert.equal(tradeReason({ side: 'sell', reason: 'time_stop', move: 'close' }, 'slots'), '抱滿到期')
  assert.equal(tradeReason({ side: 'sell', reason: 'rebalance', move: 'close' }, 'rebalance'), '換股賣出')
  assert.equal(tradeReason({ side: 'sell', reason: 'rebalance', move: 'close' }, 'slots'), '價格異常出場')
  // 換股日：已持有的股票只是調回平均，不是又買一次
  assert.equal(tradeReason({ side: 'buy', reason: 'rebalance', move: 'add' }, 'rebalance'), '加碼（調回平均）')
  assert.equal(tradeReason({ side: 'sell', reason: 'rebalance', move: 'trim' }, 'rebalance'), '減碼（調回平均）')
})

test('D-176 修訂四：另外的對照（00981A）從帳戶第一天起算累積報酬；第一天沒有值或長度對不上不畫，中間缺的跳過', () => {
  const vals = [50, 55, null, 60]
  assert.deepEqual(
    extraLine(nav, vals).map((p) => [p.time, Math.round(p.value * 1e9) / 1e9]),
    [
      ['2026-10-06', 0],
      ['2026-10-07', 10],
      ['2026-10-09', 20],
    ],
  )
  assert.deepEqual(extraLine(nav, [null, 55, 56, 57]), [])
  assert.deepEqual(extraLine(nav, null), [])
  assert.deepEqual(extraLine(nav, [50, 55]), [])
  assert.ok(Math.abs((extraAt(vals, 3) ?? 0) - 0.2) < 1e-12)
  assert.equal(extraAt(vals, 2), null)
  assert.equal(extraAt(null, 1), null)
})
