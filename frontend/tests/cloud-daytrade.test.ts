// T-458／D-221：個股當沖分頁（盤中強勢股榜＋網友原版模擬單＋復盤）。
// 使用者 2026-10-28：「那可以開始規劃做一個個股當沖的功能嗎」→「都做」「先用他預設，但是可以調整」「明天上線再抓」。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { DAYTRADE_NOT_READY, NO_HISTORY } from '../functions/_lib/daytrade.ts'
import { can } from '../functions/_lib/roles.ts'
import { lockedText, tabAllowed } from '../src/cloud/access.ts'
import {
  CONFIG_TEXT,
  EXIT_TEXT,
  MARKET_STATE_TEXT,
  boardAt,
  boardTag,
  normalizeDay,
  pct,
  priceAt,
  replayAt,
  roughOpenPnl,
  shortAt,
  shouldPoll,
  type DayTradeDay,
  type DayTradeHistory,
  type ShortSnapshot,
} from '../src/cloud/daytrade.ts'
import { tabFromUrl } from '../src/cloud/tabs.ts'

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')

function day(): DayTradeDay {
  const trade = (symbol: string, name: string, entry: string, exit: string, ep: number, xp: number, net: number) => ({
    symbol, name, market: 'TWSE', strategy: 'TREND', setup: '趨勢・初次進場', entry_time: entry, entry_price: ep, quantity: 2,
    stop_price: ep * 0.98, exit_time: exit, exit_price: xp, exit_reason: 'HIGHER_LOW_BREAK', net_pnl: net, net_ret_pct: net / (ep * 2000) * 100,
    fees: 40, tax: 100, hold_minutes: 10,
  })
  return {
    version: 1,
    day: '2026-10-28',
    status: 'closed',
    source: 'minute_replay',
    updated_at: '2026-10-28T13:40:00+08:00',
    board_time: '13:30:30',
    market: { state: 'BULL', trend: 'UP', score: 70, taiex_pct: 2.04, tpex_pct: 4.06, breadth: 0.4, momentum_allowed: true, trend_allowed: true },
    board: [
      { rank: 1, symbol: '3231', name: '緯創', market: 'TWSE', score: 88, change_pct: 6.1 },
      { rank: 2, symbol: '2382', name: '廣達', market: 'TWSE', score: 81, change_pct: 4.2 },
    ],
    positions: [],
    trades: [trade('3231', '緯創', '09:20:05', '09:31:10', 120, 122, 3600), trade('2382', '廣達', '10:02:00', '10:40:30', 300, 297, -6300)],
    events: [
      { time: '09:20:04', kind: 'signal', symbol: '3231', name: '緯創', text: '趨勢・初次進場訊號' },
      { time: '09:20:05', kind: 'fill', symbol: '3231', name: '緯創', text: '模擬買進 2 張 @ 120' },
      { time: '09:31:10', kind: 'exit', symbol: '3231', name: '緯創', text: '模擬賣出 @ 122' },
      { time: '10:02:00', kind: 'fill', symbol: '2382', name: '廣達', text: '模擬買進 2 張 @ 300' },
      { time: '10:40:30', kind: 'exit', symbol: '2382', name: '廣達', text: '模擬賣出 @ 297' },
    ],
    events_total: 5,
    account: { capital: 1_000_000, realized: -2700, open_pnl: 0, total: -2700, entries: 2, closed: 2 },
    config: { capital: 1_000_000, watch_count: 30, fee_discount: 0.28, min_fee: 20, tax_rate: 0.0015, latency_ms: 500, trading: { max_position_count: 4, max_buy_price: 150 } },
    watch: { in: 40, out: 38 },
  }
}

