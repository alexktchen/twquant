// autotrade A-005（AC10、AC11）：「台指期」分頁的「程式交易」——交易程式單向寫出來的狀態，網站只讀。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { AUTOTRADE_DAYS_LIMIT, envelope, modeOk } from '../functions/_lib/autotrade.ts'
import {
  EVENT_LABEL,
  HALT_TEXT,
  MODE_NAME,
  SLOW_EVERY,
  STALE_AFTER_S,
  assumptionText,
  clock,
  cumPath,
  cumulative,
  dayStats,
  eventText,
  lamp,
  lossRoom,
  positionText,
  refetchDue,
  type AutoStatus,
} from '../src/cloud/autotrade.ts'

const base: AutoStatus = {
  schema: 1,
  day: '2026-11-13',
  mode: 'sim',
  source: 'replay',
  status: 'live',
  updated_at: '2026-11-13T09:00:06+08:00',
  symbol: 'TMF',
  strategy: 'momentum',
  state: 'ok',
  halt: null,
  position: 1,
  avg_price: 27001,
  last_price: 27040,
  last_ts: '2026-11-13T09:00:06+08:00',
  open_pnl_twd: 390,
  realized_twd: -31,
  day_pnl_twd: 359,
  costs_twd: 31,
  entries: 1,
  limits: { max_position: 1, max_entries_per_day: 4, daily_loss_limit_twd: 3000, position_stop_loss_twd: null },
  contract: { point_value: 10, commission_twd: 15, tax_rate: 0.00002 },
  sim: { latency_s: 0.5, slippage_points: 1 },
  control: { listening: false, ok: null, checked_at: null, poll_s: null, last_command_id: 0 },
  fills: [{ ts: '2026-11-13T09:00:01+08:00', side: 'BUY', qty: 1, price: 27001, pnl_twd: -31 }],
  corrected: false,
  events: [],
}
const at = (s: number) => new Date(Date.parse(base.updated_at) + s * 1000)

test('AC10 模式只認 sim 與 live', () => {
  assert.equal(modeOk('sim'), true)
  assert.equal(modeOk('live'), true)
  for (const bad of [null, '', 'paper', "sim' OR 1=1"]) assert.equal(modeOk(bad), false)
  assert.ok(AUTOTRADE_DAYS_LIMIT > 0 && AUTOTRADE_DAYS_LIMIT <= 250)
})

test('AC10 一次回日期清單與那一天的狀態；沒有紀錄是空的不是錯誤', () => {
  const days = [{ day: '2026-11-13', status: 'closed', pnl: 359, fills: 2 }]
  const body = JSON.stringify(base)
  assert.deepEqual(JSON.parse(envelope(days, body)), { days, current: base, stop: null, listeners: {} })
  assert.deepEqual(JSON.parse(envelope([], null)), { days: [], current: null, stop: null, listeners: {} })
})

test('AC10 端點：權限在讀資料庫之前擋、跟小台模擬同一道權限、表還沒建不算錯', () => {
  const src = readFileSync(new URL('../functions/api/autotrade/index.ts', import.meta.url), 'utf8')
  assert.match(src, /denied\(ctx\.request, 'futures', ctx\.env\)\) \?\? handle\(ctx\)/)
  assert.match(src, /readRows\(/)
  assert.match(src, /serving\.autotrade_status/)
  assert.doesNotMatch(src, /INSERT|UPDATE|DELETE/i) // 這支只讀；唯一的寫入（遠端停止）在 ./stop.ts，見 cloud-autotrade-stop.test.ts
  assert.equal(/onRequest(Post|Put|Delete|Patch)/.test(src), false)
})

