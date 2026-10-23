import assert from 'node:assert/strict'
import test from 'node:test'
import { TL_PAST, buildTimeline, tlAlpha, tlBinLabel, tlBinMax, tlDayTicks, tlHubCounts } from '../src/cloud/galaxy-timeline.ts'
import type { GalaxyItem } from '../src/cloud/news-galaxy.ts'

const item = (id: string, t: string, kind: 'tw' | 'intl' = 'tw', m: string[] = []): GalaxyItem => ({ id, x: 0, y: 0, z: 0, k: 0, title: id, src: 's', kind, tr: false, t, url: '', ev: null, m, ml: [] })

// 快照 10/22 10:30Z ＝ 台北 18:30 → 最後一格 18–21 時、終點台北 21:00 ＝ 13:00Z
const BUILT = '2026-10-22T10:30:00Z'
const ITEMS = [
  item('a', '2026-10-22T10:00:00Z', 'tw', ['tw:2330']), // 台北 18:00 → 最後一格
  item('b', '2026-10-22T09:59:00Z', 'intl', ['tw:2330', 'us:NVDA']), // 台北 17:59 → 倒數第二格
  item('c', '2026-10-16T00:00:00Z', 'tw', ['us:NVDA']), // 台北 10/16 08:00 → 第 2 格（10/15 21:00 起算）
  item('d', '2026-10-01T00:00:00Z', 'intl'), // 早於範圍 → 夾到第 0 格
  item('e', '2026-10-23T00:00:00Z', 'tw'), // 晚於快照 → 夾到最後一格
]

// T-443 AC1：切成 7 天 × 8 格、格界對齊台北時間；每則剛好落在一格、台灣／國際分開數
test('T-443 AC1 buildTimeline：格數、格界、每則落點', () => {
  const tl = buildTimeline(ITEMS, BUILT, 7)
  assert.equal(tl.bins.length, 56)
  assert.equal(tl.end, Date.parse('2026-10-22T13:00:00Z'))
  assert.equal(tl.start, tl.end - 56 * 3 * 3600_000)
  assert.equal(new Date(tl.start + 8 * 3600_000).getUTCHours() % 3, 0)
  assert.deepEqual(tl.of, [55, 54, 3, 0, 55])
  assert.deepEqual(tl.bins.map((b) => b.tw + b.intl).reduce((s, n) => s + n, 0), ITEMS.length)
  assert.equal(tl.bins[55].tw, 2)
  assert.equal(tl.bins[55].intl, 0)
  assert.equal(tl.bins[54].intl, 1)
  assert.deepEqual(tl.bins[55].idx, [0, 4])
  assert.equal(tlBinMax(tl.bins), 2)
  assert.equal(tlBinMax([]), 1)
})

// T-443 AC2：拉到某一格——之後的光點不畫；「累積」之前的全亮，「當時」之前的淡掉
test('T-443 AC2 tlAlpha：累積與當時', () => {
  assert.equal(tlAlpha(10, 5, 'cum'), 0)
  assert.equal(tlAlpha(5, 5, 'cum'), 1)
  assert.equal(tlAlpha(2, 5, 'cum'), 1)
  assert.equal(tlAlpha(10, 5, 'at'), 0)
  assert.equal(tlAlpha(5, 5, 'at'), 1)
  assert.equal(tlAlpha(2, 5, 'at'), TL_PAST)
})

// T-443 AC3：亮點的大小只算到那時候為止被幾則提到；還沒出現的不在表裡
test('T-443 AC3 tlHubCounts：累積到游標', () => {
  const tl = buildTimeline(ITEMS, BUILT, 7)
  const atStart = tlHubCounts(ITEMS, tl.of, 3)
  assert.equal(atStart.get('us:NVDA'), 1)
  assert.equal(atStart.get('tw:2330'), undefined)
  const atEnd = tlHubCounts(ITEMS, tl.of, 55)
  assert.equal(atEnd.get('tw:2330'), 2)
  assert.equal(atEnd.get('us:NVDA'), 2)
})

// T-443 AC4：格的名字與每天的刻度都用台北時間
test('T-443 AC4 tlBinLabel／tlDayTicks：台北時間', () => {
  assert.equal(tlBinLabel(Date.parse('2026-10-22T10:00:00Z')), '10/22 18–21時')
  assert.equal(tlBinLabel(Date.parse('2026-10-22T13:00:00Z')), '10/22 21–24時')
  const tl = buildTimeline(ITEMS, BUILT, 7)
  const ticks = tlDayTicks(tl)
  assert.equal(ticks.length, 7)
  assert.deepEqual(ticks[0], { i: 1, label: '10/16' })
  assert.deepEqual(ticks[6], { i: 49, label: '10/22' })
})