function history(): DayTradeHistory {
  return {
    version: 1,
    day: '2026-10-28',
    names: { '3231': '緯創', '2382': '廣達', '2330': '台積電' },
    board_history: [
      ['09:00:35', [['2330', 1.2, 75]]],
      ['09:20:05', [['3231', 5.1, 85], ['2330', 1.0, 72]]],
      ['10:00:05', [['2382', 3.0, 80], ['3231', 4.0, 79]]],
    ],
    charts: {
      '3231': { bars: [['09:20', 120, 121, 119.5, 121, 120.5], ['09:25', 121, 122.5, 121, 122, 121.2]], secs: { t0: '09:00:00', s: [1205, 1206, 1800], p: [120, 120.5, 121.5] } },
      '2382': { bars: [['10:02', 300, 301, 299, 300.5, 300.2], ['10:20', 299, 299.5, 298, 298.5, 299.6]], secs: null },
    },
  }
}

test('T-458 AC1 分頁「當沖」：權限同台指期（只給 admin）；?tab=daytrade 認得；導覽列有它', () => {
  assert.equal(tabFromUrl('https://twquant.pages.dev/?tab=daytrade'), 'daytrade')
  assert.ok(tabAllowed('daytrade', ['futures']))
  assert.ok(!tabAllowed('daytrade', []))
  assert.equal(lockedText('daytrade', false), '這個帳號沒有「當沖」的權限')
  const app = read('src/cloud/CloudApp.tsx')
  assert.match(app, /setTab\('daytrade'\)\}>\s*當沖/)
  assert.match(app, /tab === 'daytrade' \? \(\s*<DayTradePage refresh=\{refresh\} \/>/)
})

test('T-458 AC2 三支端點只給 admin、在讀資料庫之前擋、不進 60 秒暫存；日期要驗格式', () => {
  assert.ok(can('cktalex@gmail.com', 'futures') && !can('friend@example.com', 'futures') && !can(null, 'futures'))
  for (const f of ['functions/api/daytrade/index.ts', 'functions/api/daytrade/days.ts', 'functions/api/daytrade/history.ts']) {
    const src = read(f)
    assert.match(src, /export const onRequestGet: PagesFunction<Env> = async \(ctx\) => \(await denied\(ctx\.request, 'futures', ctx\.env\)\) \?\? handle\(ctx\)/, f)
    assert.ok(!src.includes('cached('), f)
  }
  assert.match(read('functions/api/daytrade/index.ts'), /!dayOk\(day\)/)
  assert.match(read('functions/api/daytrade/history.ts'), /day === null \|\| !dayOk\(day\)/)
  assert.ok(DAYTRADE_NOT_READY.includes('stock_live.py --upload') && NO_HISTORY.includes('收盤後'))
})

test('T-458 AC3 復盤：拉到某一刻只算那之前的成交；抱著的用那一刻的價格粗估', () => {
  const d = day()
  const h = history()
  const open = replayAt(d, h, '09:00:00')
  assert.deepEqual([open.closed.length, open.holding.length, open.realized, open.events.length], [0, 0, 0, 0])
  const mid = replayAt(d, h, '09:25:00')
  assert.equal(mid.closed.length, 0)
  assert.deepEqual(mid.holding.map((t) => [t.symbol, t.price]), [['3231', 120.5]]) // 秒級：09:20:06 那筆
  assert.equal(roughOpenPnl(mid.holding[0], mid.holding[0].price), 1000) // (120.5 − 120) × 2 張 × 1000 股
  assert.equal(mid.events.length, 2)
  const after = replayAt(d, h, '10:30:00')
  assert.deepEqual(after.closed.map((t) => t.symbol), ['3231'])
  assert.equal(after.realized, 3600)
  assert.deepEqual(after.holding.map((t) => [t.symbol, t.price]), [['2382', 298.5]]) // 沒秒級：10:30 之前最後一根 K 的收盤
  const end = replayAt(d, h, '23:59:59')
  assert.equal(end.realized, d.account.realized)
  assert.equal(roughOpenPnl({ entry_price: 1, quantity: 1 }, null), null)
})

