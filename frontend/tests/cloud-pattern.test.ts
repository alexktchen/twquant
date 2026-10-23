import assert from 'node:assert/strict'
import test from 'node:test'
import { BOX_WIDTH, BREAK_BUFFER, FLAG_MIN, POLE_MIN, WINDOW, pattern, type Ohlcv } from '../functions/_lib/pattern.ts'
import { BADGE_LEGEND, pickBadges } from '../src/cloud/badges.ts'
import { patternTag } from '../src/cloud/pattern.ts'

/** 從 2026-01-01 起的連續日期（測試只需要排序正確）。 */
const day = (i: number) => new Date(Date.UTC(2026, 0, 1 + i)).toISOString().slice(0, 10)
/** [high, low, close, volume] 或 null（停牌）。 */
type Row = [number, number, number, number] | null
const bars = (rows: Row[]): Ohlcv[] =>
  rows.map((r, i) => (r ? { date: day(i), high: r[0], low: r[1], close: r[2], volume: r[3] } : { date: day(i), high: null, low: null, close: null, volume: null }))
const last = (xs: Ohlcv[]) => xs[xs.length - 1].date
const flat = (n: number, hi: number, lo: number, vol = 1000): Row[] => Array.from({ length: n }, () => [hi, lo, (hi + lo) / 2, vol])

test('T-440 AC8a：箱型＝前 60 日高低差 12% 以內、今天收盤高出箱頂 0.5% 以上', () => {
  const box = bars([...flat(WINDOW, 100, 92), [104, 99, 103, 4000]])
  const p = pattern(box, [], last(box))
  assert.ok(p && p.kind === 'box')
  assert.equal(p.days, WINDOW)
  assert.ok(Math.abs(p.width - (100 / 92 - 1)) < 1e-9 && p.width <= BOX_WIDTH)
  assert.ok(Math.abs(p.over - 0.03) < 1e-9)
  // 箱體太寬就不是箱型（也不是旗型：沒有急漲）
  const wide = bars([...flat(WINDOW, 100, 80), [104, 99, 103, 4000]])
  assert.equal(pattern(wide, [], last(wide)), null)
})

test('T-440 AC8b：旗型＝急漲兩成以上（旗桿）、整理 5～40 天回檔不深且量縮（旗面）、今天突破前高', () => {
  // 20 天底部 80 → 10 天急漲到 100（量大）→ 15 天在 94～99 整理（量縮）→ 突破 101
  const rows: Row[] = [
    ...flat(20, 82, 80, 800),
    ...Array.from({ length: 10 }, (_, i): Row => [84 + 1.6 * (i + 1), 82 + 1.6 * i, 83 + 1.6 * (i + 1), 3000]),
    ...flat(15, 99, 94, 900),
  ]
  while (rows.length < WINDOW) rows.unshift([82, 80, 81, 800])
  const flag = bars([...rows, [102, 99, 101, 5000]])
  const p = pattern(flag, [], last(flag))
  assert.ok(p && p.kind === 'flag', JSON.stringify(p))
  assert.equal(p.days, 15)
  assert.ok(p.pole >= POLE_MIN)
  // 旗面量沒縮（比旗桿還大）→ 不算旗型；箱體又太寬 → null
  const loud = bars([...rows.map((r, i): Row => (i >= rows.length - 15 && r ? [r[0], r[1], r[2], 9000] : r)), [102, 99, 101, 5000]])
  assert.equal(pattern(loud, [], last(loud)), null)
  // 回檔太深（跌回旗桿一半以下）→ 不算
  const deep = bars([...rows.map((r, i): Row => (i >= rows.length - 15 && r ? [99, 85, 90, 900] : r)), [102, 99, 101, 5000]])
  assert.equal(pattern(deep, [], last(deep)), null)
  // 整理不滿 FLAG_MIN 天 → 不算
  const short: Row[] = [...flat(30, 82, 80, 800), ...Array.from({ length: 10 }, (_, i): Row => [84 + 1.6 * (i + 1), 82 + 1.6 * i, 83 + 1.6 * (i + 1), 3000]), ...flat(FLAG_MIN - 1, 99, 94, 900)]
  while (short.length < WINDOW) short.unshift([82, 80, 81, 800])
  const s = bars([...short, [102, 99, 101, 5000]])
  assert.equal(pattern(s, [], last(s)), null)
})

test('T-440 AC8c：沒突破、資料不夠、那天沒行情都不標；除權息跳空用還原價判', () => {
  const noBreak = bars([...flat(WINDOW, 100, 92), [100, 97, 100 * (1 + BREAK_BUFFER) - 0.01, 4000]])
  assert.equal(pattern(noBreak, [], last(noBreak)), null)
  const short = bars([...flat(WINDOW - 1, 100, 92), [104, 99, 103, 4000]])
  assert.equal(pattern(short, [], last(short)), null)
  const holes = bars([...flat(WINDOW, 100, 92), null, null, [104, 99, 103, 4000]])
  assert.ok(pattern(holes, [], last(holes))?.kind === 'box')
  const noToday = bars([...flat(WINDOW, 100, 92), null])
  assert.equal(pattern(noToday, [], last(noToday)), null)
  // 除息 10%：還原後前面的 100 變成 90，今天收 95 其實是突破；不還原會以為還在箱子裡
  const ex = bars([...flat(WINDOW, 100, 92), [96, 93, 95, 4000]])
  assert.equal(pattern(ex, [], last(ex)), null)
  assert.ok(pattern(ex, [{ date: last(ex), factor: 0.9 }], last(ex))?.kind === 'box')
})

test('T-440 AC8d：名單列的標記與圖示說明', () => {
  const box = patternTag({ kind: 'box', over: 0.037, width: 0.1, days: 60 })
  assert.ok(box && box.label === '箱型突破' && box.title.includes('只差 10%') && box.title.includes('+3.7%'))
  const flag = patternTag({ kind: 'flag', over: 0.02, pole: 0.25, days: 12 })
  assert.ok(flag && flag.label === '旗型突破' && flag.title.includes('急漲 25%') && flag.title.includes('整理 12 個交易日'))
  assert.equal(patternTag(null), null)
  const b = pickBadges({ pattern: { kind: 'flag', over: 0.02, pole: 0.25, days: 12 } })
  assert.deepEqual(b.map((x) => [x.icon, x.short, x.tone]), [['flag', '+2%', 'good']])
  assert.equal(pickBadges({ pattern: null }).length, 0)
  for (const icon of ['box', 'flag']) assert.ok(BADGE_LEGEND.some((l) => l.icon === icon))
})
