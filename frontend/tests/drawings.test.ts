import assert from 'node:assert/strict'
import test from 'node:test'
import type { Bar } from '../src/stock/indicators.ts'
import { DRAW, SCORE, boxes, channelOffset, confluenceZones, fibLevels, keyBars, lineAt, openGaps, pivots, priorHighLow, regressionChannel, srLevels, structureScore, trendLines } from '../src/stock/drawings.ts'

/** Deterministic bars: a drifting sine so swing highs and lows recur at known places. */
function bars(n: number, drift = 0.4): Bar[] {
  return Array.from({ length: n }, (_, i) => {
    const c = 100 + i * drift + 8 * Math.sin(i / 5)
    return { d: `2026-01-${String(i + 1).padStart(3, '0')}`, o: c - 0.5, h: c + 1.5, l: c - 2, c, v: 1000 }
  })
}
const fromCloses = (cs: number[]): Bar[] => cs.map((c, i) => ({ d: `d${String(i).padStart(3, '0')}`, o: c, h: c, l: c, c }))

test('AC-321-1: pivots mark strict left / non-strict right extremes, a plateau only once', () => {
  const b = fromCloses([1, 2, 5, 5, 2, 1, 0, 1, 2])
  assert.deepEqual(pivots(b, 2), [{ i: 2, kind: 'high', price: 5 }, { i: 6, kind: 'low', price: 0 }])
})

test('AC-321-1: pivots of a prefix are exactly the confirmed pivots of the full series (no lookahead)', () => {
  const all = bars(120)
  for (const span of [DRAW.pivotSpan.short, DRAW.pivotSpan.long]) {
    const full = pivots(all, span)
    for (const t of [30, 57, 90, 119]) {
      assert.deepEqual(pivots(all.slice(0, t), span), full.filter((p) => p.i + span < t), `span ${span} @${t}`)
    }
  }
  assert.deepEqual(pivots(all, 3), pivots(all, 3))
})

test('AC-321-2: an uptrend yields an unbroken rising support line that no bar between the anchors pierces', () => {
  const b = bars(120)
  const sup = trendLines(b, DRAW.pivotSpan.short).find((t) => t.kind === 'support')!
  assert.ok(sup, 'support line found')
  assert.ok(sup.slope > 0)
  assert.equal(sup.brokenAt, null)
  assert.ok(sup.touches >= 2)
  for (let i = sup.a.i; i <= sup.b.i; i++) assert.ok(b[i].l >= lineAt(sup, i) * (1 - DRAW.touchTol), `bar ${i}`)
})

test('AC-321-2: a close through the support line reports where it broke', () => {
  const b = bars(100)
  const clean = trendLines(b, DRAW.pivotSpan.short).find((t) => t.kind === 'support')!
  const k = b.length - 1
  const crash = lineAt(clean, k) * 0.9
  const broken = [...b.slice(0, k), { ...b[k], o: crash, h: crash, l: crash, c: crash }]
  const lines = trendLines(broken, DRAW.pivotSpan.short)
  const same = lines.find((t) => t.kind === 'support' && t.a.i === clean.a.i && t.b.i === clean.b.i)
  // Either the same line is now flagged broken at the crash bar, or an unbroken line was preferred.
  if (same) assert.equal(same.brokenAt, k)
  else assert.equal(lines.find((t) => t.kind === 'support')!.brokenAt, null)
})

test('AC-321-3: horizontal levels cluster repeated swings and split at the last close', () => {
  // A box range: highs repeatedly near 110, lows repeatedly near 100, last close in the middle.
  const cs: number[] = []
  for (let r = 0; r < 5; r++) cs.push(100, 102, 105, 108, 110, 108, 105, 102)
  cs.push(100, 103, 105)
  const lv = srLevels(fromCloses(cs), 2)
  const res = lv.find((l) => l.kind === 'resistance')!
  const sup = lv.find((l) => l.kind === 'support')!
  assert.ok(Math.abs(res.price - 110) < 1e-9 && res.touches >= 4)
  assert.ok(Math.abs(sup.price - 100) < 1e-9 && sup.touches >= 3)
  assert.ok(lv.filter((l) => l.kind === 'support').length <= DRAW.levelsPerSide)
})