test('AC11 狀態燈：正常、停手、暫停送單、連線中斷', () => {
  assert.deepEqual(lamp(base, at(5)), { tone: 'ok', label: '運作中', detail: '' })
  const halted = lamp({ ...base, state: 'halted', halt: 'daily_loss' }, at(5))
  assert.equal(halted.tone, 'bad')
  assert.equal(halted.label, '停手')
  assert.equal(halted.detail, HALT_TEXT.daily_loss)
  const sus = lamp({ ...base, state: 'suspended', halt: 'broker_reject' }, at(5))
  assert.deepEqual([sus.tone, sus.label, sus.detail], ['bad', '暫停送單', HALT_TEXT.broker_reject])
  assert.deepEqual(lamp({ ...base, state: 'link_down' }, at(5)).tone, 'warn')
  assert.equal(lamp({ ...base, state: 'link_down' }, at(5)).label, '券商連線中斷')
  // 不認得的原因代碼照原樣顯示，不吞掉
  assert.equal(lamp({ ...base, state: 'halted', halt: 'new_code' }, at(5)).detail, 'new_code')
})

test('AC11 狀態燈：盤中太久沒更新標「可能沒在跑」；已收盤不算', () => {
  assert.equal(lamp(base, at(STALE_AFTER_S)).tone, 'ok')
  const stale = lamp(base, at(STALE_AFTER_S + 1))
  assert.deepEqual([stale.tone, stale.label], ['warn', '可能沒在跑'])
  assert.match(stale.detail, /沒有更新/)
  assert.deepEqual(lamp({ ...base, status: 'closed' }, at(86400)), { tone: 'off', label: '已收盤', detail: '' })
  // 收盤時是停手的日子，停手比「已收盤」重要
  assert.equal(lamp({ ...base, status: 'closed', state: 'halted', halt: 'kill' }, at(86400)).tone, 'bad')
  // 停手比「沒更新」重要；但盤中的狀態太久沒更新時一定要講——畫面上的「停手」可能是程式死掉前的最後一份
  const dead = lamp({ ...base, state: 'halted', halt: 'kill' }, at(9999))
  assert.deepEqual([dead.tone, dead.label], ['bad', '停手'])
  assert.equal(dead.detail, `${HALT_TEXT.kill}；2 小時沒有更新，程式可能沒在跑`)
  assert.match(lamp({ ...base, state: 'suspended', halt: 'order_rate' }, at(600)).detail, /10 分鐘沒有更新，程式可能沒在跑/)
  assert.match(lamp({ ...base, state: 'link_down' }, at(600)).detail, /斷線期間不送單也不刪單；10 分鐘沒有更新/)
  assert.equal(lamp({ ...base, state: 'halted', halt: 'kill' }, at(5)).detail, HALT_TEXT.kill) // 還新的時候不多話
})

test('AC11 離每日虧損上限還有多少', () => {
  assert.equal(lossRoom(359, null), null)
  assert.deepEqual(lossRoom(359, 3000), { used: 0, left: 3000 })
  assert.deepEqual(lossRoom(-750, 3000), { used: 0.25, left: 2250 })
  assert.deepEqual(lossRoom(-3600, 3000), { used: 1, left: 0 })
})

test('AC11 部位、時間、名稱', () => {
  assert.equal(positionText(0), '空手')
  assert.equal(positionText(2), '多 2 口')
  assert.equal(positionText(-1), '空 1 口')
  assert.equal(clock('2026-11-13T09:00:06+08:00'), '09:00:06')
  assert.equal(clock('2026-11-13T13:44:59.250000+08:00'), '13:44:59')
  assert.deepEqual(MODE_NAME, { sim: '模擬', live: '真單' })
  for (const kind of ['halt', 'release', 'stop', 'correction', 'link', 'broker_reject', 'foreign_order', 'reject', 'control', 'control_link'])
    assert.ok(EVENT_LABEL[kind], kind)
})

test('AC11 今日大事的文字不露出內部代碼', () => {
  assert.equal(eventText('部位對不上，停手（mismatch）'), '部位對不上，停手（跟券商對不上）')
  assert.equal(eventText('停手（daily_loss）：當日損益 -3050 元'), '停手（當日虧損到上限）：當日損益 -3050 元')
  assert.equal(eventText('暫停送單（order_rate）'), '暫停送單（送單太頻繁）')
  assert.equal(eventText('緊急停止'), '緊急停止')
  // 不認得的代碼、一般的括號說明都照原樣
  assert.equal(eventText('停手（new_code）'), '停手（new_code）')
  assert.equal(eventText('重連後券商沒有這張委託（不重送）'), '重連後券商沒有這張委託（不重送）')
  for (const code of Object.keys(HALT_TEXT)) assert.doesNotMatch(eventText(`停手（${code}）`), /[a-z_]{3,}/, code)
})

