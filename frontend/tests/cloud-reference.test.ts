import assert from 'node:assert/strict'
import test from 'node:test'
import { readReference } from '../functions/_lib/reference.ts'
import { MIN_N, bucketText, refBar, refFor, refText, type RefRow } from '../src/cloud/reference.ts'

const row = (over: Partial<RefRow>): RefRow => ({
  period: 'dev',
  label: '開發期（規則在這段資料上試出來的，只當參考）',
  bucket: '0.35-0.40',
  lo: 0.35,
  hi: 0.4,
  n: 1234,
  mean: 0.031,
  median: 0.028,
  win_rate: 0.552,
  big_loss_rate: 0.04,
  p10: -0.14,
  p90: 0.21,
  ...over,
})

test('D-179：依分數對到區間（左閉右開）；不分區間的線（lo 是 null）全部適用；dev 在前', () => {
  const rows = [row({ period: 'unseen', label: '模型沒看過的期間' }), row({}), row({ bucket: '0.40-0.50', lo: 0.4, hi: 0.5 })]
  assert.deepEqual(
    refFor(rows, 0.37).map((r) => r.period),
    ['dev', 'unseen'],
  )
  assert.equal(refFor(rows, 0.4)[0].bucket, '0.40-0.50') // 0.40 屬於上一個區間
  assert.deepEqual(refFor(rows, 0.9), [])
  assert.deepEqual(refFor(rows, null), [])
  assert.deepEqual(refFor([], 0.37), [])
  assert.deepEqual(refFor(null, 0.37), [])
  const core = [row({ bucket: 'ALL', lo: null, hi: null })]
  assert.equal(refFor(core, null).length, 1) // 大盤精選沒有分數也對得到
})

test('D-179：區間與一行文字；筆數少標出來；沒筆數寫沒有可比的筆', () => {
  assert.equal(bucketText(row({})), '分數 0.35～0.40')
  assert.equal(bucketText(row({ lo: 0, hi: 0.25 })), '分數 0.25 以下')
  assert.equal(bucketText(row({ lo: 0.5, hi: 1.0001 })), '分數 0.50 以上')
  assert.equal(bucketText(row({ lo: null, hi: null })), '這條線')
  assert.equal(refText(row({})), '中位數 +2.8%、55% 上漲、最差一成 -14.0% 以下（1,234 筆）')
  assert.equal(refText(row({ n: MIN_N - 1 })), '中位數 +2.8%、55% 上漲、最差一成 -14.0% 以下（29 筆，樣本少）')
  assert.equal(refText(row({ n: 0, median: null, win_rate: null, p10: null, p90: null })), '沒有可比的筆')
  assert.equal(refText(row({ p10: null })), '中位數 +2.8%、55% 上漲（1,234 筆）')
})

test('D-179：分布條把 p10／中位數／p90 放在 −30%～+30% 的橫軸上，超出夾在邊上', () => {
  const bar = refBar(row({}))!
  assert.equal(bar.zero, 50)
  assert.ok(bar.p10 < bar.median && bar.median < bar.p90)
  assert.ok(Math.abs(bar.p10 - ((-0.14 + 0.3) / 0.6) * 100) < 1e-9)
  assert.equal(refBar(row({ p10: -0.9, p90: 0.9 }))!.p10, 0)
  assert.equal(refBar(row({ p10: -0.9, p90: 0.9 }))!.p90, 100)
  assert.equal(refBar(row({ p10: null })), null)
})

// 假的 postgres.js：tagged template 呼叫，照順序回結果
function fakeDb(results: (() => Promise<unknown[]>)[]) {
  return ((_s: TemplateStringsArray, ..._v: unknown[]) => {
    const next = results.shift()
    return next ? next() : Promise.resolve([])
  }) as unknown as Parameters<typeof readReference>[0]
}

test('D-179：/api 讀整表、依線分組；表還沒建（還沒算）當成沒有', async () => {
  const missing = () => Promise.reject(Object.assign(new Error('relation "serving.score_reference" does not exist'), { code: '42P01' }))
  assert.equal((await readReference(fakeDb([missing]))).size, 0)
  const got = await readReference(
    fakeDb([() => Promise.resolve([{ line: 'QUIET', ...row({}) }, { line: 'QUIET', ...row({ period: 'unseen' }) }, { line: 'CORE', ...row({ bucket: 'ALL', lo: null, hi: null }) }])]),
  )
  assert.deepEqual([...got.keys()], ['QUIET', 'CORE'])
  assert.equal(got.get('QUIET')?.length, 2)
  assert.equal('line' in (got.get('CORE')?.[0] ?? {}), false)
  await assert.rejects(readReference(fakeDb([() => Promise.reject(Object.assign(new Error('x'), { code: '28P01' }))])))
})
