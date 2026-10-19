import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  AVG_SESSIONS,
  BATCH,
  HIGH_LOW_WINDOW,
  MIN_GAP_MS,
  SLICES,
  STOCK_PATTERN,
  UNIVERSE_KEY,
  buildSlice,
  parseOhlc,
  parseStatis,
  parseUniverse,
  readJson,
  runOnce,
  sliceFor,
  sliceKey,
  sliceOf,
  stockUrls,
  summarize,
  taipeiClock,
  universeKey,
  type Slice,
  type Universe,
  type UniverseStock,
} from '../functions/_lib/market-live.ts'
import type { Quote } from '../functions/_lib/quote.ts'
import { marketView, scopeTrend, sparkPoints, type LiveMarket, type Market } from '../src/cloud/war.ts'

const config = (path: string) => readFileSync(new URL(`../../configs/${path}`, import.meta.url), 'utf8')

test('T-397：股票範圍與視窗同收盤後的 market_daily（configs）', () => {
  const sys = config('system.yaml')
  const block = sys.slice(sys.indexOf('\nmarket_daily:'))
  assert.equal(Number(/avg_sessions:\s*(\d+)/.exec(block)?.[1]), AVG_SESSIONS)
  assert.equal(Number(/high_low_window:\s*(\d+)/.exec(block)?.[1]), HIGH_LOW_WINDOW)
  assert.equal(/stock_pattern:\s*"([^"]+)"/.exec(config('serving.yaml'))?.[1], STOCK_PATTERN)
})

const stocks = (n: number): UniverseStock[] =>
  Array.from({ length: n }, (_, i) => [String(1101 + i), i % 2 ? 'otc' : 'tse', null, null] as UniverseStock)

test('T-397／D-199：全市場切成 SLICES 份（每份一個請求，CPU 才壓得到 10ms 內），不重複、不漏；每分鐘輪下一份', () => {
  const all = stocks(1942)
  assert.equal(SLICES, 15)
  const parts = Array.from({ length: SLICES }, (_, k) => sliceOf(all, k))
  assert.deepEqual(parts.flat(), all)
  assert.ok(parts.every((p) => p.length <= BATCH), '每份一個請求')
  assert.deepEqual(['09:00', '09:01', '09:14', '09:15'].map((t) => sliceFor(t)), [0, 1, 14, 0])
  assert.deepEqual(['09:00', '09:01', '09:02', '09:03'].map((t) => sliceFor(t, 3)), [0, 1, 2, 0])
})

test('T-397：每檔只問自己的市場；第一個請求順便問加權、櫃買；一次不超過 BATCH 檔', () => {
  const urls = stockUrls(stocks(300))
  assert.equal(urls.length, Math.ceil(300 / BATCH))
  const ch = urls.map((u) => decodeURIComponent(new URL(u).searchParams.get('ex_ch') ?? '').split('|'))
  assert.deepEqual(ch[0].slice(0, 4), ['tse_t00.tw', 'otc_o00.tw', 'tse_1101.tw', 'otc_1102.tw'])
  assert.equal(ch[0].length, BATCH + 2)
  assert.ok(!ch[1].includes('tse_t00.tw'))
  assert.equal(ch.flat().length, 300 + 2)
})

test('T-397：成交統計取「股票」欄；分時走勢一分鐘一點；格式不對就丟錯', () => {
  assert.equal(parseStatis({ rtcode: '0000', detail: { tz: '589835699700', fz: '515501542190' } }), 515501542190)
  assert.throws(() => parseStatis({ rtcode: '9999', rtmessage: '發生錯誤' }), /成交統計/)
  const ohlc = {
    rtcode: '0000',
    ohlcArray: [
      { c: '46443.85', ts: '090100' },
      { c: '46364.41', ts: '090200' },
      { c: '46656.30', ts: '114800' },
      { c: '46657.19', ts: '114850' },
    ],
  }
  assert.deepEqual(parseOhlc(ohlc), [
    ['09:01', 46443.85],
    ['09:02', 46364.41],
    ['11:48', 46657.19],
  ])
  assert.throws(() => parseOhlc({ rtcode: '0000' }), /分時走勢/)
  assert.throws(() => readJson('<html>因為安全性考量</html>', '即時報價'), /防火牆.*即時報價/)
})

const q = (sid: string, last: number | null, prev: number, extra: Partial<Quote> = {}): Quote => ({
  sid,
  date: '2026-10-19',
  time: '11:56:09',
  open: null,
  high: null,
  low: null,
  last,
  prev,
  volume: null,
  limit_up: Math.round(prev * 1.1 * 100) / 100,
  limit_down: Math.round(prev * 0.9 * 100) / 100,
  auction: false,
  ...extra,
})

