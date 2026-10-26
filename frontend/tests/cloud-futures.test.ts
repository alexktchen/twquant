// T-450／D-220：小台模擬分頁（盤中＋復盤）。使用者 2026-10-26：「這個可以在我網站即時模擬出來嗎？」「畫面可以是復盤模式跟盤中模式」
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { DAYS_LIMIT, NOT_READY, NO_SECONDS, dayOk } from '../functions/_lib/futures.ts'
import { FEATURE_ROLES, can } from '../functions/_lib/roles.ts'
import { FEATURES, lockedText, tabAllowed } from '../src/cloud/access.ts'
import {
  STALE_AFTER_S,
  addSeconds,
  chartTime,
  decodeSeconds,
  fillTimes,
  lastAtOrBefore,
  closeNowPnl,
  minuteOf,
  replayAt,
  shouldPoll,
  staleSeconds,
  taipeiNow,
  tone,
  yuan,
  type FuturesDay,
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
  assert.equal(lockedText('futures', false), '這個帳號沒有「小台模擬」的權限')
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