test('AC-321-4: regression channel recovers a straight line exactly and needs enough bars', () => {
  const line = fromCloses(Array.from({ length: 30 }, (_, i) => 50 + 0.5 * i))
  const ch = regressionChannel(line)!
  assert.ok(Math.abs(ch.slope - 0.5) < 1e-9 && Math.abs(ch.mid0 - 50) < 1e-9 && ch.width < 1e-9)
  assert.equal(regressionChannel(line.slice(0, DRAW.channelMinBars - 1)), null)
})

test('AC-321-5: the parallel channel passes through the farthest opposite extreme and bounds every bar', () => {
  const b = bars(120)
  const sup = trendLines(b, DRAW.pivotSpan.short).find((t) => t.kind === 'support')!
  const off = channelOffset(b, sup)
  assert.ok(off > 0)
  let touched = false
  for (let i = sup.a.i; i < b.length; i++) {
    const top = lineAt(sup, i) + off
    assert.ok(b[i].h <= top + 1e-9, `bar ${i} above channel`)
    if (Math.abs(b[i].h - top) < 1e-9) touched = true
  }
  assert.ok(touched)
})

test('AC-321-6: boxes find a flat consolidation after a trend and respect the range limit', () => {
  const cs = [...Array.from({ length: 20 }, (_, i) => 50 + i * 2), ...Array.from({ length: 25 }, (_, i) => 90 + 3 * Math.sin(i))]
  const bx = boxes(fromCloses(cs))
  assert.ok(bx.length >= 1)
  const last = bx[bx.length - 1]
  assert.equal(last.i1, cs.length - 1)
  assert.ok(last.i0 >= 18 && last.i0 <= 21, `box starts near the top of the rally, got ${last.i0}`)
  assert.ok((last.top - last.bottom) / last.bottom <= DRAW.boxMaxRange)
  assert.ok(last.i1 - last.i0 + 1 >= DRAW.boxMinBars)
  assert.deepEqual(boxes(fromCloses(cs.slice(0, 20))), []) // A pure trend is not a box.
})

test('AC-321-7: Fibonacci levels measure the pullback from the high of an up leg, the bounce from the low of a down leg', () => {
  const upLeg = fromCloses([...Array.from({ length: 30 }, (_, i) => 100 + i), 120])
  const f = fibLevels(upLeg)!
  assert.ok(f.up && f.hi === 129 && f.lo === 100)
  assert.ok(Math.abs(f.levels.find((l) => l.ratio === 0.5)!.price - 114.5) < 1e-9)
  const down = fibLevels(fromCloses([...Array.from({ length: 30 }, (_, i) => 130 - i), 110]))!
  assert.ok(!down.up && Math.abs(down.levels.find((l) => l.ratio === 0.382)!.price - (101 + 0.382 * 29)) < 1e-9)
  assert.equal(fibLevels(fromCloses([5, 5, 5])), null)
})

test('AC-321-8: gaps are listed until a later bar trades through them', () => {
  const b: Bar[] = [
    { d: 'a', o: 10, h: 10, l: 9.8, c: 10 },
    { d: 'b', o: 10.5, h: 10.8, l: 10.4, c: 10.6 }, // up gap [10, 10.4]
    { d: 'c', o: 10.6, h: 10.9, l: 10.5, c: 10.7 },
  ]
  assert.deepEqual(openGaps(b), [{ i: 1, up: true, top: 10.4, bottom: 10 }])
  assert.deepEqual(openGaps([...b, { d: 'd', o: 10.5, h: 10.6, l: 9.9, c: 10 }]), []) // Filled, and this bar opens no new gap.
  const tiny = [b[0], { ...b[1], l: 10.05 }]
  assert.deepEqual(openGaps(tiny), []) // Below the minimum gap size.
})