test('T-397：一份報價 → Slice；沒有加權指數就丟錯', () => {
  const s = buildSlice(1, [q('t00', 46656.3, 47111.26), q('o00', 394.16, 400.26), q('1101', 27, 27.3)], { tse: 1, otc: 2 }, [], 5)
  assert.equal(s.date, '2026-10-19')
  assert.equal(s.time, '11:56:09')
  assert.deepEqual(s.q, [['1101', 27, 27.3, 30.03, 24.57]])
  assert.deepEqual(s.idx, { taiex: 46656.3, taiex_prev: 47111.26, otc: 394.16, otc_prev: 400.26 })
  assert.throws(() => buildSlice(0, [q('1101', 27, 27.3)], { tse: 1, otc: 2 }, [], 5), /加權指數/)
})

const universe: Universe = {
  asof: '2026-10-16',
  amount_avg: 1_000e8,
  stocks: [
    ['1101', 'tse', 30, 25],
    ['1102', 'tse', 40, 35],
    ['2330', 'tse', null, null],
    ['6488', 'otc', 100, 90],
  ],
}

const slice = (k: number, rows: Slice['q'], at: number, date = '2026-10-19', time = '11:56:09'): Slice => ({
  k,
  date,
  time,
  at,
  q: rows,
  idx: { taiex: 46656.3, taiex_prev: 47111.26, otc: 394.16, otc_prev: 400.26 },
  amt: { tse: 400e8, otc: 100e8 },
  trend: [
    ['09:01', 46443.85],
    ['11:56', 46656.3],
  ],
})

test('T-397：三份都是今天才算家數；定義同 market_daily（昨收、漲跌停價、前 20 日最高最低收盤）', () => {
  const s0 = slice(0, [['1101', 31, 28, 30.8, 25.2], ['1102', 34, 38, 41.8, 34.2]], 1, '2026-10-19', '11:55:00')
  const s1 = slice(1, [['2330', 1000, 1000, 1100, 900]], 2)
  const s2 = slice(2, [['6488', null, 95, 104.5, 85.5]], 3)

  const partial = summarize(universe, [s0, s1, null], '2026-10-19')
  assert.ok(partial)
  assert.equal(partial.up, null)
  assert.equal(partial.since, null)
  assert.equal(partial.amount, 500e8)
  assert.equal(partial.amount_ratio, 0.5)
  assert.ok(Math.abs((partial.taiex_chg ?? 0) - (46656.3 / 47111.26 - 1)) < 1e-12)

  const full = summarize(universe, [s0, s1, s2], '2026-10-19')
  assert.ok(full)
  // 1101 漲停（31 ≥ 30.8）且創新高（31 > 30）；1102 跌停（34 ≤ 34.2）且創新低（34 < 35）；2330 平盤、沒有視窗；6488 還沒成交
  assert.deepEqual(
    [full.up, full.down, full.flat, full.untraded, full.limit_up, full.limit_down, full.new_high, full.new_low],
    [1, 1, 1, 1, 1, 1, 1, 1],
  )
  assert.equal(full.since, '11:55:00')
  assert.equal(full.time, '11:56:09')

  // 昨天的份不算：今天只有兩份 → 不算家數；一份都沒有 → null
  assert.equal(summarize(universe, [s0, s1, { ...s2, date: '2026-10-16' }], '2026-10-19')?.up, null)
  assert.equal(summarize(universe, [null, null, null], '2026-10-19'), null)
})

test('D-199：分上市、上櫃——家數各算各的、加起來等於全市場；成交金額對各自的 20 日平均', () => {
  const u: Universe = { ...universe, amount_avg_by: { tse: 800e8, otc: 200e8 } }
  const at = (k: number, rows: Slice['q']) => ({ ...slice(k, rows, k + 1), n: 2 })
  const s0 = at(0, [['1101', 31, 28, 30.8, 25.2], ['1102', 34, 38, 41.8, 34.2]])
  const s1 = at(1, [['2330', 1000, 1000, 1100, 900], ['6488', 96, 95, 104.5, 85.5]])
  const r = summarize(u, [s0, s1], '2026-10-19')
  assert.ok(r)
  const { tse, otc } = r.by_market
  assert.deepEqual([tse.up, tse.down, tse.flat, tse.limit_up, tse.limit_down, tse.new_high, tse.new_low], [1, 1, 1, 1, 1, 1, 1])
  assert.deepEqual([otc.up, otc.down, otc.flat, otc.untraded], [1, 0, 0, 0])
  for (const f of ['up', 'down', 'flat', 'untraded', 'limit_up', 'limit_down', 'new_high', 'new_low'] as const) {
    assert.equal((tse[f] ?? 0) + (otc[f] ?? 0), r[f], f)
  }
  assert.deepEqual([tse.amount, tse.amount_ratio, otc.amount, otc.amount_ratio], [400e8, 0.5, 100e8, 0.5])
  assert.deepEqual(r.trend_otc, [], '舊 Worker 沒抓櫃買分時 → 空的')
})

