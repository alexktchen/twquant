import assert from 'node:assert/strict'
import test from 'node:test'
import { crosses, kd, macd, sma, upDownVolume, volumeProfile, type Bar } from '../src/stock/indicators.ts'
import { categoryScores, coverageRows, factorCardFrom, poolSignals, technicalReadings } from '../src/stock/readings.ts'
import { resolveStockQuery } from '../src/stock/search.ts'
import { ohlcCsv } from '../src/stock/exporting.ts'

/** Deterministic bars: a drifting sine so highs, lows and volume all vary without randomness. */
function bars(n: number): Bar[] {
  return Array.from({ length: n }, (_, i) => {
    const c = 100 + i * 0.4 + 8 * Math.sin(i / 5)
    return { d: `2026-01-${String(i + 1).padStart(3, '0')}`, o: c - 0.5, h: c + 1.5, l: c - 2, c, v: 1000 + ((i * 37) % 11) * 100 }
  })
}

const near = (a: number | null, b: number, eps = 1e-9) => assert.ok(a !== null && Math.abs(a - b) < eps, `${a} vs ${b}`)

test('AC-240-1: SMA, KD and MACD match a hand calculation', () => {
  assert.deepEqual(sma([1, 2, 3, 4, 5], 3), [null, null, 2, 3, 4])
  // KD(3): RSV=(C-L3)/(H3-L3)*100, K=2/3*K'+1/3*RSV, D=2/3*D'+1/3*K, both seeded at 50.
  const b: Bar[] = [
    { d: 'a', o: 10, h: 12, l: 9, c: 11 }, { d: 'b', o: 11, h: 13, l: 10, c: 12 },
    { d: 'c', o: 12, h: 14, l: 11, c: 13 }, { d: 'd', o: 13, h: 13, l: 10, c: 10 },
  ]
  const out = kd(b, 3, 3)
  assert.equal(out[0], null)
  assert.equal(out[1], null)
  const rsv2 = ((13 - 9) / (14 - 9)) * 100
  const k2 = (2 / 3) * 50 + rsv2 / 3
  const d2 = (2 / 3) * 50 + k2 / 3
  near(out[2]!.k, k2)
  near(out[2]!.d, d2)
  const rsv3 = ((10 - 10) / (14 - 10)) * 100
  const k3 = (2 / 3) * k2 + rsv3 / 3
  near(out[3]!.k, k3)
  near(out[3]!.d, (2 / 3) * d2 + k3 / 3)
  // MACD(2,3,2) on a short series: EMA seeded with the first close, alpha = 2/(n+1).
  const m = macd([10, 11, 12, 13], 2, 3, 2)
  assert.equal(m[0], null)
  assert.equal(m[1], null)
  const ema = (xs: number[], n: number) => xs.reduce<number[]>((acc, x, i) => [...acc, i === 0 ? x : acc[i - 1] + (2 / (n + 1)) * (x - acc[i - 1])], [])
  const fast = ema([10, 11, 12, 13], 2), slow = ema([10, 11, 12, 13], 3)
  near(m[2]!.dif, fast[2] - slow[2])
  near(m[3]!.dif, fast[3] - slow[3])
  assert.equal(m[2]!.dea, null) // The signal line needs `signal` DIF values first.
  near(m[3]!.dea, (fast[2] - slow[2]) + (2 / 3) * ((fast[3] - slow[3]) - (fast[2] - slow[2])))
  near(m[3]!.osc, m[3]!.dif - m[3]!.dea!)
})

test('AC-240-1: indicators never look ahead — f(data[:t]) equals f(data)[:t]', () => {
  const all = bars(90)
  for (const t of [30, 61, 89]) {
    const head = all.slice(0, t)
    assert.deepEqual(kd(head), kd(all).slice(0, t), `kd@${t}`)
    assert.deepEqual(macd(head.map(x => x.c)), macd(all.map(x => x.c)).slice(0, t), `macd@${t}`)
    assert.deepEqual(sma(head.map(x => x.c), 20), sma(all.map(x => x.c), 20).slice(0, t), `sma@${t}`)
  }
  // Same input twice is bit-identical.
  assert.deepEqual(kd(all), kd(all))
  assert.deepEqual(volumeProfile(all), volumeProfile(all))
})

test('AC-240-1: crosses report direction and skip warm-up gaps', () => {
  const a = [null, 1, 3, 3, 1, 1]
  const b = [null, 2, 2, 2, 2, null]
  assert.deepEqual(crosses(a, b), [{ i: 2, kind: 'golden' }, { i: 4, kind: 'death' }])
})

