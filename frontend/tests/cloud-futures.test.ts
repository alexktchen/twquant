// T-450／D-220：小台模擬分頁（盤中＋復盤）。使用者 2026-10-26：「這個可以在我網站即時模擬出來嗎？」「畫面可以是復盤模式跟盤中模式」
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { DAYS_LIMIT, NOT_READY, NO_SECONDS, QUOTES_NOT_READY, dayOk, productOk } from '../functions/_lib/futures.ts'
import { FEATURE_ROLES, can } from '../functions/_lib/roles.ts'
import { FEATURES, lockedText, tabAllowed } from '../src/cloud/access.ts'
import {
  STALE_AFTER_S,
  addSeconds,
  CAL_VIEW_NAME,
  MTX_COST,
  SIDE_OF,
  STRATEGY_NAME,
  accountPnl,
  accountSide,
  allFillTimes,
  barsAt,
  change,
  chartMarks,
  chartTime,
  comboSummary,
  compactYuan,
  dayAt,
  dayPnl,
  costOf,
  isPaper,
  decodeSeconds,
  fillTimes,
  gapBook,
  gapLevels,
  gapSub,
  gapSummary,
  lastAtOrBefore,
  mergeEvents,
  monthSummary,
  monthWeeks,
  monthsOf,
  pickLegs,
  closeNowPnl,
  minuteOf,
  replayAt,
  replayBook,
  shouldPoll,
  staleSeconds,
  taipeiNow,
  tone,
  yuan,
  type FuturesDay,
  type FuturesTrade,
  type GapFail,
  type Sleeve,
} from '../src/cloud/futures.ts'
import { tabFromUrl } from '../src/cloud/tabs.ts'

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')

/** 2026-10-22 的真實走勢縮寫（fubon_live.py 用期交所逐筆重播的那一天）：ORB 做多被停損，11:15 噪音區放空、還沒回補。 */
function day(): FuturesDay {
  return {
    version: 1,
    day: '2026-10-22',
    source: 'taifex_replay',
    status: 'live',
    contract: '202611',
    symbol: 'MXFK6',
    updated_at: '2026-10-22T11:30:05+08:00',
    last_tick: '11:29:59',
    last_price: 46753,
    prev_close: 46998,
    open: 47197,
    bars: [
      ['08:45', 47197, 47230, 47150, 47220, 47200],
      ['08:49', 47300, 47333, 47290, 47321, 47260],
      ['08:50', 47318, 47330, 47300, 47310, 47270],
      ['09:00', 47250, 47260, 47200, 47210, 47250],
      ['09:05', 47160, 47170, 47130, 47140, 47230],
      ['09:10', 47150, 47180, 47140, 47170, 47220],
      ['11:15', 46770, 46780, 46760, 46765, 47050],
      ['11:29', 46760, 46770, 46750, 46753, 47040],
    ],
    orb: { state: 'closed', box: { open: 47197, close: 47321, high: 47333, low: 47150, side: 1 }, stop: 47150, target: 49009, entry_price: 47319, entry_time: '08:50:01' },
    noise_lb: [
      ['09:15', 46862.1],
      ['11:15', 46784],
    ],
    noise: [{ time: '11:15', lb: 46784, price: 46766, vwap: 47051.7, target: -1 }],
    sleeves: {
      ORB_LONG: { position: 0, entry_price: null, realized: -9354.5, open_pnl: null },
      NOISE_SHORT: { position: -1, entry_price: 46763, realized: 0, open_pnl: 296.5 },
    },
    events: [
      { time: '08:50:00', kind: 'signal', sleeve: 'ORB_LONG', text: '開盤 5 分鐘收紅' },
      { time: '08:50:01', kind: 'fill', sleeve: 'ORB_LONG', text: '模擬買進 47319' },
      { time: '09:05:40', kind: 'signal', sleeve: 'ORB_LONG', text: '碰到停損 47150' },
      { time: '09:05:41', kind: 'exit', sleeve: 'ORB_LONG', text: '模擬賣出 47135（停損）' },
      { time: '11:15:00', kind: 'check', sleeve: 'NOISE_SHORT', text: '11:15 檢查 → 持空' },
      { time: '11:15:01', kind: 'fill', sleeve: 'NOISE_SHORT', text: '模擬放空 46763' },
    ],
    trades: [
      {
        day: '2026-10-22',
        sleeve: 'ORB_LONG',
        side: 1,
        signal_time: '08:50:00',
        entry_time: '08:50:01.000',
        entry_price: 47319,
        exit_time: '09:05:41.000',
        exit_price: 47135,
        exit_reason: 'STOP',
        points: -184,
        net_pnl: -9354.5,
        source: 'live',
      },
    ],
    total: -9058,
    official: null,
    official_match: null,
  }
}

test('T-450 AC3 復盤：拉到某一分鐘只看得到那之前的 K 棒、事件與成交（不偷看後面）', () => {
  const v = replayAt(day(), '09:00')
  assert.deepEqual(v.bars.map((b) => b[0]), ['08:45', '08:49', '08:50', '09:00'])
  assert.deepEqual(v.events.map((e) => e.time), ['08:50:00', '08:50:01'])
  assert.deepEqual(v.fills.map((f) => [f.sleeve, f.kind, f.price]), [['ORB_LONG', 'entry', 47319]])
  assert.equal(v.price, 47210)
})