test('D-199：Worker 改切法的過渡期——照最新那份的切法判斷有沒有掃完；舊切法（沒寫 n＝3 份）的份不混進來', () => {
  const legacy = [0, 1, 2].map((k) => slice(k, [['1101', 31, 28, 30.8, 25.2]], k + 1))
  const fresh = [0, 1].map((k) => ({ ...slice(k, [['1102', 34, 38, 41.8, 34.2]], 10 + k), n: 10 }))
  const r = summarize(universe, [...fresh, ...legacy], '2026-10-19')
  assert.equal(r?.up, null, '新切法十份只到兩份：還沒掃完')
  assert.equal(summarize(universe, legacy, '2026-10-19')?.up, 1, '只有舊切法、三份到齊：照算')
})

test('T-397：股票清單要有日期、代號是四碼普通股、市場是上市或上櫃', () => {
  const u = parseUniverse({ asof: '2026-10-16', amount_avg: 1.14e12, stocks: [['1101', 'tse', 27.3, 24.05]] })
  assert.equal(u.stocks.length, 1)
  assert.equal(u.amount_avg_by, null, '舊的清單沒有分市場平均')
  assert.deepEqual(parseUniverse({ asof: '2026-10-16', amount_avg: 1, amount_avg_by: { tse: 9e11, otc: 0 }, stocks: [['1101', 'tse', 1, 1]] }).amount_avg_by, { tse: 9e11, otc: null })
  assert.throws(() => parseUniverse({ asof: '2026-10-16', amount_avg: 1, stocks: [] }), /股票清單/)
  assert.throws(() => parseUniverse({ asof: '2026-10-16', amount_avg: 1, stocks: [['0050', 'tse', 1, 1]] }), /格式錯誤/)
  assert.throws(() => parseUniverse({ asof: '2026-10-16', amount_avg: 1, stocks: [['1101', 'esb', 1, 1]] }), /格式錯誤/)
})

// ── Worker 每分鐘的流程（假的證交所與 KV）──────────────────────────────

const misEntry = (ex: string, c: string, z: string, y: string, d = '20261019') => ({
  c,
  ex,
  d,
  t: '11:56:09',
  z,
  y,
  u: (Number(y) * 1.1).toFixed(2),
  w: (Number(y) * 0.9).toFixed(2),
  trade: { z },
})

function fakeWorld(opts: { date?: string; uni?: UniverseStock[] } = {}) {
  const store = new Map<string, string>()
  const calls: string[] = []
  const sleeps: number[] = []
  const uni = opts.uni ?? stocks(30)
  const kv = {
    get: async (k: string) => (store.has(k) ? JSON.parse(store.get(k) as string) : null),
    put: async (k: string, v: string) => void store.set(k, v),
  }
  const fetchText = async (url: string): Promise<string> => {
    calls.push(url)
    if (url.startsWith('https://example.test/universe')) return JSON.stringify({ asof: '2026-10-16', amount_avg: 1e12, stocks: uni })
    if (url.includes('getStatis')) return JSON.stringify({ rtcode: '0000', detail: { fz: url.includes('ex=tse') ? '400' : '100' } })
    if (url.includes('mis_ohlc_TSE')) return JSON.stringify({ rtcode: '0000', ohlcArray: [{ c: '46443.85', ts: '090100' }] })
    if (url.includes('mis_ohlc_OTC')) return JSON.stringify({ rtcode: '0000', ohlcArray: [{ c: '396.12', ts: '090100' }] })
    const ch = decodeURIComponent(new URL(url).searchParams.get('ex_ch') ?? '').split('|')
    const d = opts.date ?? '20261019'
    return JSON.stringify({
      rtcode: '0000',
      msgArray: ch.map((x) => {
        const [ex, rest] = x.split('_')
        const c = rest.replace('.tw', '')
        return c === 't00' || c === 'o00' ? { ...misEntry(ex, c, '46656.30', '47111.26', d), u: undefined, w: undefined } : misEntry(ex, c, '10.5', '10', d)
      }),
    })
  }
  return { store, calls, sleeps, kv, fetchText, sleep: async (ms: number) => void sleeps.push(ms) }
}