test('T-458 AC4 那一刻的強勢股榜：最後一份 ≤ 那一刻、照名次、帶名字；開盤前是空的', () => {
  const h = history()
  assert.deepEqual(boardAt(h, '08:59:59'), { time: null, rows: [] })
  const b = boardAt(h, '09:59:59')
  assert.equal(b.time, '09:20:05')
  assert.deepEqual(b.rows.map((r) => [r.rank, r.symbol, r.name, r.change_pct, r.score]), [
    [1, '3231', '緯創', 5.1, 85],
    [2, '2330', '台積電', 1.0, 72],
  ])
  assert.equal(boardAt(h, '13:30:00').rows[0].symbol, '2382')
})

test('T-458 AC5 價格：有秒級用那一秒以前最後一筆，沒有就用那一分鐘之前最後一根 K；沒有圖 → null', () => {
  const h = history()
  assert.equal(priceAt(h, '3231', '09:20:04'), null) // 秒級還沒開始，09:20 那根 K 也還沒收完 → 沒有價格
  assert.equal(priceAt(h, '3231', '09:30:00'), 121.5)
  assert.equal(priceAt(h, '2382', '10:03:00'), 300.5)
  assert.equal(priceAt(h, '2382', '10:01:00'), null)
  assert.equal(priceAt(h, '9999', '10:00:00'), null)
})