test('T-450 AC3 復盤：部位與損益照那一刻算——持倉用「現在平倉的話」，平倉後變已實現', () => {
  const open = replayAt(day(), '09:00')
  assert.deepEqual(open.sleeves.ORB_LONG, { position: 1, entry_price: 47319, realized: 0, open_pnl: Math.round(closeNowPnl(1, 47319, 47210)) })
  assert.equal(open.sleeves.NOISE_SHORT.position, 0)
  const closed = replayAt(day(), '09:10')
  assert.deepEqual(closed.sleeves.ORB_LONG, { position: 0, entry_price: null, realized: -9354, open_pnl: null }) // Math.round(-9354.5) → -9354
  assert.deepEqual(closed.fills.map((f) => f.kind), ['entry', 'exit'])
  // 還在盤中、還沒回補的噪音區空單：trades 裡沒有，從 sleeves 補上，而且要等成交時間到了才出現
  assert.equal(replayAt(day(), '11:10').sleeves.NOISE_SHORT.position, 0)
  const short = replayAt(day(), '11:29')
  assert.equal(short.sleeves.NOISE_SHORT.position, -1)
  assert.equal(short.sleeves.NOISE_SHORT.open_pnl, Math.round(closeNowPnl(-1, 46763, 46753)))
  assert.equal(short.total, -9354 + Math.round(closeNowPnl(-1, 46763, 46753)))
})

test('T-450 AC3 現在平倉的話：與 fubon_live.py 同一條公式（含出場滑價、來回手續費、期交稅）', () => {
  // fubon_live.py 在 2026-10-22 11:29:59 算出 296.5（價 46753、空單 46763）
  assert.ok(Math.abs(closeNowPnl(-1, 46763, 46753) - 296.483) < 0.01)
  assert.ok(Math.abs(closeNowPnl(1, 100, 100) - (-50 - 60 - 0.00002 * 199 * 50)) < 1e-9)
})

test('T-450 AC4 盤中：只有台北 08:25～13:50、而且看的是今天，才每 15 秒重抓', () => {
  const at = (iso: string) => new Date(iso)
  assert.equal(shouldPoll(at('2026-10-27T00:24:00Z'), null), false) // 台北 08:24
  assert.equal(shouldPoll(at('2026-10-27T00:25:00Z'), null), true)
  assert.equal(shouldPoll(at('2026-10-27T05:50:00Z'), '2026-10-27'), true) // 台北 13:50
  assert.equal(shouldPoll(at('2026-10-27T05:51:00Z'), null), false)
  assert.equal(shouldPoll(at('2026-10-27T02:00:00Z'), '2026-10-22'), false) // 看的是舊的一天
  assert.deepEqual(taipeiNow(at('2026-10-26T16:30:00Z')), { day: '2026-10-27', hhmm: '00:30' }) // 跨日照台北
})

test('T-450 AC4 盤中：超過 60 秒沒更新算斷線；寫入時間壞掉回 null', () => {
  assert.equal(STALE_AFTER_S, 60)
  assert.equal(staleSeconds('2026-10-27T10:00:00+08:00', new Date('2026-10-27T02:01:30Z')), 90)
  assert.equal(staleSeconds('壞掉', new Date()), null)
})

test('T-450 圖表時間軸：台北盤中時間當成 UTC 顯示；數字寫法', () => {
  assert.equal(chartTime('2026-10-27', '08:45'), Date.UTC(2026, 9, 27, 8, 45) / 1000)
  assert.equal(minuteOf('13:44'), 13 * 60 + 44)
  assert.equal(yuan(12345.4), '+12,345')
  assert.equal(yuan(-9354.5), '-9,354')
  assert.equal(yuan(0.2), '0')
  assert.deepEqual([tone(5), tone(-5), tone(0), tone(null)], ['up', 'down', 'plain', 'plain'])
})

test('T-450 AC2 兩支端點只給 admin、在讀資料庫之前擋、不進 60 秒暫存', () => {
  assert.deepEqual(FEATURE_ROLES.futures, ['admin'])
  assert.ok(can('cktalex@gmail.com', 'futures'))
  assert.ok(!can('friend@example.com', 'futures'))
  assert.ok(!can(null, 'futures'))
  for (const f of ['functions/api/futures/index.ts', 'functions/api/futures/days.ts', 'functions/api/futures/seconds.ts']) {
    const src = read(f)
    assert.match(src, /export const onRequestGet: PagesFunction<Env> = async \(ctx\) => \(await denied\(ctx\.request, 'futures', ctx\.env\)\) \?\? handle\(ctx\)/, f)
    assert.ok(!src.includes('cached('), f) // 盤中每 15 秒就變，暫存會讓畫面落後
  }
  assert.ok(dayOk('2026-10-27') && !dayOk('2026-1-27') && !dayOk("2026-10-27' OR 1=1"))
  assert.ok(NOT_READY.includes('fubon_live.py --upload') && DAYS_LIMIT > 200 && NO_SECONDS.includes('1 分 K'))
})