// 台北 2026-10-19（一）11:56＝UTC 03:56；11:56 是當天第 716 分鐘 → 716 % 15 = 11（雙數分鐘抓加權走勢）
const AT = Date.parse('2026-10-19T03:56:00Z')

test('T-397：Worker 盤中每分鐘問一份並寫進 KV；股票清單一天只抓一次；請求一個一個隔開', async () => {
  const w = fakeWorld()
  const msg = await runOnce({ now: AT, kv: w.kv, fetchText: w.fetchText, sleep: w.sleep, universeUrl: 'https://example.test/universe' })
  assert.match(msg, /第 11 份 2 檔/)
  const saved = JSON.parse(w.store.get(sliceKey(11)) as string) as Slice
  assert.equal(saved.date, '2026-10-19')
  assert.equal(saved.n, SLICES, '每份記自己是幾份裡的一份（網站換切法時靠它判斷）')
  assert.deepEqual(saved.q.map((r) => r[0]), ['1123', '1124'])
  assert.deepEqual(saved.amt, { tse: 400, otc: 100 })
  assert.deepEqual([saved.trend, saved.trend_otc], [[['09:01', 46443.85]], []], '雙數分鐘只抓加權走勢')
  assert.equal(JSON.parse(w.store.get(UNIVERSE_KEY) as string).fetched, '2026-10-19')
  assert.deepEqual(JSON.parse(w.store.get(universeKey(11)) as string).stocks.map((r: UniverseStock) => r[0]), ['1123', '1124'], '每份一段清單')
  // 清單 1 次＋報價 1 次＋成交統計 2 次＋走勢 1 次；證交所的 4 個請求之間隔 3 次
  assert.equal(w.calls.length, 5)
  assert.deepEqual(w.sleeps, [MIN_GAP_MS, MIN_GAP_MS, MIN_GAP_MS])

  await runOnce({ now: AT + 60_000, kv: w.kv, fetchText: w.fetchText, sleep: w.sleep, universeUrl: 'https://example.test/universe' })
  assert.equal(w.calls.filter((u) => u.includes('example.test')).length, 1, '清單一天只抓一次')
  const next = JSON.parse(w.store.get(sliceKey(12)) as string) as Slice
  assert.deepEqual([next.trend, next.trend_otc], [[], [['09:01', 396.12]]], '單數分鐘只抓櫃買走勢')
})

test('T-397：盤前盤後不抓；休市（證交所給的是前一天）不寫', async () => {
  const w = fakeWorld()
  const early = await runOnce({ now: Date.parse('2026-10-19T00:59:00Z'), kv: w.kv, fetchText: w.fetchText, sleep: w.sleep, universeUrl: 'https://example.test/universe' })
  assert.match(early, /略過/)
  const late = await runOnce({ now: Date.parse('2026-10-19T05:37:00Z'), kv: w.kv, fetchText: w.fetchText, sleep: w.sleep, universeUrl: 'https://example.test/universe' })
  assert.match(late, /略過/)
  assert.equal(w.calls.length, 0)

  const h = fakeWorld({ date: '20261016' })
  const msg = await runOnce({ now: AT, kv: h.kv, fetchText: h.fetchText, sleep: h.sleep, universeUrl: 'https://example.test/universe' })
  assert.match(msg, /休市/)
  assert.ok(!h.store.has(sliceKey(11)))
})

test('T-397：證交所擋下（回 HTML）→ 丟錯、不寫', async () => {
  const w = fakeWorld()
  const blocked = async (url: string) => (url.includes('example.test') ? w.fetchText(url) : '<html>因為安全性考量，您的連線已被中斷</html>')
  await assert.rejects(
    runOnce({ now: AT, kv: w.kv, fetchText: blocked, sleep: w.sleep, universeUrl: 'https://example.test/universe' }),
    /防火牆/,
  )
  assert.ok(!w.store.has(sliceKey(11)))
})

test('T-397：台北時鐘（固定 +8）', () => {
  assert.deepEqual(taipeiClock(AT), { date: '2026-10-19', hhmm: '11:56' })
  assert.deepEqual(taipeiClock(Date.parse('2026-10-19T16:30:00Z')), { date: '2026-10-20', hhmm: '00:30' })
})

// ── 網站：卡片畫哪一份 ─────────────────────────────────────────────

