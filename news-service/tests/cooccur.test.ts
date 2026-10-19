import assert from 'node:assert/strict'
import test from 'node:test'
import { cooccurLabel, desiredCooccur, diffCooccur, learnCooccur, validateCooccurConfig, type CooccurEdge } from '../src/cooccur.ts'
import type { EntityType } from '../src/graph.ts'
import { propagate } from '../src/graph.ts'

const cfg = { window_days: 90, min_items: 3, hub_share: 0.5 }
const types = new Map<string, EntityType>([
  ['tw:2317', 'tw'],
  ['co:mitsubishi', 'company'],
  ['tw:2330', 'tw'],
  ['us:NVDA', 'us'],
  ['th:ai', 'theme'],
  ['grp:半導體', 'group'],
  ['th:ev', 'theme'],
])

test('從新聞學關係：一起出現達門檻才長線；到處都提的、兩邊都不是公司的、已經有線的不算；說明寫次數', () => {
  const pairs = [
    { a: 'tw:2317', b: 'co:mitsubishi', n: 4 }, // 鴻海＋三菱電機 4 則 → 長線
    { a: 'tw:2330', b: 'us:NVDA', n: 2 }, // 不到 3 則
    { a: 'th:ai', b: 'tw:2330', n: 9 }, // AI 到處都提
    { a: 'grp:半導體', b: 'th:ev', n: 5 }, // 兩邊都不是公司
    { a: 'us:NVDA', b: 'tw:2317', n: 3 }, // 已經有供應商線
    { a: 'tw:9999', b: 'tw:2317', n: 6 }, // 不在實體表
  ]
  const mentionsOf = new Map([
    ['th:ai', 9],
    ['tw:2330', 5],
    ['tw:2317', 4],
  ])
  const got = desiredCooccur(pairs, mentionsOf, 10, new Set(['tw:2317|us:NVDA']), types, cfg)
  assert.deepEqual(got, [{ src: 'co:mitsubishi', dst: 'tw:2317', label: '新聞常一起提到・4 則' }])
  assert.equal(cooccurLabel(12), '新聞常一起提到・12 則')
})

test('漸漸累積：次數變了原地改說明、新的長出來、掉到門檻以下的關掉', () => {
  const old: CooccurEdge[] = [
    { src: 'a', dst: 'b', label: cooccurLabel(3) },
    { src: 'c', dst: 'd', label: cooccurLabel(5) },
  ]
  const want: CooccurEdge[] = [
    { src: 'a', dst: 'b', label: cooccurLabel(4) },
    { src: 'e', dst: 'f', label: cooccurLabel(3) },
  ]
  const d = diffCooccur(old, want)
  assert.deepEqual(d.relabel, [want[0]])
  assert.deepEqual(d.insert, [want[1]])
  assert.deepEqual(d.close, [old[1]])
  assert.deepEqual(diffCooccur(want, want), { close: [], insert: [], relabel: [] })
})

test('cooccur 線雙向都走：從三菱電機或鴻海出發都連得到對方', () => {
  const edges = [{ src: 'co:mitsubishi', dst: 'tw:2317', type: 'cooccur' as const, label: cooccurLabel(4) }]
  const w = { supplier: 0.6, belongs: 0.5, member: 0.5, peer: 0.4, theme: 0.6, cooccur: 0.35 }
  const c = { weights: w, fanout_power: 0.5, max_degree: 2, max_links: 26 }
  assert.ok(propagate(['tw:2317'], edges, c).some((l) => l.entity === 'co:mitsubishi' && l.path[1].via === 'cooccur'))
  assert.ok(propagate(['co:mitsubishi'], edges, c).some((l) => l.entity === 'tw:2317'))
})

test('ingest 第四步：照注入的 IO 算、有變才寫；設定檢查', async () => {
  const applied: unknown[] = []
  const deps = {
    counts: async () => ({ pairs: [{ a: 'tw:2317', b: 'co:mitsubishi', n: 3 }], mentionsOf: new Map(), total: 50 }),
    types: async () => types,
    linked: async () => new Set<string>(),
    existing: async () => [] as CooccurEdge[],
    apply: async (d: unknown) => void applied.push(d),
  }
  assert.deepEqual(await learnCooccur(cfg, deps), { edges: 1, added: 1, closed: 0, relabeled: 0 })
  assert.equal(applied.length, 1)
  await learnCooccur(cfg, { ...deps, existing: async () => [{ src: 'co:mitsubishi', dst: 'tw:2317', label: cooccurLabel(3) }] })
  assert.equal(applied.length, 1, '沒變就不寫')
  assert.throws(() => validateCooccurConfig({ window_days: 90, min_items: 1, hub_share: 0.1 }), /min_items/)
  assert.throws(() => validateCooccurConfig(undefined), /cooccur/)
})