test('T-450 AC5 分頁只給有「小台模擬」權限的人；?tab=futures 認得', () => {
  assert.ok(FEATURES.includes('futures'))
  assert.equal(tabFromUrl('https://twquant.pages.dev/?tab=futures'), 'futures')
  assert.ok(!tabAllowed('futures', []))
  assert.ok(tabAllowed('futures', ['futures']))
  assert.equal(lockedText('futures', false), '這個帳號沒有「台指期」的權限') // T-451 起分頁改名（小台模擬＋大台、微台）
  const app = read('src/cloud/CloudApp.tsx')
  assert.match(app, /features\.includes\('futures'\) && \(/)
  assert.match(app, /tab === 'futures' \? \(\s*<FuturesPage refresh=\{refresh\} \/>/)
})

// ── AC6 秒級復盤（使用者：「我想要秒級的復盤」「買進賣出的點位要在復盤過程標出來」） ──────────────
/** fubon_live.py seconds_payload() 的格式：08:45:00 起第幾秒、價、VWAP×10，都是差分整數 */
const SECS = { version: 1 as const, day: '2026-10-22', t0: '08:45:00', s: [0, 1, 299, 1, 2, 897], p: [47197, 3, 120, -1, 0, -270], w: [471970, 10, 900, 5, 0, -500] }

test('T-450 AC6 秒級資料：差分加回去、時間從 08:45:00 算；長度對不上就丟錯', () => {
  const x = decodeSeconds(SECS)
  assert.deepEqual(x.label, ['08:45:00', '08:45:01', '08:50:00', '08:50:01', '08:50:03', '09:05:00'])
  assert.deepEqual(x.price, [47197, 47200, 47320, 47319, 47319, 47049])
  assert.deepEqual(x.vwap, [47197, 47198, 47288, 47288.5, 47288.5, 47238.5])
  assert.throws(() => decodeSeconds({ ...SECS, p: [1] }), /長度不一致/)
})

test('T-450 AC6 秒級找點與播放：最後一個 ≤ 那一秒；加秒會進位', () => {
  const x = decodeSeconds(SECS)
  assert.equal(lastAtOrBefore(x.label, '08:50:02'), 3)
  assert.equal(lastAtOrBefore(x.label, '08:44:59'), -1)
  assert.equal(lastAtOrBefore(x.label, '13:45:00'), 5)
  assert.equal(addSeconds('08:59:55', 6), '09:00:01')
  assert.equal(addSeconds('08:45:00', 0.6), '08:45:00') // 不到一秒不動
})

test('T-450 AC6 秒級復盤：拉到某一秒——價用那一秒以前最後一筆，還沒走完的那一分鐘 K 棒不算，事件比到秒', () => {
  const d = day()
  const v = replayAt(d, '08:50:00', decodeSeconds(SECS))
  assert.equal(v.price, 47320)
  assert.deepEqual(v.bars.map((b) => b[0]), ['08:45', '08:49']) // 08:50 那根含 08:50:00 以後的成交，不能先畫
  assert.deepEqual(v.events.map((e) => e.time), ['08:50:00']) // 08:50:01 的成交還沒發生
  assert.deepEqual(v.fills, [])
  const after = replayAt(d, '08:50:01', decodeSeconds(SECS))
  assert.deepEqual(after.fills.map((f) => [f.time.slice(0, 8), f.price]), [['08:50:01', 47319]]) // 買點在成交那一秒、成交價
  assert.equal(after.sleeves.ORB_LONG.position, 1)
})

test('T-450 AC6 上一個／下一個買賣點：成交與平倉的時間照先後', () => {
  assert.deepEqual(fillTimes(day()), ['08:50:01', '09:05:41', '11:15:01'])
})

// ── T-451 大台、微台行情（使用者 2026-10-27：「我也要微台跟大台的資料」「放到網站」） ─────────────────
test('T-451 AC2 大台／微台三支端點只給 admin、在讀資料庫之前擋、不進暫存；商品只認 TX、TMF', () => {
  for (const f of ['functions/api/futures/quotes/index.ts', 'functions/api/futures/quotes/days.ts', 'functions/api/futures/quotes/seconds.ts']) {
    const src = read(f)
    assert.match(src, /export const onRequestGet: PagesFunction<Env> = async \(ctx\) => \(await denied\(ctx\.request, 'futures', ctx\.env\)\) \?\? handle\(ctx\)/, f)
    assert.ok(!src.includes('cached('), f)
    assert.match(src, /if \(!productOk\(product\)\) return fail\(400/, f) // 先驗商品才查資料庫
  }
  assert.ok(productOk('TX') && productOk('TMF'))
  assert.ok(!productOk('MTX') && !productOk(null) && !productOk("TX' OR 1=1"))
  assert.ok(QUOTES_NOT_READY.includes('fubon_live.py --upload'))
})

test('T-451 AC3 大台／微台復盤跟小台同一套規則：拉到某一秒，價用那一秒以前最後一筆、沒走完的那一分鐘 K 棒不算', () => {
  const bars = day().bars
  const secs = decodeSeconds(SECS)
  const a = barsAt(bars, '08:50:00', secs)
  assert.deepEqual(a.bars.map((b) => b[0]), ['08:45', '08:49'])
  assert.equal(a.price, 47320)
  const m = barsAt(bars, '08:50')
  assert.deepEqual(m.bars.map((b) => b[0]), ['08:45', '08:49', '08:50']) // 1 分 K：含那一分鐘
  assert.equal(m.price, 47310)
  assert.deepEqual(replayAt(day(), '08:50:00', secs).bars, a.bars) // 小台的 replayAt 也是用它
})

test('T-451 漲跌：點數與百分比；沒有昨收或沒有價就不算', () => {
  const c = change(47320, 47000)
  assert.ok(c !== null && c.pts === 320 && Math.abs(c.pct - 320 / 47000) < 1e-12)
  assert.equal(change(null, 47000), null)
  assert.equal(change(47320, null), null)
})

test('T-451 AC5 分頁改名「台指期」，上面切小台・模擬單／大台／微台', () => {
  const page = read('src/cloud/FuturesPage.tsx')
  assert.match(page, /\{ key: 'MTX', label: '小台・模擬單' \}/)
  assert.match(page, /\{ key: 'TX', label: '大台' \}/)
  assert.match(page, /\{ key: 'TMF', label: '微台' \}/)
  assert.match(read('src/cloud/CloudApp.tsx'), /onClick=\{\(\) => setTab\('futures'\)\}>\s*台指期/)
})

// ── 大台、微台也有模擬單（使用者 2026-10-27：「也要有模擬單」） ───────────────────────────────
test('T-451 AC6 現在平倉的話照各自合約：大台每點 200 元、微台 10 元；資料沒寫成本＝小台', () => {
  assert.deepEqual(costOf({}), MTX_COST)
  assert.deepEqual(costOf({ point_value: 200, commission: 60 }), { point_value: 200, commission: 60 })
  // 同一筆空單（46763 → 現價 46753）：小台 296.48、大台 1305.93（fubon_live.py 同一條公式）
  assert.ok(Math.abs(closeNowPnl(-1, 46763, 46753) - 296.483) < 0.01)
  assert.ok(Math.abs(closeNowPnl(-1, 46763, 46753, { point_value: 200, commission: 60 }) - 1305.932) < 0.01)
  const tx = { ...day(), product: 'TX' as const, name: '大台', volume: 1, point_value: 200, commission: 60 }
  assert.equal(replayAt(tx, '11:29').sleeves.NOISE_SHORT.open_pnl, Math.round(closeNowPnl(-1, 46763, 46753, costOf(tx))))
})

test('T-451 AC6 大台／微台那天有模擬單就用小台同一個畫面；前面不足 14 天只有行情', () => {
  assert.ok(isPaper(day()))
  const quoteOnly = { version: 1 as const, day: '2026-09-10', product: 'TX' as const, name: '大台', source: 'taifex_replay' as const, status: 'closed' as const, contract: '202609', symbol: 'TXFI6', updated_at: '2026-09-10T13:50:00+08:00', last_tick: '13:45:00', last_price: 1, prev_close: null, open: 1, volume: 1, bars: [] }
  assert.ok(!isPaper(quoteOnly))
  const page = read('src/cloud/FuturesPage.tsx')
  assert.match(page, /\{data && isPaper\(data\) && <PaperBody /)
  assert.match(page, /\{data && !isPaper\(data\) && at && \(/)
  // 日期清單有模擬單的日子多合計與一致與否（同小台）
  assert.match(read('functions/api/futures/quotes/days.ts'), /\(payload->>'total'\)::float8 AS total/)
})

// ── 開高走低（T-452，使用者 2026-10-27：「abc 也新增成一個策略加到前端」） ───────────────────────
const gapTrade = (sleeve: 'GAP_A' | 'GAP_B' | 'GAP_C', exit_time: string, exit_price: number, exit_reason: string, net_pnl: number): FuturesTrade => ({
  day: '2026-10-27',
  sleeve,
  side: -1,
  signal_time: '09:22:00.000',
  entry_time: '09:22:01.000',
  entry_price: 46479,
  exit_time,
  exit_price,
  exit_reason,
  points: 46479 - exit_price,
  net_pnl,
  source: 'live',
})

/** 2026-10-27 的真實走勢縮寫（fubon_live.py 用期交所逐筆重播）：開高 +0.56%，09:22 A、B、C 同時放空，C 10:23 補完缺口，A、B 收盤回補。 */
function gapDay(): FuturesDay {
  const rule = (target: number | null) => ({ state: 'closed' as const, stop: 46848, target, signal_time: '09:22:00', entry_price: 46479, entry_time: '09:22:01' })
  const flat = (realized: number) => ({ position: 0 as const, entry_price: null, realized, open_pnl: null })
  const g: GapFail = {
    version: 1,
    names: { GAP_A: 'A 開高失守', GAP_B: 'B 跌破開盤區間', GAP_C: 'C 補缺口' },
    threshold_pct: 0.526,
    gap_pct: 0.558,
    active: true,
    range: { high: 46848, low: 46505 },
    rules: { GAP_A: rule(null), GAP_B: rule(null), GAP_C: rule(46262) },
    sleeves: { GAP_A: flat(17947.4), GAP_B: flat(17947.4), GAP_C: flat(10897.3) },
    events: [
      { time: '08:45:00', kind: 'info', sleeve: null, text: '開高 +0.56%（門檻 +0.526%）→ 等跌破再放空' },
      { time: '09:00:00', kind: 'info', sleeve: 'GAP_B', text: '開盤區間（08:45～08:59）高 46848、低 46505' },
      { time: '09:22:00', kind: 'signal', sleeve: 'GAP_A', text: '09:22 價格 46483 跌破開盤 46520 → A、C 模擬放空' },
      { time: '09:22:00', kind: 'signal', sleeve: 'GAP_B', text: '09:22 價格 46483 跌破開盤區間低點 46505 → B 模擬放空' },
      { time: '09:22:01', kind: 'fill', sleeve: 'GAP_A', text: 'A 開高失守：模擬放空 46479' },
      { time: '09:22:01', kind: 'fill', sleeve: 'GAP_C', text: 'C 補缺口：模擬放空 46479' },
      { time: '09:22:01', kind: 'fill', sleeve: 'GAP_B', text: 'B 跌破開盤區間：模擬放空 46479' },
      { time: '10:23:28', kind: 'signal', sleeve: 'GAP_C', text: 'C 補缺口：補完缺口 46262' },
      { time: '10:23:29', kind: 'exit', sleeve: 'GAP_C', text: 'C 補缺口：模擬回補 46258（停利）' },
      { time: '13:44:01', kind: 'exit', sleeve: 'GAP_A', text: 'A 開高失守：模擬回補 46117（收盤前）' },
      { time: '13:44:01', kind: 'exit', sleeve: 'GAP_B', text: 'B 跌破開盤區間：模擬回補 46117（收盤前）' },
    ],
    trades: [
      gapTrade('GAP_C', '10:23:29.000', 46258, 'TARGET', 10897.3),
      gapTrade('GAP_A', '13:44:01.000', 46117, 'EOD', 17947.4),
      gapTrade('GAP_B', '13:44:01.000', 46117, 'EOD', 17947.4),
    ],
    total: 46792.1,
  }
  return {
    ...day(),
    day: '2026-10-27',
    status: 'closed',
    open: 46520,
    prev_close: 46262,
    bars: [
      ['08:45', 46520, 46560, 46505, 46540, 46530],
      ['08:51', 46799, 46848, 46789, 46799, 46700],
      ['09:00', 46720, 46759, 46627, 46673, 46690],
      ['09:21', 46525, 46537, 46483, 46483, 46650],
      ['09:22', 46484, 46507, 46450, 46475, 46640],
      ['10:00', 46400, 46420, 46380, 46410, 46560],
      ['10:23', 46300, 46310, 46255, 46262, 46500],
      ['13:44', 46102, 46120, 46070, 46077, 46400],
    ],
    gap_fail: g,
  }
}

/** 同一天 10:00 盤中的樣子：三條都還持空，trades 還沒有。 */
function gapLive(): FuturesDay {
  const d = gapDay()
  const g = d.gap_fail!
  const open = { position: -1 as const, entry_price: 46479, realized: 0, open_pnl: -3655 }
  return {
    ...d,
    status: 'live',
    bars: d.bars.slice(0, 6),
    gap_fail: {
      ...g,
      rules: { GAP_A: { ...g.rules.GAP_A, state: 'open' }, GAP_B: { ...g.rules.GAP_B, state: 'open' }, GAP_C: { ...g.rules.GAP_C, state: 'open' } },
      sleeves: { GAP_A: open, GAP_B: open, GAP_C: open },
      events: g.events.slice(0, 7),
      trades: [],
    },
  }
}

test('T-452 AC1 舊資料沒有開高走低 → 帳本是 null、畫面提示重新上傳；有的話就是 A、B、C 三個子帳戶', () => {
  assert.equal(gapBook(day()), null)
  assert.deepEqual(gapBook(gapDay())!.sleeves, ['GAP_A', 'GAP_B', 'GAP_C'])
  // T-453 起兩個策略同一個畫面：沒有開高走低的那一天，「各策略」那塊寫要重新上傳
  assert.equal(gapSummary(null, null), '這一天的資料沒有（要重新上傳）')
  assert.match(read('src/cloud/FuturesPage.tsx'), /<p className="cq-empty">\{gapSummary\(null, null\)\}<\/p>/)
})

test('T-452 AC2 復盤：拉到某一刻只算那之前的 A、B、C 成交；持倉用「現在平倉的話」，平倉後變已實現', () => {
  const d = gapDay()
  const book = gapBook(d)!
  const before = replayBook(d, book, '09:21')
  assert.deepEqual(before.events.map((e) => e.time), ['08:45:00', '09:00:00'])
  assert.deepEqual(before.fills, [])
  assert.equal(before.total, 0)
  const holding = replayBook(d, book, '10:00')
  for (const s of ['GAP_A', 'GAP_B', 'GAP_C'] as const)
    assert.deepEqual(holding.sleeves[s], { position: -1, entry_price: 46479, realized: 0, open_pnl: Math.round(closeNowPnl(-1, 46479, 46410)) })
  const filled = replayBook(d, book, '10:23')
  assert.equal(filled.sleeves.GAP_C.realized, Math.round(10897.3))
  assert.equal(filled.sleeves.GAP_A.position, -1)
  assert.equal(replayBook(d, book, '23:59').total, Math.round(17947.4) * 2 + Math.round(10897.3))
})

test('T-452 AC2 盤中還沒回補的空單：trades 裡沒有，從 sleeves 補上，成交時間到了才出現', () => {
  const d = gapLive()
  const book = gapBook(d)!
  assert.equal(replayBook(d, book, '09:21').sleeves.GAP_A.position, 0)
  const v = replayBook(d, book, '23:59')
  assert.deepEqual(v.fills.map((f) => [f.sleeve, f.kind, f.price]), [['GAP_A', 'entry', 46479], ['GAP_B', 'entry', 46479], ['GAP_C', 'entry', 46479]])
  assert.equal(v.sleeves.GAP_B.open_pnl, Math.round(closeNowPnl(-1, 46479, 46410)))
})

test('T-452 AC3 數字卡的白話狀態只用那一刻知道的事（不劇透）', () => {
  const d = gapDay()
  const g = d.gap_fail!
  const at = (s: 'GAP_A' | 'GAP_B' | 'GAP_C', cut: string, day = d, gap = g) => gapSub(day, gap, s, replayBook(day, gapBook(day)!, cut).sleeves[s], cut)
  assert.equal(at('GAP_A', '08:55'), '等跌破開盤 46520')
  assert.equal(at('GAP_B', '08:55'), '09:00 定開盤區間')
  assert.equal(at('GAP_B', '09:10'), '等跌破 46505')
  assert.match(at('GAP_A', '10:00'), /^持空 1 口 @ 46479（現在平倉 /)
  assert.equal(at('GAP_A', '23:59'), '已平倉，這邊 +17,947')
  // 沒有開高：開盤就知道，整天都是「今天不做」
  const flat = { ...g, active: false, gap_pct: 0.12 }
  assert.equal(at('GAP_A', '09:00', { ...d, gap_fail: flat }, flat), '沒有開高（+0.12%），今天不做')
  // C 在進場那一刻缺口已經補完 → 不做；但訊號之前看不出來
  const skipped = { ...g, rules: { ...g.rules, GAP_C: { ...g.rules.GAP_C, state: 'skipped' as const, entry_price: null, entry_time: null } }, trades: g.trades.filter((t) => t.sleeve !== 'GAP_C') }
  const dc = { ...d, gap_fail: skipped }
  assert.equal(at('GAP_C', '09:10', dc, skipped), '等跌破開盤 46520')
  assert.equal(at('GAP_C', '09:30', dc, skipped), '缺口已經補完，不做')
  // 整天沒等到訊號：收盤才說
  const none = { ...g, rules: { ...g.rules, GAP_A: { ...g.rules.GAP_A, state: 'none' as const, signal_time: null, entry_price: null, entry_time: null } }, trades: [] }
  const dn = { ...d, gap_fail: none }
  assert.equal(at('GAP_A', '12:00', dn, none), '等跌破開盤 46520')
  assert.equal(at('GAP_A', '13:44', dn, none), '今天沒等到訊號')
})

test('T-452 AC4 圖上的買賣點：同一刻同價合成一個、標出是哪幾條；水平線只畫那一刻已經知道的', () => {
  const d = gapDay()
  const book = gapBook(d)!
  assert.deepEqual(
    chartMarks(replayBook(d, book, '23:59:59').fills, true).map((m) => [m.time, m.text]),
    [['09:22:01', 'ABC 空 46479'], ['10:23:29', 'C 補 46258'], ['13:44:01', 'AB 補 46117']],
  )
  // 固定組合（T-453 起跟開高走低畫在同一張圖）標「突破」「噪音」；1 分 K 時比到分鐘
  assert.deepEqual(chartMarks(replayAt(day(), '09:10').fills, false).map((m) => [m.time, m.text]), [['08:50', '突破 買 47319'], ['09:05', '突破 賣 47135']])
  const g = d.gap_fail!
  const lv = (cut: string) => gapLevels(d, g, replayBook(d, book, cut)).map((l) => `${l.title} ${l.price}`)
  assert.deepEqual(lv('08:55'), ['開盤 46520', '昨收 46262'])
  assert.deepEqual(lv('10:00'), ['開盤 46520', '昨收 46262', 'B 區間低 46505', 'ABC 停損 46848'])
  assert.deepEqual(lv('23:59'), ['開盤 46520', '昨收 46262', 'B 區間低 46505'])
  assert.deepEqual(gapLevels(d, { ...g, active: false }, replayBook(d, book, '10:00')), [])
})

test('T-452 AC5 成本照合約：大台的開高走低用大台的每點金額與手續費', () => {
  const tx = { ...gapLive(), product: 'TX' as const, name: '大台', volume: 1, point_value: 200, commission: 60 }
  assert.equal(replayBook(tx, gapBook(tx)!, '10:00').sleeves.GAP_A.open_pnl, Math.round(closeNowPnl(-1, 46479, 46410, costOf(tx))))
})

test('T-452 AC6 兩個策略的名字；兩支日期端點多回開高走低合計（T-453 起不切換、改用日曆，見 T-453 AC3、AC4）', () => {
  assert.deepEqual(STRATEGY_NAME, { combo: '固定組合', gap: '開高走低' })
  for (const f of ['functions/api/futures/days.ts', 'functions/api/futures/quotes/days.ts'])
    assert.match(read(f), /\(payload->'gap_fail'->>'total'\)::float8 AS gap_total/)
  assert.deepEqual(fillTimes(gapBook(gapDay())!), ['09:22:01', '09:22:01', '09:22:01', '10:23:29', '13:44:01', '13:44:01'])
})

// ── 兩個策略合在一起＋每日損益日曆（T-453，使用者 2026-10-27：「都整合在一起…再呈現各策略賺賠…用成日曆一次可以看完當天賺多少」） ──
test('T-453 AC1 兩個策略同一個畫面：各自的對照照舊；買賣點、時間軸照時間排在一起，各自標屬於誰（上面的合計 T-454 起是帳戶）', () => {
  const d = gapDay()
  const v = dayAt(d, '23:59')
  assert.equal(v.gap!.total, Math.round(17947.4) * 2 + Math.round(10897.3))
  assert.deepEqual(v.fills.slice(0, 3).map((f) => f.sleeve), ['ORB_LONG', 'ORB_LONG', 'GAP_A'])
  assert.deepEqual(v.events.slice(0, 3).map((e) => [e.time, e.strategy]), [['08:45:00', 'gap'], ['08:50:00', 'combo'], ['08:50:01', 'combo']])
  // 同一秒：固定組合排前面
  const tie = mergeEvents([{ time: '09:00:00', kind: 'info', sleeve: null, text: 'a' }], [{ time: '09:00:00', kind: 'info', sleeve: null, text: 'b' }])
  assert.deepEqual(tie.map((e) => [e.text, e.strategy]), [['a', 'combo'], ['b', 'gap']])
  // 舊資料沒有開高走低：帳戶就是固定組合（多單、空單各一條，本來就不會疊）
  const old = dayAt(day(), '23:59')
  assert.equal(old.gap, null)
  assert.equal(old.account.total, old.combo.total)
  // 上一個／下一個買賣點：兩個策略的都算
  assert.deepEqual(allFillTimes(d), ['08:50:01', '09:05:41', '09:22:01', '09:22:01', '09:22:01', '10:23:29', '11:15:01', '13:44:01', '13:44:01'])
})

test('T-453 AC2 各策略一句話：持多／持空幾口、平倉幾筆；開高走低多寫有沒有開高', () => {
  const d = gapDay()
  assert.equal(comboSummary(dayAt(d, '08:45').combo), '空手')
  assert.equal(comboSummary(dayAt(d, '09:00').combo), '持多 1 口')
  assert.equal(comboSummary(dayAt(d, '09:10').combo), '平倉 1 筆')
  assert.equal(comboSummary(dayAt(d, '23:59').combo), '持空 1 口')
  const g = d.gap_fail!
  assert.equal(gapSummary(g, dayAt(d, '09:10').gap), '開高 +0.56%・等跌破')
  assert.equal(gapSummary(g, dayAt(d, '10:00').gap), '開高 +0.56%・持空 3 口')
  assert.equal(gapSummary(g, dayAt(d, '23:59').gap), '開高 +0.56%・平倉 3 筆')
  assert.equal(gapSummary({ ...g, active: false, gap_pct: 0.12 }, dayAt(d, '09:10').gap), '沒有開高（+0.12%），不做')
  assert.equal(gapSummary({ ...g, active: null, gap_pct: null }, dayAt(d, '08:45').gap), '還沒開盤')
})

test('T-453 AC3 日曆：週一到週五一週一列；格子看單一策略（帳戶見 T-454 AC4）；只有行情的日子沒有金額；月小結', () => {
  const oct = monthWeeks('2026-10')
  assert.equal(oct.length, 5)
  assert.deepEqual(oct[0], [null, null, null, '2026-10-01', '2026-10-02'])
  assert.deepEqual(oct[4], ['2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30'])
  // 2026-11-01 是星期日：第一列從 11-02 星期一開始；11-30 星期一自己一列
  const nov = monthWeeks('2026-11')
  assert.deepEqual(nov[0], ['2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06'])
  assert.deepEqual(nov[nov.length - 1], ['2026-11-30', null, null, null, null])
  const r = { day: '2026-10-27', total: -13303, gap_total: 46792.1 }
  assert.equal(dayPnl(r, 'combo'), -13303)
  assert.equal(dayPnl(r, 'gap'), 46792.1)
  assert.equal(dayPnl({ day: '2026-10-22', total: 16092, gap_total: null }, 'gap'), null) // 舊資料只有固定組合
  assert.equal(dayPnl({ day: '2026-09-10' }, 'combo'), null) // 只有行情
  const rows = [
    r,
    { day: '2026-10-21', total: -2155, gap_total: 44187 },
    { day: '2026-10-20', total: 0, gap_total: 0 },
    { day: '2026-10-14', total: 0, gap_total: -13123 },
    { day: '2026-09-30', total: 5000, gap_total: 0 },
    { day: '2026-09-10' },
  ]
  assert.deepEqual(monthSummary(rows, '2026-10', 'combo'), { total: -13303 - 2155, days: 4, win: 0, loss: 2 })
  assert.deepEqual(monthSummary(rows, '2026-10', 'gap'), { total: 46792 + 44187 - 13123, days: 4, win: 2, loss: 1 })
  assert.deepEqual(monthSummary(rows, '2026-09', 'combo'), { total: 5000, days: 1, win: 1, loss: 0 })
  assert.deepEqual(monthsOf(rows), ['2026-09', '2026-10'])
  assert.equal(compactYuan(46792.1), '+4.7萬')
  assert.equal(compactYuan(-13123), '-1.3萬')
  assert.equal(compactYuan(183308), '+18.3萬')
  assert.equal(compactYuan(-3278), '-3,278')
  assert.equal(compactYuan(0), '0')
})

test('T-453 AC4 畫面：不再切策略；復盤上面是每日損益日曆（取代下拉）；各策略列出每個子帳戶（數字卡見 T-454 AC5）', () => {
  const page = read('src/cloud/FuturesPage.tsx')
  assert.doesNotMatch(page, /cq-fu-strategy|STRATEGIES|<DayPick/)
  assert.equal(page.match(/<PnlCalendar days=\{days\} value=\{replayDay\} onPick=\{setPick\} \/>/g)?.length, 2)
  assert.match(page, /<StrategyBoard data=\{data\} view=\{view\} cut=\{cut\} \/>/)
  assert.match(page, /jumpTimes=\{jumps\}/)
})

// ── 實際帳戶：多空各最多 1 口（T-454，使用者 2026-10-27：「多空都持有一口就好」→ 同時要空「A 優先」、「保留對照」） ──
const leg = (sleeve: Sleeve, entry_time: string, exit_time: string | null) => ({ sleeve, side: SIDE_OF[sleeve], entry_time, exit_time, trade: null })

test('T-454 AC1 多空各最多 1 口：先到先做；同一秒照 A、B、C、噪音區；同方向有部位時整筆跳過；同一秒出場又進場算空出來；多空各算各的', () => {
  const { taken, skipped } = pickLegs([
    leg('NOISE_SHORT', '09:22:01', '09:45:01'),
    leg('GAP_C', '09:22:01.000', '10:23:29.000'),
    leg('GAP_B', '09:22:01.000', '13:44:01.000'),
    leg('GAP_A', '09:22:01.000', '13:44:01.000'),
    leg('ORB_LONG', '08:50:01.000', '13:44:01.000'),
  ])
  assert.deepEqual(taken.map((l) => l.sleeve), ['ORB_LONG', 'GAP_A'])
  assert.deepEqual(skipped.map((l) => [l.sleeve, l.by]), [['GAP_B', 'GAP_A'], ['GAP_C', 'GAP_A'], ['NOISE_SHORT', 'GAP_A']])
  // 先到先做：噪音區 09:15 先空，A 09:22 就不做（之後也不補進）；噪音區 09:45:01 出場、同一秒 B 進場 → B 做
  const r = pickLegs([leg('GAP_A', '09:22:01', null), leg('NOISE_SHORT', '09:15:01', '09:45:01'), leg('GAP_B', '09:45:01', null)])
  assert.deepEqual(r.taken.map((l) => l.sleeve), ['NOISE_SHORT', 'GAP_B'])
  assert.deepEqual(r.skipped.map((l) => [l.sleeve, l.by]), [['GAP_A', 'NOISE_SHORT']])
  // 還沒出場的部位一直佔著
  assert.deepEqual(pickLegs([leg('GAP_A', '09:22:01', null), leg('NOISE_SHORT', '13:15:01', '13:44:01')]).skipped.map((l) => l.sleeve), ['NOISE_SHORT'])
})

test('T-454 AC2 那一天的帳戶：開盤突破做多 1 口；09:22 只空 A 那 1 口，B、C 與後來的噪音區空單都不做，寫進時間軸；對照照舊', () => {
  const d = gapDay()
  const v = dayAt(d, '23:59')
  assert.equal(v.account.total, Math.round(-9354.5) + Math.round(17947.4))
  assert.deepEqual(v.accountTrades.map((t) => t.sleeve), ['ORB_LONG', 'GAP_A'])
  assert.deepEqual(v.skipped.map((l) => [l.sleeve, l.by]), [['GAP_B', 'GAP_A'], ['GAP_C', 'GAP_A'], ['NOISE_SHORT', 'GAP_A']])
  assert.deepEqual(v.fills.map((f) => [f.sleeve, f.kind]), [['ORB_LONG', 'entry'], ['ORB_LONG', 'exit'], ['GAP_A', 'entry'], ['GAP_A', 'exit']])
  const skips = v.events.filter((e) => e.strategy === 'account')
  assert.deepEqual(skips.map((e) => e.time), ['09:22:01', '09:22:01', '11:15:01'])
  assert.equal(skips[0].text, 'B 跌破開盤區間要放空，但帳戶已經有空單（A 開高失守）→ 這筆不做')
  // 圖上的停損線只畫帳戶真的持有的那一口
  assert.deepEqual(gapLevels(d, d.gap_fail!, dayAt(d, '10:00').account).map((l) => l.title), ['開盤', '昨收', 'B 區間低', 'A 停損'])
  // 拉到 09:21：還沒有任何跳過（不劇透）
  assert.deepEqual(dayAt(d, '09:21').skipped, [])
  // 每條各 1 口的對照照舊
  assert.equal(v.gap!.total, Math.round(17947.4) * 2 + Math.round(10897.3))
})

test('T-454 AC3 多單、空單兩格：持有哪一條的 1 口、平倉幾筆、空手', () => {
  const d = gapDay()
  const at = (cut: string, side: 1 | -1) => accountSide(dayAt(d, cut).account, side)
  assert.deepEqual(at('08:45', 1), { value: 0, text: '空手' })
  assert.equal(at('09:00', 1).text, '持多 1 口・開盤突破・只做多 @ 47319')
  assert.equal(at('09:10', 1).text, '平倉 1 筆')
  assert.equal(at('10:00', -1).text, '持空 1 口・A 開高失守 @ 46479')
  assert.deepEqual(at('23:59', -1), { value: Math.round(17947.4), text: '平倉 1 筆' })
})

test('T-454 AC4 日曆「帳戶」：用那天兩邊已平倉的成交算多空各 1 口；只有行情的日子沒有金額', () => {
  const g = gapDay().gap_fail!
  const row = { day: '2026-10-27', total: -13303, gap_total: 46792.1, trades: day().trades, gap_trades: g.trades }
  assert.equal(accountPnl(row), -9354.5 + 17947.4)
  assert.equal(dayPnl(row, 'account'), -9354.5 + 17947.4)
  assert.equal(dayPnl({ day: '2026-10-22', total: 16092, gap_total: null, trades: day().trades, gap_trades: null }, 'account'), -9354.5)
  assert.equal(dayPnl({ day: '2026-09-10' }, 'account'), null)
  assert.deepEqual(monthSummary([row], '2026-10', 'account'), { total: Math.round(-9354.5 + 17947.4), days: 1, win: 1, loss: 0 })
  assert.deepEqual(CAL_VIEW_NAME, { account: '帳戶', combo: '固定組合', gap: '開高走低' })
})

test('T-454 AC5 畫面：數字卡是帳戶合計、多單、空單、最新；各策略標「每條各 1 口的對照」與帳戶沒做的；日曆預設看帳戶；日期端點多回兩邊的成交', () => {
  const page = read('src/cloud/FuturesPage.tsx')
  assert.match(page, /label="帳戶合計"/)
  assert.match(page, /<Kpi label="多單" value=\{yuan\(long\.value\)\}/)
  assert.match(page, /<Kpi label="空單" value=\{yuan\(short\.value\)\}/)
  assert.match(page, /title="各策略" meta="每條各 1 口的對照"/)
  assert.match(page, /帳戶沒做/)
  assert.match(page, /useState<CalView>\('account'\)/)
  for (const f of ['functions/api/futures/days.ts', 'functions/api/futures/quotes/days.ts'])
    assert.match(read(f), /payload->'trades' AS trades, payload->'gap_fail'->'trades' AS gap_trades/)
})
