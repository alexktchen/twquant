import assert from 'node:assert/strict'
import test from 'node:test'
import { readSimilar } from '../functions/_lib/similar.ts'
import { exampleText, similarBar, similarText, spanText, type Similar } from '../src/cloud/similar.ts'

const sim = (over: Partial<Similar>): Similar => ({
  n: 200,
  median: 0.028,
  mean: 0.031,
  win_rate: 0.552,
  big_loss_rate: 0.06,
  p10: -0.14,
  p90: 0.21,
  from: '2018-03-05',
  to: '2025-11-20',
  examples: [],
  ...over,
})

test('D-182：一行文字含中位數、上漲比例、大賠比例、最差一成；沒筆數寫沒有可比的情境', () => {
  assert.equal(similarText(sim({})), '中位數 +2.8%、55% 上漲、虧超過 15% 的 6%、最差一成 -14.0% 以下')
  assert.equal(similarText(sim({ big_loss_rate: null, p10: null })), '中位數 +2.8%、55% 上漲')
  assert.equal(similarText(sim({ n: 0, median: null, win_rate: null })), '沒有可比的情境')
})

test('D-182：年份範圍與例子', () => {
  assert.equal(spanText(sim({})), '2018～2025 年')
  assert.equal(spanText(sim({ from: '2021-01-04', to: '2021-12-30' })), '2021 年')
  assert.equal(spanText(sim({ from: null })), '')
  assert.equal(exampleText({ d: '2021-03-05', sid: '2330', name: '台積電', r: 0.1234 }), '2021-03-05 台積電（2330） +12.3%')
  assert.equal(exampleText({ d: '2021-03-05', sid: '2330', name: null, r: -0.05 }), '2021-03-05 2330 -5.0%')
})

test('D-182：分布條同同區間參考的畫法（−30%～+30%，超出夾邊）', () => {
  const bar = similarBar(sim({}))!
  assert.equal(bar.zero, 50)
  assert.ok(bar.p10 < bar.median && bar.median < bar.p90)
  assert.equal(similarBar(sim({ p10: -0.9, p90: 0.9 }))!.p90, 100)
  assert.equal(similarBar(sim({ median: null })), null)
})

// 假的 postgres.js：tagged template 呼叫，照順序回結果
function fakeDb(results: (() => Promise<unknown[]>)[]) {
  return ((_s: TemplateStringsArray, ..._v: unknown[]) => {
    const next = results.shift()
    return next ? next() : Promise.resolve([])
  }) as unknown as Parameters<typeof readSimilar>[0]
}

test('D-182：/api 讀當天的比對、鍵＝線|代號、例子補名稱；表還沒建當成沒有', async () => {
  const missing = () => Promise.reject(Object.assign(new Error('relation "serving.pick_similar" does not exist'), { code: '42P01' }))
  assert.equal((await readSimilar(fakeDb([missing]), '2026-10-15')).size, 0)
  const row = {
    line: 'QUIET',
    stock_id: '1234',
    ...sim({}),
    examples: JSON.stringify([
      { d: '2021-03-05', sid: '2330', r: 0.12 },
      { d: '2019-06-03', sid: '9999', r: -0.2 },
      { bad: true },
    ]),
  }
  const got = await readSimilar(
    fakeDb([() => Promise.resolve([row]), () => Promise.resolve([{ stock_id: '2330', name: '台積電' }])]),
    '2026-10-15',
  )
  const s = got.get('QUIET|1234')!
  assert.equal(s.n, 200)
  assert.deepEqual(s.examples, [
    { d: '2021-03-05', sid: '2330', r: 0.12, name: '台積電' },
    { d: '2019-06-03', sid: '9999', r: -0.2, name: null },
  ])
  assert.equal('line' in s, false)
  await assert.rejects(readSimilar(fakeDb([() => Promise.reject(Object.assign(new Error('x'), { code: '28P01' }))]), '2026-10-15'))
})
