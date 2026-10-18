import assert from 'node:assert/strict'
import test from 'node:test'
import { LIVE_NAME, barWidth, benchDiff, cashRatio, mddTimes, outcomeBuckets, segments, shapeVerdict, tone } from '../src/cloud/score-view.ts'

test('績效頁：報酬顏色、白話名稱、對照多少點、跌幅倍數、現金比例', () => {
  assert.equal(tone(0.03), 'up')
  assert.equal(tone(-0.03), 'down')
  assert.equal(tone(0), 'plain')
  assert.equal(tone(null), 'na')
  assert.equal(LIVE_NAME.live, '每天公布')
  assert.equal(LIVE_NAME.backfill, '回頭補算')
  assert.deepEqual(benchDiff(0.123, 0.159, '0050'), { win: false, text: '比 0050 少 3.6 點' })
  assert.deepEqual(benchDiff(0.123, 0.04, '00981A'), { win: true, text: '比 00981A 多 8.3 點' })
  assert.equal(benchDiff(0.1, null, '0050'), null)
  assert.equal(mddTimes(-0.352, -0.1537, '0050'), '是 0050 的 2.3 倍')
  assert.equal(mddTimes(-0.3, 0, '0050'), null)
  assert.equal(mddTimes(null, -0.1, '0050'), null)
  assert.equal(cashRatio(37, 100), 0.37)
  assert.equal(cashRatio(1, 0), null)
})

test('長條寬度照這一組最大的絕對值；沒有值是 0', () => {
  assert.equal(barWidth(0.2, [0.2, -0.1, null]), 100)
  assert.equal(barWidth(-0.1, [0.2, -0.1, null]), 50)
  assert.equal(barWidth(null, [0.2]), 0)
  assert.equal(barWidth(0, [0, 0]), 0)
})

test('每一檔的結果分四格：門檻用設定檔的大虧門檻，剛好 0 算小賠；沒有任何一筆時比例全 0', () => {
  const b = outcomeBuckets([0.4, 0.15, 0.01, 0, -0.15, -0.16], -0.15)
  assert.deepEqual(
    b.map((x) => [x.key, x.label, x.range, x.n]),
    [
      ['bigWin', '大賺', '+15% 以上', 1],
      ['win', '小賺', '0～+15%', 2],
      ['loss', '小賠', '−15%～0', 2],
      ['bigLoss', '大賠', '−15% 以下', 1],
    ],
  )
  assert.ok(Math.abs(b.reduce((s, x) => s + x.share, 0) - 1) < 1e-12)
  assert.ok(outcomeBuckets([], -0.15).every((x) => x.n === 0 && x.share === 0))
})

test('平均與排中間那檔正負不同時要講出來（大盤精選：平均 +0.37%、中間 −3.07%）', () => {
  assert.equal(shapeVerdict(0.0037, -0.0307), '多數賠錢，靠少數大賺把平均拉上來')
  assert.equal(shapeVerdict(0.1, 0.1), '多數檔賺錢')
  assert.equal(shapeVerdict(-0.01, 0.02), '多數小賺，但少數大賠把平均拉下來')
  assert.equal(shapeVerdict(-0.01, -0.02), '多數檔賠錢')
  assert.equal(shapeVerdict(null, 0.1), null)
})

test('時間軸：回頭補算與每天公布各佔多少', () => {
  assert.deepEqual(segments(100, 10), { backfill: 0.9, live: 0.1 })
  assert.deepEqual(segments(100, null), { backfill: 1, live: 0 })
  assert.deepEqual(segments(0, null), { backfill: 0, live: 0 })
})

test('淨值切兩段：每天公布從第一天收盤算起（那天的名單隔天才買）；兩段接起來等於整段', async () => {
  const { splitReturn } = await import('../src/cloud/score-view.ts')
  const nav = [
    { d: '2026-05-04', v: 100 },
    { d: '2026-10-07', v: 105 },
    { d: '2026-10-08', v: 110 },
    { d: '2026-10-16', v: 121 },
  ]
  const s = splitReturn(nav, '2026-10-08')
  assert.equal(s.backfillEnd, '2026-10-08')
  assert.ok(Math.abs((s.backfill ?? 0) - 0.1) < 1e-12)
  assert.ok(Math.abs((s.live ?? 0) - 0.1) < 1e-12)
  assert.ok(Math.abs((1 + (s.backfill ?? 0)) * (1 + (s.live ?? 0)) - 1.21) < 1e-12)
  const whole = splitReturn(nav, null)
  assert.ok(Math.abs((whole.backfill ?? 0) - 0.21) < 1e-12)
  assert.equal(whole.live, null)
  assert.equal(whole.backfillEnd, '2026-10-16')
  assert.deepEqual(splitReturn([], null), { backfill: null, live: null, backfillEnd: null })
})
