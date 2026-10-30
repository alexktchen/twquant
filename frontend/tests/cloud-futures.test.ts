// T-450／D-220：小台模擬分頁（盤中＋復盤）。使用者 2026-10-26：「這個可以在我網站即時模擬出來嗎？」「畫面可以是復盤模式跟盤中模式」
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { BIG_MIN_FLOOR, DAYS_LIMIT, NOT_READY, NO_FLOW, NO_SECONDS, QUOTES_NOT_READY, dayOk, flowProductOk, productOk, withEstimates } from '../functions/_lib/futures.ts'
import { FEATURE_ROLES, can } from '../functions/_lib/roles.ts'
import { FEATURES, lockedText, tabAllowed } from '../src/cloud/access.ts'
import {
  STALE_AFTER_S,
  addSeconds,
  BIG_THRESHOLDS,
  CAL_VIEW_NAME,
  CAL_VIEWS,
  DEFAULT_BIG_MIN,
  MTX_COST,
  SIDE_OF,
  STRATEGY_NAME,
  comboBook,
  SLEEVES,
  SLEEVE_TAG,
  allFillTimes,
  sideSummary,
  barsAt,
  bookTotals,
  change,
  chartMarks,
  chartTime,
  comboSummary,
  compactYuan,
  dayAt,
  dayPnl,
  costOf,
  isEstimate,
  isPaper,
  decodeSeconds,
  fillTimes,
  flowAt,
  lastAtOrBefore,
  lots,
  mergeEvents,
  msBook,
  msKpis,
  msStopAt,
  msSub,
  REASON_TEXT,
  monthSummary,
  monthWeeks,
  monthsOf,
  closeNowPnl,
  minuteOf,
  replayAt,
  shouldPoll,
  staleSeconds,
  taipeiNow,
  tone,
  yuan,
  type Flow,
  type FuturesDay,
  type FuturesTrade,
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
  assert.ok(!('NOISE_SHORT' in open.sleeves), 'T-464 起噪音區不在畫面上')
  const closed = replayAt(day(), '09:10')
  assert.deepEqual(closed.sleeves.ORB_LONG, { position: 0, entry_price: null, realized: -9354, open_pnl: null }) // Math.round(-9354.5) → -9354
  assert.deepEqual(closed.fills.map((f) => f.kind), ['entry', 'exit'])
  // T-464：舊資料裡還沒回補的噪音區空單不顯示、不算進合計
  const short = replayAt(day(), '11:29')
  assert.equal(short.total, -9354)
  assert.ok(short.fills.every((f) => f.sleeve !== 'NOISE_SHORT'))
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
  assert.equal(replayAt(tx, '09:00').sleeves.ORB_LONG.open_pnl, Math.round(closeNowPnl(1, 47319, 47210, costOf(tx)))) // T-464 起看晨光突破那筆
})