test('AC-321-9: key bars need both a volume surge over the prior average and a real body', () => {
  const b: Bar[] = Array.from({ length: 25 }, (_, i) => ({ d: `d${i}`, o: 100, h: 101, l: 99, c: 100.5, v: 1000 }))
  b[22] = { d: 'd22', o: 100, h: 106, l: 99.5, c: 105, v: 3000 }
  b[23] = { d: 'd23', o: 100, h: 101, l: 99, c: 100.5, v: 5000 } // Surge without a body: not a key bar.
  const kb = keyBars(b)
  assert.equal(kb.length, 1)
  assert.ok(kb[0].i === 22 && kb[0].up && kb[0].high === 106 && kb[0].low === 99.5)
  assert.deepEqual(keyBars(b.map((x) => ({ ...x, v: undefined }))), [])
})

test('AC-321-10: prior high / low are the most recent confirmed swings on each side of the last close', () => {
  const b = fromCloses([10, 12, 15, 12, 10, 8, 10, 13, 11, 9, 11])
  const { high, low } = priorHighLow(b, 2)
  assert.deepEqual(high, { i: 7, kind: 'high', price: 13 })
  assert.deepEqual(low, { i: 5, kind: 'low', price: 8 })
})

test('AC-321-2: a line is not extended more than trendMaxExtend times its anchor span', () => {
  for (const n of [60, 120, 200]) {
    for (const span of [DRAW.pivotSpan.short, DRAW.pivotSpan.long]) {
      for (const tl of trendLines(bars(n), span)) assert.ok(n - 1 - tl.b.i <= DRAW.trendMaxExtend * (tl.b.i - tl.a.i))
    }
  }
})

test('AC-321-11: confluence picks the zone where the most lines agree on each side, nearest on a tie', () => {
  const items = [
    { price: 110, label: 'a' }, { price: 110.5, label: 'b' }, { price: 111, label: 'c' }, // 3 lines above
    { price: 104, label: 'd' }, { price: 104.2, label: 'e' }, // 2 lines above, nearer
    { price: 95, label: 'f' }, { price: 95.5, label: 'g' }, { price: 90, label: 'h' }, { price: 90.3, label: 'i' },
    { price: 150, label: 'far' }, { price: 150.1, label: 'far2' }, // beyond the range: ignored
  ]
  const z = confluenceZones(items, 100)
  assert.deepEqual(z.above!.labels, ['a', 'b', 'c'])
  assert.deepEqual(z.below!.labels, ['f', 'g']) // Tie on count: the nearer zone wins.
  assert.deepEqual(confluenceZones([{ price: 101, label: 'lonely' }], 100), { above: null, below: null })
})

test('AC-321-12: structure score maps each item to 0-20 and totals to a percentage with a grade', () => {
  const best = structureScore({ longTag: '偏多', shortTag: '偏多', where: '靠下軌', support: { lines: 6, dist: -0.03 }, up: 0.08 })
  assert.equal(best.total, 100)
  assert.equal(best.pct, 1)
  assert.equal(best.grade, '結構偏多')
  const worst = structureScore({ longTag: '偏空', shortTag: '轉弱', where: '跌出軌道下方', support: null, up: null })
  assert.deepEqual(worst.items.map((i) => i.score), [0, 4, 0, 0, 0])
  assert.equal(worst.grade, '結構偏空')
  // Support far away is discounted; a 1:1 reward/risk earns half the space score.
  const mid = structureScore({ longTag: '收斂', shortTag: '不明', where: null, support: { lines: 5, dist: -0.12 }, up: 0.12 })
  assert.deepEqual(mid.items.map((i) => i.score), [10, 10, 10, 8, 10])
  for (const it of [...best.items, ...worst.items, ...mid.items]) assert.ok(it.score >= 0 && it.score <= SCORE.perItem)
})
