import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { BIG_LEVEL, HOLDERS_WEEKS, STREAK_MIN, holdersFlag, holdersSeries, holdersStreak } from '../functions/_lib/holders.ts'
import { STREAK_MIN as UI_STREAK_MIN, holdersTag, ppText, weekChange } from '../src/cloud/holders.ts'

const raw = (date: string, big: number | null, total: number | null, holders = 120) => ({ date, big, total, big_holders: holders })

test('T-386 AC-1：每週一列、舊到新；比例＝千張大戶股數 ÷ 全部分級合計；合計 0 或缺值 → null', () => {
  const s = holdersSeries([raw('2026-09-18', 420, 1000), raw('2026-09-04', 400, 1000), raw('2026-09-11', 410, 0), raw('2026-08-28', null, 1000)])
  assert.deepEqual(
    s.map((w) => [w.date, w.pct]),
    [
      ['2026-08-28', null],
      ['2026-09-04', 0.4],
      ['2026-09-11', null],
      ['2026-09-18', 0.42],
    ],
  )
  assert.equal(s[1].big_holders, 120)
  assert.equal(BIG_LEVEL, 15, '第 15 級＝1,000 張以上')
  assert.ok(HOLDERS_WEEKS >= 8)
})

test('T-386 AC-2：連增連減——嚴格遞增算 +n、遞減算 −n，持平或缺值就斷；只有一週是 0', () => {
  const up = holdersSeries([raw('2026-08-28', 38, 100), raw('2026-09-04', 39, 100), raw('2026-09-11', 40, 100), raw('2026-09-18', 42, 100)])
  assert.equal(holdersStreak(up), 3)
  const down = holdersSeries([raw('2026-09-04', 40, 100), raw('2026-09-11', 39, 100), raw('2026-09-18', 38, 100)])
  assert.equal(holdersStreak(down), -2)
  const flat = holdersSeries([raw('2026-09-04', 40, 100), raw('2026-09-11', 40, 100), raw('2026-09-18', 41, 100)])
  assert.equal(holdersStreak(flat), 1, '持平那週斷掉')
  const gap = holdersSeries([raw('2026-09-04', 38, 100), raw('2026-09-11', 39, 0), raw('2026-09-18', 41, 100)])
  assert.equal(holdersStreak(gap), 0, '缺值斷掉')
  assert.equal(holdersStreak(holdersSeries([raw('2026-09-18', 41, 100)])), 0)
  assert.equal(holdersStreak([]), 0)
})

test('T-386 AC-3：名單摘要——最新一週比例、對前一週變化、連增連減、週數；最新一週算不出來 → null', () => {
  const s = holdersSeries([raw('2026-09-04', 40, 100), raw('2026-09-11', 41, 100), raw('2026-09-18', 42, 100)])
  const f = holdersFlag(s)
  assert.deepEqual(f, { latest: '2026-09-18', pct: 0.42, change: 0.42 - 0.41, streak: 2, weeks: 3 })
  assert.equal(holdersFlag([]), null)
  assert.equal(holdersFlag(holdersSeries([raw('2026-09-18', 42, 0)])), null)
  assert.equal(holdersFlag(holdersSeries([raw('2026-09-18', 42, 100)]))?.change, null, '只有一週沒有變化')
})

test('T-386 AC-4：名單小標——連增／連減 3 週以上才有；文字與說明；個股頁變化欄', () => {
  assert.equal(STREAK_MIN, UI_STREAK_MIN)
  assert.equal(holdersTag(null), null)
  assert.equal(holdersTag({ latest: '2026-09-18', pct: 0.42, change: 0.01, streak: 2, weeks: 5 }), null, '兩週不標')
  const up = holdersTag({ latest: '2026-09-18', pct: 0.42, change: 0.004, streak: 3, weeks: 5 })
  assert.equal(up?.text, '大戶連增3週')
  assert.match(up!.title, /連續 3 週增加.*（09\/18）42\.0%.*\+0\.4 個百分點/)
  assert.match(up!.title, /名單不因此刪減/)
  const down = holdersTag({ latest: '2026-09-18', pct: 0.3, change: -0.012, streak: -4, weeks: 8 })
  assert.equal(down?.text, '大戶連減4週')
  assert.match(down!.title, /−1\.2 個百分點/)
  assert.equal(ppText(0.0002), '持平')
  assert.equal(ppText(null), '')
  const weeks = holdersSeries([raw('2026-09-11', 40, 100), raw('2026-09-18', 42, 100)])
  assert.equal(weekChange(weeks, 1), '+2.0 個百分點')
  assert.equal(weekChange(weeks, 0), '')
})

test('T-386 AC-5（靜態）：名單只用早於名單那天的週（週六才公布，不偷看）；個股頁端點存在；名單列與個股頁都有接', () => {
  const lib = readFileSync(new URL('../functions/_lib/holders.ts', import.meta.url), 'utf8')
  assert.ok(lib.includes('serving.shareholding_dispersion'))
  assert.match(lib, /readRows\(\(\) => GROUPED\(db, sids\.join\(','\), date, true\)\)/, '名單用 strict（date < 名單日）')
  assert.match(lib, /date < \$\{until\}::date/)
  const api = readFileSync(new URL('../functions/api/stocks/[sid]/holders.ts', import.meta.url), 'utf8')
  assert.ok(api.includes('SID_RE.test(sid)') && api.includes('readHolderWeeks(db, sid, today)'))
  const picks = readFileSync(new URL('../functions/api/picks/index.ts', import.meta.url), 'utf8')
  assert.ok(picks.includes('readHolders(db, target, sids)') && picks.includes('holders: holders.get(r.stock_id) ?? null'))
  const app = readFileSync(new URL('../src/cloud/CloudApp.tsx', import.meta.url), 'utf8')
  // 2026-10-18 改版：名單列的標記統一由 badges.ts 產生，App 用 pickBadges
  assert.ok(app.includes('pickBadges(') && readFileSync(new URL('../src/cloud/badges.ts', import.meta.url), 'utf8').includes('holdersTag(p.holders)') && app.includes('<StockFacts sid={sid} refresh={refresh} />'))
})