test('AC11 盤中每次都抓；已收盤或沒有紀錄時抓得比較慢', () => {
  for (let t = 1; t <= 8; t++) assert.equal(refetchDue(t, true), true)
  const hits = [1, 2, 3, 4, 5, 6, 7, 8].filter((t) => refetchDue(t, false))
  assert.deepEqual(hits, [SLOW_EVERY, SLOW_EVERY * 2])
})

test('AC11 每天的成績：累計、賺賠天數、最好最差；沒有損益數字的日子不算', () => {
  const days = [
    { day: '2026-11-12', status: 'closed', pnl: 644, fills: 4 },
    { day: '2026-11-11', status: 'closed', pnl: -66, fills: 4 },
    { day: '2026-11-10', status: 'closed', pnl: 0, fills: 0 },
    { day: '2026-11-09', status: 'closed', pnl: null, fills: null },
  ]
  assert.deepEqual(dayStats(days), { n: 3, total: 578, wins: 1, losses: 1, best: 644, worst: -66 })
  assert.deepEqual(dayStats([]), { n: 0, total: 0, wins: 0, losses: 0, best: null, worst: null })
  assert.deepEqual(cumulative(days), [
    { day: '2026-11-10', pnl: 0, cum: 0 },
    { day: '2026-11-11', pnl: -66, cum: -66 },
    { day: '2026-11-12', pnl: 644, cum: 578 },
  ])
  assert.equal(days[0].day, '2026-11-12') // 不改到傳進來的清單
})

test('AC11 累計線：座標落在圖內、0 那條線的位置對、少於兩天不畫', () => {
  assert.equal(cumPath([{ cum: 5 }], 300, 100), null)
  const p = cumPath([{ cum: 0 }, { cum: -100 }, { cum: 300 }], 300, 100)
  assert.deepEqual(p, { line: '0.0,75.0 150.0,100.0 300.0,0.0', zeroY: 75 })
  assert.equal(cumPath([{ cum: 10 }, { cum: 20 }], 300, 100)?.zeroY, 100) // 全部都賺：0 在最底
  assert.equal(cumPath([{ cum: 0 }, { cum: 0 }], 300, 100)?.line, '0.0,100.0 300.0,100.0') // 全 0 不會除以 0
})

test('AC11 損益怎麼算講清楚：模擬假設與成本；真單不講模擬假設', () => {
  const sim = assumptionText(base)
  assert.equal(
    sim,
    '模擬假設：下單後 0.5 秒才成交、每邊滑價 1 點。損益已扣成本：手續費每口每邊 15 元、期交稅十萬分之 2（每點 10 元）。模擬結果不代表真單會一樣。',
  )
  const live = assumptionText({ ...base, sim: null })
  assert.equal(live, '損益已扣成本：手續費每口每邊 15 元、期交稅十萬分之 2（每點 10 元）。')
})

test('AC11 頁面：模擬與真單分得出來、沒有任何寫入的呼叫', () => {
  const view = readFileSync(new URL('../src/cloud/AutotradeView.tsx', import.meta.url), 'utf8')
  assert.match(view, /cq-at-banner cq-at-mode-\$\{s\.mode\}/)
  assert.match(view, /fetchAutotrade/)
  const api = readFileSync(new URL('../src/cloud/api.ts', import.meta.url), 'utf8')
  const line = api.split('\n').find((l) => l.includes('fetchAutotrade'))
  assert.ok(line && !/'(PUT|POST|DELETE)'/.test(line))
  const page = readFileSync(new URL('../src/cloud/FuturesPage.tsx', import.meta.url), 'utf8')
  assert.match(page, /<AutotradeView/)
  assert.match(view, /current\.schema !== SCHEMA/) // 版本不認得就不硬畫
})
