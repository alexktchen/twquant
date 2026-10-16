import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { DROP_HORIZON, marketDrop, readDrop, readWeak, weakness, type Bar } from '../functions/_lib/weak.ts'
import { dropNote, weakSummary, weakTags } from '../src/cloud/weak.ts'

/** 從 2026-01-01 起的連續日期（測試只需要排序正確）。 */
const day = (i: number) => new Date(Date.UTC(2026, 0, 1 + i)).toISOString().slice(0, 10)
const bars = (closes: (number | null)[]): Bar[] => closes.map((close, i) => ({ date: day(i), close }))
const last = (xs: Bar[]) => xs[xs.length - 1].date

test('T-365 AC1：近 20 日新低（同價也算）與連跌天數（平盤就斷）', () => {
  const down4 = bars([...Array(20).fill(60), 58, 57, 56, 55])
  assert.deepEqual(weakness(down4, [], last(down4)), { low20: true, down: 4 })
  const flat = bars([...Array(20).fill(60), 56, 56, 55])
  assert.deepEqual(weakness(flat, [], last(flat)), { low20: true, down: 1 })
  const tie = bars([...Array(10).fill(60), 50, ...Array(10).fill(60), 55, 50])
  assert.deepEqual(weakness(tie, [], last(tie)), { low20: true, down: 2 })
  const notLow = bars([...Array(10).fill(60), 40, ...Array(10).fill(60), 55, 50])
  assert.deepEqual(weakness(notLow, [], last(notLow)), { low20: false, down: 2 })
  // 20 天以前更低不算：只比近 20 個交易日
  const old = bars([40, ...Array(19).fill(60), 58, 57])
  assert.deepEqual(weakness(old, [], last(old)), { low20: true, down: 2 })
})

test('T-365 AC2：資料不夠不判斷——有收盤的日子不滿 20 天 low20 是 null；那天沒有收盤兩個都是 null；0 與缺值當沒有收盤', () => {
  const short = bars([60, 59, 58, 57, 56])
  assert.deepEqual(weakness(short, [], last(short)), { low20: null, down: 4 })
  const full = bars([...Array(20).fill(60), 58])
  assert.deepEqual(weakness(full, [], day(30)), { low20: null, down: null })
  const holes = bars([...Array(20).fill(60), 0, null, 58])
  assert.deepEqual(weakness(holes, [], last(holes)), { low20: true, down: 1 })
  const noToday = bars([...Array(22).fill(60), null])
  assert.deepEqual(weakness(noToday, [], last(noToday)), { low20: null, down: null })
})

test('T-365 AC3：還原除權息——除息日的跳空不算破底、不算下跌；那天以後的除權息不影響', () => {
  // 除息 5%：參考價 95，當天收 95.5（還原後是小漲，不是跌 4.5%）
  const xs = bars([...Array(22).fill(100), 95.5])
  const ex = [{ date: last(xs), factor: 0.95 }]
  assert.deepEqual(weakness(xs, [], last(xs)), { low20: true, down: 1 }, '沒還原會誤判')
  assert.deepEqual(weakness(xs, ex, last(xs)), { low20: false, down: 0 })
  // 看前一天：除息還沒發生，不能拿來還原
  assert.deepEqual(weakness(xs.slice(0, -1), ex, day(21)), weakness(xs.slice(0, -1), [], day(21)))
  // 減資（因子 > 1）同樣照乘
  const cut = bars([...Array(22).fill(10), 19])
  assert.deepEqual(weakness(cut, [{ date: last(cut), factor: 2 }], last(cut)), { low20: true, down: 1 })
})

/** 每天 +0.1%，指定的日子改成指定的漲跌。 */
function index(n: number, moves: Record<number, number>): Bar[] {
  const c = [100]
  for (let i = 1; i < n; i++) c.push(c[i - 1] * (1 + (moves[i] ?? 0.001)))
  return bars(c)
}