test('T-458 AC6 畫面：盤中只在 08:25～13:45 重抓；榜上標持有中／做過；大盤、出場原因、帳戶設定都是白話', () => {
  assert.ok(shouldPoll(new Date('2026-10-29T01:00:00Z'), null)) // 台北 09:00
  assert.ok(!shouldPoll(new Date('2026-10-29T06:00:00Z'), null)) // 台北 14:00
  assert.ok(!shouldPoll(new Date('2026-10-29T01:00:00Z'), '2026-10-28'))
  const d = day()
  assert.equal(boardTag('3231', d), 'traded')
  assert.equal(boardTag('2330', d), null)
  assert.equal(boardTag('3231', { ...d, positions: [{ symbol: '3231', market: 'TWSE', name: '緯創', strategy: 'TREND', entry_time: '09:20:05', entry_price: 120, quantity: 2, stop: 118, last: 121, open_pnl: 1500 }] }), 'holding')
  assert.equal(MARKET_STATE_TEXT.PANIC, '恐慌')
  assert.equal(EXIT_TEXT.HIGHER_LOW_BREAK, '跌破前低')
  for (const k of ['max_position_count', 'max_single_position_amount', 'max_risk_per_trade_pct', 'max_buy_price', 'max_daily_loss_pct']) assert.ok(CONFIG_TEXT[k], k)
  assert.equal(pct(1.234), '+1.23%')
  assert.equal(pct(null), '—')
  const page = read('src/cloud/DayTradePage.tsx')
  assert.match(page, /account\.json/) // 告訴使用者去哪裡改
  assert.match(page, /只做多、只記帳，沒有送任何委託/)
  assert.doesNotMatch(page, /studies\//) // 不出現研究資料夾代號
})

test('T-458 AC7 只上傳榜單（使用者 2026-10-29：「只上傳 30 檔榜單」）：補成空的持股與成交、畫面只顯示大盤與榜單', () => {
  const full = day()
  assert.equal(normalizeDay(full), full) // 整份狀態原封不動
  const only = normalizeDay({
    version: 1, day: '2026-10-29', status: 'live', source: 'fubon_live', updated_at: '2026-10-29T09:15:00+08:00', board_time: '09:15:00',
    market: full.market, board: full.board, watch: { in: 12, out: 3 }, board_only: true,
  })
  assert.equal(only.board_only, true)
  assert.deepEqual([only.positions, only.trades, only.events], [[], [], []])
  assert.equal(boardTag('3231', only), null)
  const page = read('src/cloud/DayTradePage.tsx')
  assert.match(page, /if \(data\.board_only\) return <BoardOnly d=\{data\} \/>/)
  assert.match(page, /if \(d\.board_only\)/)
  assert.match(page, /模擬單（持股、成交、損益）只存在你電腦上/)
  assert.match(read('src/cloud/api.ts'), /\.then\(normalizeDay\)/)
})

// ── T-463：放空三條記帳（使用者 2026-10-30：「把這幾個空策略放上去」；規則登記在研究分支 studies/short_paper） ──
function shortSnap(): ShortSnapshot {
  const t = (book: 'A' | 'B', symbol: string, name: string, entry: string, exit: string, ep: number, xp: number, net: number, hedged: number | null) => ({
    book, symbol, name, quantity: 2, signal_time: entry, entry_time: entry, entry_price: ep, exit_time: exit, exit_price: xp,
    exit_reason: 'TIME' as const, stop: ep * 1.02, gross_pnl: (ep - xp) * 2000, net_pnl: net, net_ret_pct: net / (ep * 2000) * 100,
    hedge_pnl: hedged === null ? null : hedged - net, net_hedged: hedged, limit_up_locked: false,
  })
  return {
    version: 1, registration: 'studies/short_paper/PREREGISTRATION.md', market_ok: true, blocked: { 'A:MARKET_FILTER': 6, 'B:MAX': 1 },
    books: {
      A: { name: '失速空', trades: [t('A', '1101', '台泥', '10:20:30', '10:21:38', 28.05, 28.1, -641, null), t('A', '4989', '榮科', '11:10:04', '13:05:02', 72.9, 71.3, 2795, null)], positions: [], realized: 2154, open_pnl: 0, closed: 2, wins: 1, realized_hedged: null },
      B: { name: '榜上過熱空（對沖）', trades: [t('B', '3498', '陽程', '10:02:02', '13:05:02', 251.5, 240.5, 10302, 6658)], positions: [], realized: 10302, open_pnl: 0, closed: 1, wins: 1, realized_hedged: 6658 },
    },
  }
}

test('T-463 AC1 復盤：放空照那一刻算——還沒回補的算抱著、回補了才算已實現；B 另報含對沖', () => {
  const s = shortSnap()
  const early = shortAt(s, 'A', '10:30:00')
  assert.deepEqual([early.closed.map((x) => x.symbol), early.holding.map((x) => x.symbol), early.realized, early.hedged], [['1101'], [], -641, null])
  const mid = shortAt(s, 'A', '12:00:00')
  assert.deepEqual([mid.closed.length, mid.holding.map((x) => x.symbol)], [1, ['4989']])
  const end = shortAt(s, 'A', '23:59:59')
  assert.equal(end.realized, 2154)
  const b = shortAt(s, 'B', '23:59:59')
  assert.deepEqual([b.realized, b.hedged], [10302, 6658])
  assert.equal(shortAt(s, 'B', '10:00:00').closed.length, 0)
})

test('T-463 AC2 畫面：盤中與復盤都有「放空記帳（對照）」區塊；只有榜單的那份也帶放空摘要；寫明只記帳、已登記、60 天才判定', () => {
  const page = read('src/cloud/DayTradePage.tsx')
  assert.equal((page.match(/<ShortSection s=\{(data|d)\.short\}/g) ?? []).length, 3)
  assert.match(page, /title="放空記帳（對照）" fold/)
  assert.match(page, /label="過熱空（含對沖）"/)
  assert.match(page, /只記帳不下單，和多單的帳戶分開算/)
  assert.match(page, /累積 60 個交易日才判定/)
  const only = normalizeDay({ version: 1, day: '2026-10-31', status: 'live', source: 'fubon_live', updated_at: '2026-10-31T09:15:00+08:00', board_time: '09:15:00', market: day().market, board: [], watch: { in: 0, out: 0 }, board_only: true, short: shortSnap() })
  assert.equal(only.short?.books.A.closed, 2)
})