test('AC-240-1: volume profile conserves volume and brackets the POC inside the value area', () => {
  const data = bars(60)
  const vp = volumeProfile(data)!
  const total = data.reduce((s, x) => s + (x.v ?? 0), 0)
  near(vp.vol.reduce((s, x) => s + x, 0), total, 1e-6)
  assert.ok(vp.val <= vp.poc && vp.poc <= vp.vah)
  assert.ok(vp.vol[vp.pocIndex] === Math.max(...vp.vol))
  assert.equal(volumeProfile(data.slice(0, 5)), null) // Too few bars: no levels rather than noisy ones.
  assert.equal(volumeProfile(data.map(x => ({ ...x, v: 0 }))), null)
})

test('AC-240-1: up/down volume compares each close with the previous close', () => {
  const b: Bar[] = [
    { d: 'a', o: 1, h: 1, l: 1, c: 10, v: 5 }, { d: 'b', o: 1, h: 1, l: 1, c: 11, v: 100 },
    { d: 'c', o: 1, h: 1, l: 1, c: 9, v: 40 }, { d: 'd', o: 1, h: 1, l: 1, c: 9, v: 7 },
  ]
  assert.deepEqual(upDownVolume(b, 3), { days: 3, upDays: 1, downDays: 1, flatDays: 1, upVol: 100, downVol: 40, flatVol: 7 })
  assert.equal(upDownVolume(b.slice(0, 1), 3), null)
})

test('AC-240-2: readings state missing inputs instead of inventing a value', () => {
  const short = technicalReadings({ bars: bars(30), brokerFlow: null, institutional: null, margin: null })
  const trend = short.find(r => r.key === 'trend')!
  assert.equal(trend.tone, 'muted')
  assert.match(trend.value, /資料不足/)
  assert.match(short.find(r => r.key === 'broker')!.value, /未載入/)
  // An empty series is a legitimate zero rows, worded differently from a failed read.
  const empty = technicalReadings({ bars: bars(30), brokerFlow: [], institutional: [], margin: [] })
  assert.match(empty.find(r => r.key === 'broker')!.value, /零筆/)
  const full = technicalReadings({
    bars: bars(120),
    brokerFlow: Array.from({ length: 10 }, (_, i) => ({ date: `d${i}`, net: 2000 })),
    institutional: Array.from({ length: 10 }, (_, i) => ({ date: `d${i}`, foreign_net: -3000, trust_net: 1000, dealer_net: 0, cum_foreign: 0, cum_trust: 0 })),
    margin: null,
  })
  assert.equal(full.find(r => r.key === 'broker')!.value, '+10 張')
  assert.equal(full.find(r => r.key === 'broker')!.tone, 'up')
  assert.equal(full.find(r => r.key === 'inst')!.value, '-20 張')
  assert.equal(full.find(r => r.key === 'inst')!.tone, 'down')
  assert.doesNotMatch(full.find(r => r.key === 'trend')!.value, /資料不足/) // 120 bars are enough for MA60.
  // The trend rule itself: a steady climb is 多頭排列, a steady slide is 空頭排列.
  const line = (step: number): Bar[] => Array.from({ length: 80 }, (_, i) => ({ d: `d${i}`, o: 100 + step * i, h: 101 + step * i, l: 99 + step * i, c: 100 + step * i, v: 1000 }))
  const trendOf = (b: Bar[]) => technicalReadings({ bars: b, brokerFlow: null, institutional: null, margin: null }).find(r => r.key === 'trend')!
  assert.equal(trendOf(line(1)).tone, 'up')
  assert.match(trendOf(line(1)).value, /多頭排列/)
  assert.equal(trendOf(line(-1)).tone, 'down')
  assert.match(trendOf(line(-1)).value, /空頭排列/)
})

test('AC-240-2: factor categories average only the factors that have a percentile', () => {
  const f = (factor: string, category: string, pct: number | null) => ({ factor, zh: factor, category, value: 1, z: 0, pct, direction: 1, signed_z: 0 })
  const scores = categoryScores([f('a', 'price', 0.8), f('b', 'price', 0.4), f('c', 'chip', null), f('d', 'fundamental', 0.5)])
  near(scores.find(s => s.category === 'price')!.value, 0.6)
  assert.equal(scores.find(s => s.category === 'chip')!.value, null) // No percentile → no score, never 0.
  assert.equal(scores.find(s => s.category === 'volume')!.value, null)
  assert.equal(scores.find(s => s.category === 'price')!.n, 2)
})