const daily = { date: '2026-10-16', taiex_close: 47111.26, taiex_chg: 0.003, otc_close: 400.26, otc_chg: 0.0085, amount: 1.13e12, amount_avg: 1.12e12, amount_ratio: 1.01, up: 1000, down: 700, flat: 200, limit_up: 32, limit_down: 2, new_high: 178, new_low: 97 } as Market
const live: LiveMarket = {
  date: '2026-10-19',
  time: '11:56:09',
  since: '11:53:02',
  taiex_close: 46656.3,
  taiex_chg: -0.0097,
  taiex_prev: 47111.26,
  otc_close: 394.16,
  otc_chg: -0.015,
  amount: 7.0e11,
  amount_avg: 1.14e12,
  amount_ratio: 0.61,
  up: 293,
  down: 1491,
  flat: 106,
  untraded: 51,
  limit_up: 16,
  limit_down: 4,
  new_high: 66,
  new_low: 392,
  trend: [
    ['09:01', 46443.85],
    ['11:56', 46656.3],
  ],
}

test('T-397：盤中統計比盤後體檢新就畫盤中；13:30 以後是收盤速報；同一天以盤後體檢為準', () => {
  const v = marketView(daily, live)
  assert.equal(v?.kind, 'live')
  assert.equal(v?.time, '11:56')
  assert.equal(v?.scopes.all.up, 293)
  assert.equal(v?.scopes.tse, null, '舊伺服器沒有分市場 → 只有全部')
  assert.equal(marketView(daily, { ...live, time: '13:30:00' })?.kind, 'final')
  const same = marketView({ ...daily, date: '2026-10-19' }, live)
  assert.equal(same?.kind, 'close')
  assert.equal(same?.scopes.all.up, 1000)
  assert.equal(marketView(daily, null)?.kind, 'close')
  assert.equal(marketView(null, live)?.kind, 'live')
  assert.equal(marketView(null, null), null)
})

test('T-397：分時走勢小圖——橫軸 9:00～13:30、昨收算進上下範圍；不到兩點不畫', () => {
  const p = sparkPoints(
    [
      ['09:01', 100],
      ['10:00', 110],
    ],
    90,
    24,
  )
  assert.ok(p)
  assert.equal(p.line, '1,12 60,2')
  assert.equal(p.base, 22)
  assert.deepEqual(p.last, [60, 2])
  assert.equal(sparkPoints([['09:01', 100]], 90, 24), null)
})

test('D-199：分上市、上櫃——盤中用 by_market、收盤後用 market_split；走勢上櫃看櫃買、其他看加權', () => {
  const br = (up: number) => ({ up, down: 1, flat: 1, limit_up: 0, limit_down: 0, new_high: 0, new_low: 0, amount: 1e10, amount_avg: 2e10, amount_ratio: 0.5 })
  const lv: LiveMarket = { ...live, trend_otc: [['09:01', 396], ['11:56', 394]], otc_prev: 400.26, by_market: { tse: br(200), otc: br(93) } }
  const v = marketView(daily, lv)
  assert.ok(v)
  assert.deepEqual([v.scopes.tse?.up, v.scopes.otc?.up], [200, 93])
  assert.deepEqual(scopeTrend(v, 'otc'), { name: '櫃買', trend: [['09:01', 396], ['11:56', 394]], prev: 400.26 })
  assert.deepEqual(scopeTrend(v, 'tse').name, '加權')
  assert.deepEqual(scopeTrend(v, 'all').prev, 47111.26)
  const c = marketView(daily, null, { tse: br(683), otc: br(538) })
  assert.deepEqual([c?.kind, c?.scopes.tse?.up, c?.scopes.otc?.up], ['close', 683, 538])
  assert.equal(marketView(daily, null, null)?.scopes.otc, null, '新表還沒有 → 只有全部')
})

test('D-199：走勢圖標今天最高、最低（不含昨收）與它們的位置', () => {
  const p = sparkPoints([['09:01', 100], ['10:00', 110], ['11:00', 105]], 90, 24)
  assert.ok(p)
  assert.deepEqual(p.high, { value: 110, y: 2 })
  assert.deepEqual(p.low, { value: 100, y: 12 })
})

test('D-199：走勢隔分鐘輪流抓——加權、櫃買各取最新一份有抓那條的', () => {
  const s1 = { ...slice(0, [], 1), n: 2, trend_otc: [['09:01', 396]] as [string, number][], trend: [] as [string, number][] }
  const s2 = { ...slice(1, [], 2), n: 2 }
  const r = summarize(universe, [s1, s2], '2026-10-19')
  assert.deepEqual(r?.trend, s2.trend)
  assert.deepEqual(r?.trend_otc, [['09:01', 396]])
})