test('T-365 AC4：加權跌不到 2% 或指數還沒有那天的收盤 → 不提醒', () => {
  const xs = index(80, { 79: -0.015 })
  assert.equal(marketDrop(xs, last(xs)), null)
  const big = index(80, { 79: -0.03 })
  assert.equal(marketDrop(big.slice(0, -1), last(big)), null)
  assert.equal(marketDrop([], day(0)), null)
})

test('T-365 AC5：大跌之後 20 天的統計——同一波只算第一天、還沒揭曉的不算、中位數與最差一次', () => {
  // 第 10 天跌 3%、第 15 天又跌 2.5%（同一波，不算）、第 40 天跌 2.1%、第 70 天跌 3%（之後 20 天還沒到 → 不算）、今天第 79 天跌 2.2%
  const xs = index(80, { 10: -0.03, 15: -0.025, 40: -0.021, 70: -0.03, 79: -0.022 })
  const c = xs.map((b) => b.close as number)
  const fwd = (i: number) => c[i + DROP_HORIZON] / c[i] - 1
  const got = marketDrop(xs, last(xs))
  assert.ok(got)
  assert.ok(Math.abs(got.ret - -0.022) < 1e-12)
  assert.equal(got.since, day(0))
  assert.equal(got.n, 2)
  assert.equal(got.up, 1)
  assert.ok(fwd(10) < 0 && fwd(40) > 0)
  assert.ok(Math.abs((got.median ?? NaN) - (fwd(10) + fwd(40)) / 2) < 1e-12)
  assert.deepEqual(got.worst, { date: day(10), ret: fwd(10) })
  const all = Array.from({ length: 80 - DROP_HORIZON }, (_, i) => fwd(i)).sort((a, b) => a - b)
  assert.ok(Math.abs((got.base ?? NaN) - (all[29] + all[30]) / 2) < 1e-12)
  // 第 15 天那次的「前 10 天」裡有第 10 天，所以不算；拉開到 11 天後就算
  const apart = marketDrop(index(80, { 10: -0.03, 21: -0.025, 79: -0.022 }), day(79))
  assert.equal(apart?.n, 2)
})

test('T-365 AC6：不偷看——看過去某天時，之後的行情不影響那天的統計；同一輸入兩次結果相同', () => {
  const xs = index(140, { 10: -0.03, 40: -0.021, 79: -0.022, 100: -0.05, 120: 0.04 })
  const past = marketDrop(xs, day(79))
  assert.deepEqual(past, marketDrop(xs.slice(0, 80), day(79)))
  assert.deepEqual(past, marketDrop(xs, day(79)))
  const later = marketDrop(xs, day(100))
  assert.equal(later?.n, 3, '第 79 天那次到第 100 天才揭曉 20 天')
})

function fakeDb(results: (() => Promise<unknown[]>)[], calls: string[]) {
  return ((strings: TemplateStringsArray, ...values: unknown[]) => {
    calls.push(strings.join('?') + JSON.stringify(values))
    const next = results.shift()
    return next ? next() : Promise.resolve([])
  }) as unknown as Parameters<typeof readWeak>[0]
}

test('T-365 AC7：讀資料庫——個股收盤與除權息一起讀、數字欄位是字串也照算；沒大跌只讀近兩週指數，大跌才讀全部歷史', async () => {
  const calls: string[] = []
  assert.equal((await readWeak(fakeDb([], calls), day(22), [])).size, 0)
  assert.equal(calls.length, 0, '沒有名單不查')
  const px = [...Array(22).fill(100), 95.5].map((close, i) => ({ stock_id: '2330', date: day(i), close: String(close) }))
  const got = await readWeak(
    fakeDb(
      [() => Promise.resolve(px), () => Promise.resolve([{ stock_id: '2330', date: day(22), factor: '0.95' }])],
      calls,
    ),
    day(22),
    ['2330', '0050'],
  )
  assert.deepEqual(got.get('2330'), { low20: false, down: 0 })
  assert.deepEqual(got.get('0050'), { low20: null, down: null })
  assert.ok(calls[0].includes('prices_daily') && calls[0].includes('"2330,0050"'))
  assert.ok(calls[1].includes('corporate_events') && calls[1].includes('adj_factor > 0'))

  const idx = index(80, { 10: -0.03, 79: -0.022 }).map((b) => ({ date: b.date, close: String(b.close) }))
  const quiet: string[] = []
  const calm = index(80, { 79: -0.01 }).map((b) => ({ date: b.date, close: b.close }))
  assert.equal(await readDrop(fakeDb([() => Promise.resolve(calm.slice(-10))], quiet), day(79)), null)
  assert.equal(quiet.length, 1)
  assert.ok(quiet[0].includes('index_daily') && quiet[0].includes("'TAIEX'") && quiet[0].includes('false'))
  const loud: string[] = []
  const drop = await readDrop(fakeDb([() => Promise.resolve(idx.slice(-10)), () => Promise.resolve(idx)], loud), day(79))
  assert.equal(loud.length, 2)
  assert.ok(loud[1].includes('true'))
  assert.equal(drop?.n, 1)
})