test('AC-240-2: pool signals list every pool and mark membership by string id', () => {
  const pool = (key: string, ids: string[], total = ids.length) => ({ key, name: key, rule: '', note: '', total, members: ids.map(stock_id => ({ stock_id, name: null, close: null, chg: null, yoy: null, conc: null, accum_ratio: null, streak: 3 })) })
  const signals = poolSignals([pool('main_accum', ['0050', '2330']), pool('main_distrib', ['2317'], 40)], '0050')
  assert.deepEqual(signals.map(s => [s.key, s.state]), [['main_accum', 'in'], ['main_distrib', 'unknown']])
  assert.equal(signals[0].streak, 3)
  // A truncated pool cannot prove absence; a complete one can.
  assert.equal(poolSignals([pool('main_distrib', ['2317'])], '0050')[0].state, 'out')
  assert.equal(poolSignals([pool('main_accum', ['50'])], '0050')[0].state, 'out') // "0050" is never coerced to 50.
})

test('AC-240-2: coverage rows keep failed, empty and loaded sources apart', () => {
  const rows = coverageRows([
    { label: '日 K', rows: 120, target: 120, asOf: '2026-09-18', live: true },
    { label: '法人', rows: 0, target: 120, asOf: null, live: true },
    { label: '融資券', rows: 0, target: 120, asOf: null, live: false },
  ])
  assert.deepEqual(rows.map(r => r.state), ['ok', 'empty', 'failed'])
  assert.equal(rows[0].ratio, 1)
  assert.equal(rows[2].ratio, null)
})

test('AC-240-3: stock search keeps leading zeroes and only resolves known or well-formed codes', () => {
  const meta = { '0050': { name: '元大台灣50' }, '2330': { name: '台積電' }, '00878': { name: '國泰永續高股息' } }
  assert.equal(resolveStockQuery('0050', meta), '0050')
  assert.equal(resolveStockQuery(' 0050 元大台灣50 ', meta), '0050')
  assert.equal(resolveStockQuery('台積電', meta), '2330')
  assert.equal(resolveStockQuery('00878', meta), '00878')
  assert.equal(resolveStockQuery('6488', {}), '6488') // Name map not loaded yet: a well-formed code still works.
  assert.equal(resolveStockQuery('不存在', meta), null)
  assert.equal(resolveStockQuery('../etc', meta), null)
  assert.equal(resolveStockQuery('', meta), null)
})

test('AC-240-3: the CSV export states the volume unit and never fills a missing volume with zero', () => {
  const csv = ohlcCsv([{ d: '2026-09-17', o: 10, h: 12, l: 9, c: 11, v: 15000 }, { d: '2026-09-18', o: 11, h: 11.5, l: 10, c: 10.5 }])
  assert.equal(csv, '\uFEFFdate,open,high,low,close,volume_shares\n2026-09-17,10,12,9,11,15000\n2026-09-18,11,11.5,10,10.5,\n')
})

test('AC-240-2: the factor card keeps only materialised factors and signs z by direction', () => {
  const options = [
    { factor: 'price.reversal_5d', zh: '5日短期反轉', direction: -1 },
    { factor: 'chip.trust_streak', zh: '投信連買天數', direction: 1 },
    { factor: 'fundamental.revenue_yoy', zh: '月營收 YoY', direction: 1 },
  ]
  const card = factorCardFrom([
    { factor: 'price.reversal_5d', series: [{ date: '2026-09-01', value: 0.1, z: 1, pct: 0.8 }, { date: '2026-09-02', value: 0.2, z: 1.5, pct: 0.9 }] },
    { factor: 'chip.trust_streak', series: [{ date: '2026-08-29', value: 3, z: null, pct: null }] },
  ], options)
  assert.deepEqual(card.factors.map(f => [f.factor, f.category, f.pct, f.signed_z]), [['price.reversal_5d', 'price', 0.9, -1.5], ['chip.trust_streak', 'chip', null, null]])
  assert.equal(card.as_of, '2026-09-02') // The latest materialised day across factors.
  // An ETF has no factor rows at all: an empty card, not a card of zeroes.
  assert.deepEqual(factorCardFrom([], options), { as_of: null, factors: [] })
})