test('T-451 AC6 大台／微台那天有模擬單就用小台同一個畫面；前面不足 14 天只有行情', () => {
  assert.ok(isPaper(day()))
  const quoteOnly = { version: 1 as const, day: '2026-09-10', product: 'TX' as const, name: '大台', source: 'taifex_replay' as const, status: 'closed' as const, contract: '202609', symbol: 'TXFI6', updated_at: '2026-09-10T13:50:00+08:00', last_tick: '13:45:00', last_price: 1, prev_close: null, open: 1, volume: 1, bars: [] }
  assert.ok(!isPaper(quoteOnly))
  const page = read('src/cloud/FuturesPage.tsx')
  assert.match(page, /\{data && isPaper\(data\) && <PaperBody /)
  assert.match(page, /\{data && !isPaper\(data\) && at && \(/)
  // 日期清單有模擬單的日子多合計與一致與否（同小台）
  assert.match(read('functions/api/futures/quotes/days.ts'), /\(payload->>'total'\)::float8\) AS total/) // T-464 起先看 ORB_LONG，沒有才用 total
})

// T-452 開高走低的測試：T-461（使用者 2026-10-29：「開高走低策略直接拿掉」）策略拿掉，一起拿掉（DECISIONS D-220 修訂八）。

// T-453 AC1（兩個策略同一個畫面）原本用開高走低的那一天，T-461 拿掉；網友原版與固定組合同一個畫面見 T-456 AC3、AC4。

test('T-453 AC2 各策略一句話：持多／持空幾口、平倉幾筆（T-461 起只剩固定組合）', () => {
  const d = day()
  assert.equal(comboSummary(dayAt(d, '08:45').combo), '空手')
  assert.equal(comboSummary(dayAt(d, '09:00').combo), '持多 1 口')
  assert.equal(comboSummary(dayAt(d, '09:10').combo), '平倉 1 筆')
  assert.equal(comboSummary(dayAt(d, '23:59').combo), '平倉 1 筆') // T-464：噪音區拿掉，收盤只剩晨光突破那一筆

})

test('T-453 AC3 日曆：週一到週五一週一列；格子看單一策略；只有行情的日子沒有金額；月小結（T-461 起只有網友原版與固定組合）', () => {
  const oct = monthWeeks('2026-10')
  assert.equal(oct.length, 5)
  assert.deepEqual(oct[0], [null, null, null, '2026-10-01', '2026-10-02'])
  assert.deepEqual(oct[4], ['2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30'])
  // 2026-11-01 是星期日：第一列從 11-02 星期一開始；11-30 星期一自己一列
  const nov = monthWeeks('2026-11')
  assert.deepEqual(nov[0], ['2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06'])
  assert.deepEqual(nov[nov.length - 1], ['2026-11-30', null, null, null, null])
  const r = { day: '2026-10-27', total: -13303, ms_total: 1500 }
  assert.equal(dayPnl(r, 'combo'), -13303)
  assert.equal(dayPnl({ day: '2026-09-10' }, 'combo'), null) // 只有行情
  const rows = [
    r,
    { day: '2026-10-21', total: -2155 },
    { day: '2026-10-20', total: 0 },
    { day: '2026-10-14', total: 0 },
    { day: '2026-09-30', total: 5000 },
    { day: '2026-09-10' },
  ]
  assert.deepEqual(monthSummary(rows, '2026-10', 'combo'), { total: -13303 - 2155, days: 4, win: 0, loss: 2 })
  assert.deepEqual(monthSummary(rows, '2026-09', 'combo'), { total: 5000, days: 1, win: 1, loss: 0 })
  assert.deepEqual(monthsOf(rows), ['2026-09', '2026-10'])
  assert.equal(compactYuan(46792.1), '+4.7萬')
  assert.equal(compactYuan(-13123), '-1.3萬')
  assert.equal(compactYuan(183308), '+18.3萬')
  assert.equal(compactYuan(-3278), '-3,278')
  assert.equal(compactYuan(0), '0')
  assert.equal(dayPnl(r, 'ms'), 1500)
  assert.equal(dayPnl({ day: '2026-10-20', total: 0 }, 'ms'), null) // 那天沒有網友原版
})

test('T-453 AC4 畫面：不再切策略；復盤上面是每日損益日曆（取代下拉）；各策略列出每個子帳戶', () => {
  const page = read('src/cloud/FuturesPage.tsx')
  assert.doesNotMatch(page, /cq-fu-strategy|STRATEGIES|<DayPick/)
  assert.equal(page.match(/<PnlCalendar days=\{days\} value=\{replayDay\} onPick=\{setPick\} \/>/g)?.length, 2)
  assert.match(page, /<StrategyBoard view=\{view\} \/>/) // T-461 起不需要 data、cut
  assert.match(page, /jumpTimes=\{jumps\}/)
})

// T-454 實際帳戶（多空各 1 口）：T-461 拿掉開高走低後帳戶就等於固定組合，一起拿掉（DECISIONS D-220 修訂八）。

// ── 盤口與大單（T-455，使用者 2026-10-28：「可以讓我在前端介面看到錄製的資料嗎」「做成可切換的」） ──
/** 2026-10-28 開盤那幾分鐘的縮寫（fubon_live.py 的 Flow.snapshot）：買賣價判主動方向、5 口以上記大單（集合競價那一筆不記）、每分鐘最後一份五檔。 */
function flow(): Flow {
  return {
    version: 1,
    day: '2026-10-28',
    product: 'MTX',
    big_min: 5,
    minutes: [
      ['08:45', 1200, 800, 100],
      ['08:46', 300, 500, 20],
      ['08:47', 400, 250, 10],
      ['08:48', 150, 150, 0],
    ],
    buy: 2050,
    sell: 1700,
    big: [
      ['08:45:12', 46945, 12, 1],
      ['08:45:40', 46910, 7, -1],
      ['08:46:05', 46900, 30, -1],
      ['08:47:30', 46960, 20, 1],
      ['08:48:10', 46950, 5, 0],
    ],
    book: { time: '08:48:59', bids: [[46950, 3], [46949, 8]], asks: [[46951, 2], [46952, 11]] },
    books: [
      ['08:45', [[46940, 1], [46939, 2]], [[46945, 2], [46948, 6]]],
      ['08:46', [[46905, 4], [46904, 1]], [[46906, 3], [46907, 9]]],
      ['08:47', [[46955, 2], [46954, 5]], [[46958, 1], [46960, 4]]],
      ['08:48', [[46950, 3], [46949, 8]], [[46951, 2], [46952, 11]]],
    ],
  }
}

test('T-455 AC1 拉到某一刻的盤口：主動買賣只算那之前的分鐘、秒級時還沒走完的那一分鐘不算；大單照門檻與時間篩；五檔用那一分鐘之前最後一份', () => {
  const f = flow()
  const v = flowAt(f, '08:46', 10)
  assert.deepEqual([v.buy, v.sell, v.delta], [1500, 1300, 200])
  assert.deepEqual(v.minutes, [['08:45', 400], ['08:46', -200]])
  assert.deepEqual(v.big.map((b) => b[2]), [12, 30]) // 7 口不到門檻、08:47 之後的還沒發生
  assert.deepEqual(v.book, { time: '08:46', bids: [[46905, 4], [46904, 1]], asks: [[46906, 3], [46907, 9]] })
  // 秒級：08:46:30 → 08:46 那一分鐘還沒走完，不算；五檔用 08:45 的；大單比到秒
  const sec = flowAt(f, '08:46:30', 5)
  assert.deepEqual(sec.minutes, [['08:45', 400]])
  assert.deepEqual(sec.big.map((b) => b[0]), ['08:45:12', '08:45:40', '08:46:05'])
  assert.equal(sec.book!.time, '08:45')
  assert.deepEqual(flowAt(f, '23:59', 30).big.map((b) => b[2]), [30])
  assert.deepEqual([flowAt(f, '23:59', 5).buy, flowAt(f, '23:59', 5).sell], [2050, 1700])
})

test('T-455 AC1 盤中（沒有每分鐘五檔）：拉到最新才給最新一份五檔；復盤沒拉到開盤 → 什麼都沒有', () => {
  const live = { ...flow(), books: null }
  assert.deepEqual(flowAt(live, '23:59', 5).book, flow().book)
  assert.equal(flowAt(live, '08:46', 5).book, null)
  assert.equal(flowAt(flow(), '08:44', 5).book, null)
  assert.deepEqual(flowAt(flow(), '08:44', 5).minutes, [])
})

test('T-455 AC2 五檔合計與買盤佔比；口數寫法；門檻可切 5／10／20／30、預設照商品', () => {
  assert.deepEqual(bookTotals(flow().book), { bid: 11, ask: 13, bidShare: 11 / 24 })
  assert.equal(bookTotals(null), null)
  assert.equal(bookTotals({ time: 'x', bids: [], asks: [] }), null)
  assert.equal(lots(1234.4), '1,234 口')
  assert.deepEqual([...BIG_THRESHOLDS], [5, 10, 20, 30])
  assert.deepEqual(DEFAULT_BIG_MIN, { MTX: 10, TX: 10, TMF: 20 })
  assert.equal(BIG_MIN_FLOOR, 5)
})

test('T-455 AC3 端點：盤口另走 /api/futures/flow（只給 admin、先驗商品、不進暫存；盤中不給每分鐘五檔、大單先照門檻篩）；原本的整份狀態拿掉 flow', () => {
  const src = read('functions/api/futures/flow.ts')
  assert.match(src, /\(await denied\(ctx\.request, 'futures', ctx\.env\)\) \?\? handle\(ctx\)/)
  assert.doesNotMatch(src, /cache|s-maxage/)
  assert.match(src, /flowProductOk\(product\)/)
  assert.match(src, /jsonb_path_query_array\(f->'big', '\$\[\*\] \? \(@\[2\] >= \$m\)'/)
  assert.match(src, /CASE WHEN \$\{live\}::boolean THEN NULL ELSE f->'books' END/)
  assert.ok(flowProductOk('MTX') && flowProductOk('TX') && flowProductOk('TMF') && !flowProductOk('MXF') && !flowProductOk(null))
  assert.match(NO_FLOW, /2026-10-28/)
  for (const f of ['functions/api/futures/index.ts', 'functions/api/futures/quotes/index.ts']) assert.match(read(f), /\(payload - 'flow'\)::text AS body/)
  assert.match(read('src/cloud/api.ts'), /\/api\/futures\/flow\?product=\$\{product\}&min=\$\{min\}\$\{live \? '&live=1' : ''\}/)
})

test('T-455 AC4 畫面：「盤口與大單」是收起的區塊、展開才抓；模擬單畫面與只有行情的畫面都有；門檻可切', () => {
  const page = read('src/cloud/FuturesPage.tsx')
  assert.match(page, /<Section icon=\{ArrowUpDown\} title="盤口與大單" fold summary=\{summary\} onToggle=\{setOpen\}>/)
  assert.match(page, /if \(!open\) return\r?\n/)
  assert.equal(page.match(/<FlowSection product=/g)?.length, 2)
  assert.match(page, /BIG_THRESHOLDS\.map\(\(k\) => \(/)
  assert.match(read('src/cloud/Section.tsx'), /onToggle\?: \(open: boolean\) => void/)
})

test('盤中重抓：分頁在背景就停（省 Supabase 流量，2026-10-29），切回前景立刻補抓', () => {
  const page = read('src/cloud/FuturesPage.tsx')
  assert.match(page, /document\.addEventListener\('visibilitychange', on\)/)
  // 模擬單／行情（useLive）與盤口與大單（FlowSection）兩個輪詢都要看前景
  assert.match(page, /if \(!enabled \|\| !visible\) return/)
  assert.match(page, /\}, \[enabled, visible, refresh\]\)/)
  assert.match(page, /if \(live && !visible\) return/)
  assert.match(page, /\}, \[open, live, visible, product, day, min, refresh\]\)/)
})

// ── 網友原版（T-456，使用者 2026-10-28：「先幫我加網友的策略去模擬單，還有復盤」） ──
/** 2026-10-22 網友原版用期交所逐筆重播的兩筆（studies/ms_tmf 研究版同一天的結果，一筆不差）；第二筆還在持有的版本給盤中用。 */
function msDay(holding = false): FuturesDay {
  const d = day()
  const t1: FuturesTrade = {
    day: '2026-10-22', sleeve: 'MS_LONG', side: 1, signal_time: '09:16:01.500', entry_time: '09:16:02.000', entry_price: 47242,
    exit_time: '09:17:33.000', exit_price: 47187, exit_reason: 'EMA_TREND_BREAK', points: -55, net_pnl: -2904, source: 'live',
    stop_price: 47180, setup: '趨勢・初次進場',
  }
  const t2: FuturesTrade = {
    ...t1, signal_time: '09:31:44.800', entry_time: '09:31:45.000', entry_price: 47174, exit_time: '09:34:28.000', exit_price: 47090,
    points: -84, net_pnl: -4354, stop_price: 47120,
  }
  const events = [
    { time: '09:16:01', kind: 'signal' as const, sleeve: 'MS_LONG' as const, text: '趨勢・初次進場訊號 → 模擬買進 1 口' },
    { time: '09:16:02', kind: 'fill' as const, sleeve: 'MS_LONG' as const, text: '模擬買進 47242，停損 47180' },
    { time: '09:17:33', kind: 'exit' as const, sleeve: 'MS_LONG' as const, text: '模擬賣出 47187（EMA 死叉），這筆淨 -2,904 元' },
    { time: '09:31:45', kind: 'fill' as const, sleeve: 'MS_LONG' as const, text: '模擬買進 47174，停損 47120' },
  ]
  if (!holding) events.push({ time: '09:34:28', kind: 'exit', sleeve: 'MS_LONG', text: '模擬賣出 47090（EMA 死叉），這筆淨 -4,354 元' })
  d.ms = {
    version: 1,
    name: '網友原版',
    commit: '1dea51b',
    sleeves: { MS_LONG: holding ? { position: 1, entry_price: 47174, realized: -2904, open_pnl: -500 } : { position: 0, entry_price: null, realized: -7258, open_pnl: null } },
    open: holding ? { entry_time: '09:31:45', entry_price: 47174, stop: 47120, setup: '趨勢・初次進場' } : null,
    trades: holding ? [t1] : [t1, t2],
    events,
    signals: 2,
    total: holding ? -3404 : -7258,
    official: null,
    official_match: null,
  }
  return d
}

test('T-456 AC1 網友原版是第三個策略：舊資料沒有 → null、畫面寫「這一天的資料沒有」；有的話只有一個只做多的子帳戶', () => {
  assert.deepEqual(STRATEGY_NAME, { combo: '晨光突破', ms: '動能順勢' }) // T-461 拿掉開高走低；T-464 改名
  assert.equal(msBook(day()), null)
  assert.equal(dayAt(day(), '23:59').ms, null)
  const b = msBook(msDay())!
  assert.deepEqual(b.sleeves, ['MS_LONG'])
  assert.equal(SIDE_OF.MS_LONG, 1)
  assert.match(read('src/cloud/FuturesPage.tsx'), /這一天的資料沒有（10-28 晚上之後上傳的才有）/)
})

test('T-456 AC2 復盤：拉到某一刻只算那之前的成交；持倉用「現在平倉的話」、平倉後變已實現；一句話照那一刻', () => {
  const d = msDay()
  assert.equal(msSub(dayAt(d, '09:00').ms!), '等動能或趨勢訊號')
  const holding = dayAt(d, '09:17:00', null).ms!
  assert.equal(holding.sleeves.MS_LONG.position, 1)
  assert.equal(holding.sleeves.MS_LONG.entry_price, 47242)
  assert.equal(holding.sleeves.MS_LONG.open_pnl, Math.round(closeNowPnl(1, 47242, 47170)))
  assert.match(msSub(holding), /^持多 1 口 @ 47242（現在平倉 /)
  const after = dayAt(d, '09:20:00').ms!
  assert.deepEqual(after.sleeves.MS_LONG, { position: 0, entry_price: null, realized: -2904, open_pnl: null })
  assert.equal(msSub(after), '已平倉 1 筆，這邊 -2,904')
  assert.equal(dayAt(d, '23:59').ms!.total, -7258)
  assert.equal(msSub(dayAt(d, '13:30').ms!), '已平倉 2 筆，這邊 -7,258')
  // 一整天都沒進場
  const none = msDay()
  none.ms = { ...none.ms!, trades: [], events: [], sleeves: { MS_LONG: { position: 0, entry_price: null, realized: 0, open_pnl: null } }, total: 0 }
  assert.equal(msSub(dayAt(none, '13:30').ms!), '今天沒有進場')
})

test('T-456 AC2 盤中還沒賣出的那一筆：trades 裡沒有，從 sleeves 補上，成交時間到了才出現；停損線只在真的持有時畫', () => {
  const d = msDay(true)
  const before = dayAt(d, '09:31:00').ms!
  assert.equal(before.sleeves.MS_LONG.position, 0)
  assert.equal(msStopAt(d.ms!, before), null)
  const now = dayAt(d, '09:32:00').ms!
  assert.equal(now.sleeves.MS_LONG.position, 1)
  assert.equal(now.sleeves.MS_LONG.entry_price, 47174)
  assert.equal(msStopAt(d.ms!, now), 47120)
  // 復盤拉到第一筆持有的時候：畫第一筆的停損
  assert.equal(msStopAt(msDay().ms!, dayAt(msDay(), '09:17:00').ms!), 47180)
  assert.equal(msStopAt(msDay().ms!, dayAt(msDay(), '09:20:00').ms!), null)
})

test('T-456 AC3 網友原版和固定組合各算各的：固定組合的數字不變；圖上的買賣點多出標「網友」的', () => {
  const plain = dayAt(day(), '23:59')
  const v = dayAt(msDay(), '23:59')
  assert.equal(v.combo.total, plain.combo.total)
  assert.equal(v.fills.length, plain.fills.length + 4)
  const marks = chartMarks(v.fills, true).filter((m) => m.text.startsWith('動能'))
  assert.deepEqual(marks.map((m) => m.text), ['動能 買 47242', '動能 賣 47187', '動能 買 47174', '動能 賣 47090']) // T-464 標「動能」
  // 買賣點照時間排：09:05 固定組合停損在網友 09:16 之前
  const times = v.fills.map((f) => f.time.slice(0, 8))
  assert.deepEqual(times, [...times].sort())
})

test('T-456 AC4 時間軸與上一個／下一個買賣點：網友原版的事件標「ms」照時間排進來；同一秒排在固定組合之後', () => {
  const v = dayAt(msDay(), '23:59')
  assert.deepEqual(v.events.filter((e) => e.strategy === 'ms').map((e) => e.time), ['09:16:01', '09:16:02', '09:17:33', '09:31:45', '09:34:28'])
  const tie = mergeEvents([{ time: '09:00:00', kind: 'info', sleeve: null, text: 'a' }], [{ time: '09:00:00', kind: 'info', sleeve: 'MS_LONG', text: 'c' }])
  assert.deepEqual(tie.map((e) => e.strategy), ['combo', 'ms'])
  assert.deepEqual(allFillTimes(msDay()), ['08:50:01', '09:05:41', '09:16:02', '09:17:33', '09:31:45', '09:34:28']) // T-464：噪音區 11:15 那筆不算
  assert.match(read('src/cloud/FuturesPage.tsx'), /if \(e\.strategy === 'ms'\) return \{ text: STRATEGY_NAME\.ms, cls: 'ms' \}/)
  assert.match(read('src/cloud/FuturesPage.tsx'), /className=\{`cq-cat cq-fu-tag-\$\{chip\.cls\}`\}/) // T-464 做成標籤
})

test('T-456 AC5 日曆可以只看網友原版；兩支日期端點多回網友原版的合計；出場原因有中文', () => {
  assert.equal(CAL_VIEW_NAME.ms, '動能順勢') // T-464 改名
  assert.equal(dayPnl({ day: '2026-10-22', ms_total: -7258 }, 'ms'), -7258)
  assert.equal(dayPnl({ day: '2026-10-21', total: 100 }, 'ms'), null)
  for (const f of ['functions/api/futures/days.ts', 'functions/api/futures/quotes/days.ts'])
    assert.match(read(f), /\(payload->'ms'->>'total'\)::float8 AS ms_total/)
  for (const k of ['EMA_TREND_BREAK', 'HIGHER_LOW_BREAK', 'MOMENTUM_DECAY_PARTIAL', 'TIME_EXIT', 'EMERGENCY_STOP']) assert.ok(REASON_TEXT[k], k)
})

test('T-456 AC6 畫面：各策略多一段網友原版、另有收起的「網友原版成交明細」、規則說明寫明不併進帳戶與一分 K 偏差', () => {
  const page = read('src/cloud/FuturesPage.tsx')
  assert.match(page, /head\(`\$\{STRATEGY_NAME\.ms\}（主）`, view\.ms \? view\.ms\.total : null\)/) // T-460
  assert.match(page, /row\('MS_LONG', 'long', view\.ms\.sleeves\.MS_LONG, msSub\(view\.ms\)\)/)
  assert.match(page, /title="成交明細（動能順勢）"/) // T-460：主策略的成交不收起
  assert.match(page, /它和晨光突破各算各的/) // T-461 改寫、T-464 改名
  assert.match(page, /title: '動能 停損'/) // T-464
  // 上方的「現在發生什麼」也標出是網友原版（它的文字本身沒寫策略名）
  assert.match(page, /\{last\.strategy === 'ms' && <span className="cq-cat cq-fu-tag-ms">\{STRATEGY_NAME\.ms\}/) // T-464 做成標籤
  assert.match(read('src/cloud/futures.css'), /\.cq-fu-sleeve\.ms \{/)
})

// ── T-460：台指期以網友原版為主（使用者 2026-10-29：「把台指期的策略都以網友為主，金額賺賠也都以網友的算，其他策略都是輔助顯示」） ──
test('T-460 AC1 數字卡：有網友原版的日子是網友原版合計、持倉、已平倉、最新；沒有的舊日子看固定組合（T-461）', () => {
  const page = read('src/cloud/FuturesPage.tsx')
  assert.match(page, /label="動能順勢合計"/)
  assert.match(page, /<Kpi label="持倉" value=\{mk\.hold\.value\}/)
  assert.match(page, /<Kpi label="已平倉" value=\{`\$\{mk\.exits\} 筆`\}/)
  assert.ok(page.indexOf('label="動能順勢合計"') < page.indexOf('label="晨光突破合計"'), '網友原版在前，固定組合是沒有網友原版時的退路')
  const hold = msKpis(dayAt(msDay(true), '09:32:00').ms!)
  assert.equal(hold.hold.text, '持多 1 口 @ 47174・現在平倉的話')
  assert.equal(hold.exits, 1)
  assert.equal(hold.realized, -2904)
  const flat = msKpis(dayAt(msDay(), '23:59').ms!)
  assert.deepEqual([flat.hold.value, flat.hold.text, flat.exits, flat.realized], ['空手', '13:00 後不再進場', 2, -7258])
  assert.equal(msKpis(dayAt(msDay(), '09:10:00').ms!).hold.text, '等動能或趨勢訊號')
})

test('T-460 AC2 圖上：網友原版的買賣點彩色、其他策略的灰色；同一點有網友原版就不算輔助；沒有主策略時都照舊', () => {
  const v = dayAt(msDay(), '23:59')
  const marks = chartMarks(v.fills, true, 'MS_LONG')
  assert.ok(marks.filter((m) => m.text.startsWith('動能')).every((m) => !m.aux))
  assert.ok(marks.filter((m) => !m.text.startsWith('動能')).every((m) => m.aux))
  assert.ok(chartMarks(v.fills, true).every((m) => !m.aux))
  const tie = chartMarks(
    [
      { time: '09:00:00', sleeve: 'ORB_LONG', side: 1, price: 100, kind: 'entry' },
      { time: '09:00:00', sleeve: 'MS_LONG', side: 1, price: 100, kind: 'entry' },
    ],
    true,
    'MS_LONG',
  )
  assert.deepEqual(tie.map((m) => [m.text, m.aux]), [['晨光・動能 買 100', false]])
  assert.match(read('src/cloud/FuturesPage.tsx'), /primary=\{ms \? 'MS_LONG' : null\}/)
})

test('T-460 AC3 其他地方：日曆網友原版排第一且預設；各策略網友原版在最上、下面標輔助；收盤重算看網友原版的；其他策略的成交收起', () => {
  assert.deepEqual(CAL_VIEWS, ['ms', 'combo']) // T-461 拿掉帳戶與開高走低
  const page = read('src/cloud/FuturesPage.tsx')
  assert.ok(page.indexOf('head(`${STRATEGY_NAME.ms}（主）`') < page.indexOf('head(STRATEGY_NAME.combo'))
  assert.match(page, /以下是輔助對照/)
  assert.match(page, /const match = ms \? ms\.official_match : data\.official_match/)
  assert.match(page, /title=\{ms \? '晨光突破成交（輔助）' : '成交明細（晨光突破）'\}\s*fold/) // T-461
  assert.match(page, /以網友原版為主/)
})

// ── T-461：開高走低拿掉（使用者 2026-10-29：「開高走低策略直接拿掉」）；帳戶（固定組合＋開高走低，多空各 1 口）因此等於固定組合，一起拿掉 ──
test('T-461 AC1 只剩網友原版與固定組合：日曆兩個、損益照各自；那一刻的畫面沒有開高走低與帳戶', () => {
  assert.deepEqual(CAL_VIEW_NAME, { ms: '動能順勢', combo: '晨光突破' })
  const v = dayAt(msDay(), '23:59')
  assert.deepEqual(Object.keys(v).sort(), ['bars', 'combo', 'cut', 'events', 'fills', 'ms', 'price'])
  assert.deepEqual([...new Set(v.events.map((e) => e.strategy))].sort(), ['combo', 'ms'])
  // 沒有網友原版的舊日子：數字卡用固定組合的多、空兩邊
  const old = dayAt(day(), '23:59')
  assert.deepEqual(sideSummary(old.combo, 1), { value: -9354, text: '平倉 1 筆' }) // 已實現照畫面取整數
  assert.equal(sideSummary(old.combo, -1).text, '空手') // T-464：噪音區拿掉後空單那邊永遠空手
})

test('T-461 AC2 畫面與端點不再有開高走低：沒有開高徽章、水平線、規則段落；日期端點不回 gap_total 與帳戶用的成交', () => {
  // 只看程式與畫面文字（註解裡留著拿掉的歷史）
  const page = read('src/cloud/FuturesPage.tsx').split('\n').filter((l) => !/^\s*(\/\/|\*|\/\*\*)/.test(l)).join('\n')
  assert.doesNotMatch(page, /開高走低|gap_fail|gapLevels|gapSub|accountSide|帳戶合計|帳戶沒做/)
  assert.match(page, /label="晨光突破合計"/)
  for (const f of ['functions/api/futures/days.ts', 'functions/api/futures/quotes/days.ts']) {
    const src = read(f)
    assert.doesNotMatch(src, /gap_fail|AS gap_total|AS trades|AS gap_trades/, f)
    assert.match(src, /\(payload->'ms'->>'total'\)::float8 AS ms_total/, f)
  }
})

// ── T-462：網友原版往前半年的 1 分 K 估算（使用者 2026-10-29：「可以把台指期的復盤往前半年的賺賠都標出來嗎？我只要網友的策略」） ──
test('T-462 AC1 日期清單併進估算：有逐筆模擬單的日子優先（估算不蓋過真實）、新到舊、最多 limit 天、估算的標 ms_est', () => {
  const real = [
    { day: '2026-10-28', source: 'fubon_live', status: 'closed', total: 100, match: true, ms_total: 244 },
    { day: '2026-10-27', source: 'taifex_replay', status: 'closed', total: -50, match: true, ms_total: null },
  ]
  const est = [
    { day: '2026-10-27', total: -999 },
    { day: '2026-05-04', total: 1200 },
    { day: '2026-04-28', total: -300 },
  ]
  const days = withEstimates(real, est, 3)
  assert.deepEqual(days.map((d) => [d.day, d.ms_total, d.ms_est ?? false]), [
    ['2026-10-28', 244, false],
    ['2026-10-27', null, false], // 有真實資料（即使那天沒有網友原版）就不拿估算
    ['2026-05-04', 1200, true],
  ])
  assert.equal(days[2].source, 'minute_estimate')
  assert.equal(days[2].total, null) // 估算的日子沒有固定組合
  assert.ok(isEstimate(days[2]) && !isEstimate(days[0]) && !isEstimate(undefined))
})

test('T-462 AC2 端點：/api/futures/estimate 只給 admin、先驗商品與日期；兩支日期清單讀估算表（表不在也照舊）', () => {
  const src = read('functions/api/futures/estimate.ts')
  assert.match(src, /export const onRequestGet: PagesFunction<Env> = async \(ctx\) => \(await denied\(ctx\.request, 'futures', ctx\.env\)\) \?\? handle\(ctx\)/)
  assert.ok(src.indexOf('flowProductOk(product)') < src.indexOf('guarded('))
  assert.ok(src.indexOf('dayOk(day)') < src.indexOf('guarded('))
  assert.ok(!src.includes('cached('))
  for (const f of ['functions/api/futures/days.ts', 'functions/api/futures/quotes/days.ts']) {
    const d = read(f)
    assert.match(d, /FROM serving\.futures_ms_estimate WHERE product = /, f)
    assert.match(d, /withEstimates\(real, est, DAYS_LIMIT\)/, f)
    assert.equal((d.match(/readRows\(/g) ?? []).length, 2, `${f}：兩個查詢分開，估算表還沒建時不影響真實的日子`)
  }
  assert.match(read('functions/api/futures/days.ts'), /product = 'MTX'/)
})

test('T-462 AC3 畫面：日曆估算的格子標「估」；小台、大台、微台選到估算的日子都顯示估算（不抓整份狀態）、寫明偏悲觀', () => {
  const page = read('src/cloud/FuturesPage.tsx')
  assert.match(page, /\{isEstimate\(row\) && <em className="cq-fu-est">估<\/em>\}/)
  assert.equal((page.match(/const estDay = mode === 'replay' && isEstimate\(days\?\.find\(\(r\) => r\.day === replayDay\)\)/g) ?? []).length, 2)
  assert.match(page, /<EstimateDay product="MTX" day=\{replayDay\} \/>/)
  assert.match(page, /<EstimateDay product=\{product\} day=\{replayDay\} \/>/)
  assert.equal((page.match(/if \(mode !== 'replay' \|\| replayDay === null \|\| estDay\) return/g) ?? []).length, 2)
  assert.match(page, /1 分 K 估算/)
  assert.match(page, /每筆大約少算 10 點/)
  assert.match(read('src/cloud/futures.css'), /\.cq-fu-est \{/)
})

// ── T-464（使用者 2026-10-30：「可以寫進場是為什麼嗎，網友策略改名字變好聽點的，然後做成標籤，然後把噪音區拿掉」） ──
test('T-464 AC1 噪音區拿掉：固定組合只剩晨光突破；舊日子資料裡的噪音區事件、成交一律不顯示、不算進合計；圖上沒有下緣線', () => {
  assert.deepEqual(SLEEVES, ['ORB_LONG'])
  const v = dayAt(day(), '23:59')
  assert.ok(v.events.every((e) => e.sleeve !== 'NOISE_SHORT'))
  assert.ok(v.fills.every((f) => f.sleeve !== 'NOISE_SHORT'))
  assert.equal(v.combo.total, -9354)
  assert.ok(comboBook(day()).trades.every((t) => t.sleeve === 'ORB_LONG'))
  const page = read('src/cloud/FuturesPage.tsx')
  assert.match(page, /const done = comboBook\(data\)\.trades/) // 收起的晨光突破成交不含舊的噪音區
  for (const f of ['functions/api/futures/days.ts', 'functions/api/futures/quotes/days.ts'])
    assert.match(read(f), /payload->'sleeves'->'ORB_LONG'->>'realized'/, f) // 日曆的晨光突破不含舊的噪音區
  assert.doesNotMatch(page, /noiseLb|noise_lb|噪音區下緣/)
  assert.match(page, /噪音區放空 2026-10-30 起不再顯示也不再記/)
})

test('T-464 AC2 改名與標籤：網友原版在畫面上叫「動能順勢」（資料鍵 ms／MS_LONG 不變）、圖上標「動能」；時間軸與提示列的策略名是 badge', () => {
  assert.equal(STRATEGY_NAME.ms, '動能順勢')
  assert.equal(STRATEGY_NAME.combo, '晨光突破')
  assert.equal(SLEEVE_TAG.MS_LONG, '動能')
  assert.equal(SLEEVE_TAG.ORB_LONG, '晨光') // 使用者 2026-10-30：「開盤突破策略也改名」
  const page = read('src/cloud/FuturesPage.tsx')
  assert.match(page, /<span className="cq-cat cq-fu-tag-ms">\{STRATEGY_NAME\.ms\}<\/span>/)
  assert.match(read('src/cloud/futures.css'), /\.cq-fu-tag-ms \{ --c:/)
  assert.doesNotMatch(page.replace(/\/\/[^\n]*|\/\*[\s\S]*?\*\//g, ''), /網友原版[^（]/) // 畫面文字裡不再叫網友原版（括號裡註明出處可以）
})