test('T-365 AC8：名單 API 每檔附 weak、整天附 drop', () => {
  const src = readFileSync(new URL('../functions/api/picks/index.ts', import.meta.url), 'utf8')
  assert.ok(src.includes('readWeak(db, target, sids)'))
  assert.ok(src.includes('readDrop(db, target)'))
  assert.ok(src.includes('weak: weak.get(r.stock_id) ?? null'))
  assert.ok(src.includes('drop, lines'))
})

test('T-365 AC9：名單上的小標與這條線的摘要——連跌 3 天以上才標；舊後端沒有這欄不顯示', () => {
  assert.deepEqual(
    weakTags({ low20: true, down: 4 }).map((t) => t.text),
    ['20日新低', '連跌4天'],
  )
  assert.deepEqual(weakTags({ low20: false, down: 2 }), [])
  assert.deepEqual(weakTags({ low20: null, down: null }), [])
  assert.deepEqual(weakTags(undefined), [])
  assert.ok(weakTags({ low20: true, down: 0 })[0].title.includes('已還原除權息'))
  const picks = [
    { weak: { low20: true, down: 4 } },
    { weak: { low20: true, down: 1 } },
    { weak: { low20: false, down: 3 } },
    { weak: null },
    {},
  ]
  assert.equal(weakSummary(picks), '5 檔裡 2 檔收在近 20 日新低、2 檔連跌 3 天以上')
  assert.equal(weakSummary([{ weak: { low20: true, down: 0 } }]), '1 檔裡 1 檔收在近 20 日新低')
  assert.equal(weakSummary([{ weak: { low20: false, down: 2 } }, {}]), null)
})

test('T-365 AC10：大跌提醒的用字——收起時一行（今天／這天、次數、中位數），點開看上漲次數、對照、最差一次；名單不刪減', () => {
  const d = {
    ret: -0.0341,
    since: '2014-06-03',
    n: 41,
    up: 26,
    median: 0.0266,
    base: 0.0138,
    worst: { date: '2020-03-09', ret: -0.077 },
  }
  const today = dropNote(d, true)
  assert.equal(today?.title, '加權指數今天 -3.4%・過去 41 次大跌後 20 天中位數 +2.7%')
  assert.equal(
    today?.body,
    '2014 年以來單日跌 2% 以上有 41 次（同一波下跌只算第一天），之後 20 個交易日有 26 次上漲，中位數 +2.7%（平常任一天 +1.4%）；最差一次是 2020-03-09 之後 -7.7%。名單照模型排名產生，不因大盤漲跌刪減；買不買、買多少請自己斟酌。',
  )
  assert.equal(dropNote(d, false)?.title, '加權指數這天 -3.4%・過去 41 次大跌後 20 天中位數 +2.7%')
  const none = dropNote({ ...d, n: 0, up: 0, median: null, worst: null }, true)
  assert.equal(none?.title, '加權指數今天 -3.4%，單日跌 2% 以上')
  assert.ok(none?.body.startsWith('指數資料裡還沒有可以對照的大跌'))
  assert.equal(dropNote(null, true), null)
  assert.equal(dropNote(undefined, true), null)
})
