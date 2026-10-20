import assert from 'node:assert/strict'
import test from 'node:test'
import {
  assignCluster,
  embedPending,
  embedText,
  mergeSimilar,
  parseEmbedReply,
  parseSearchQuery,
  validateEmbedConfig,
  vecLiteral,
  type ClusterRow,
  type EmbedConfig,
  type EmbedSource,
  type EmbeddingRow,
  type Neighbor,
} from '../src/embed.ts'

const cfg: EmbedConfig = {
  model: '@cf/baai/bge-m3',
  dim: 4,
  batch: 2,
  per_run_limit: 10,
  max_chars: 20,
  input_neurons_per_1m: 1075,
  retention_days: 45,
  cluster: { min_score: 0.85, window_hours: 72, exclude_sources: ['fed-press'] },
  similar: { limit: 3, min_score: 0.6 },
  search: { limit: 20, max_query_chars: 10, min_score: 0.4 },
}

const src = (id: string, over: Partial<EmbedSource> = {}): EmbedSource => ({
  item_id: id,
  source: 'cnbc-top',
  first_seen_at: `2026-10-20T0${id.length}:00:00Z`,
  title: 'Nvidia raises guidance',
  summary: 'AI demand',
  title_zh: null,
  summary_zh: null,
  ...over,
})

const reply = (vecs: number[][], meta: Record<string, unknown> = { cost_metric_name_1: 'input_tokens', cost_metric_value_1: 100, neurons: 0.11 }) => ({
  success: true,
  result: { data: vecs, shape: [vecs.length, 4], meta },
})

test('AC5 文字規則：有譯文用譯文、沒有用原文；標題＋摘要；截到 max_chars（按字不按位元組）', () => {
  assert.equal(embedText(src('a'), 100), 'Nvidia raises guidance\nAI demand')
  assert.equal(embedText(src('a', { title_zh: '輝達上調財測', summary_zh: 'AI 需求' }), 100), '輝達上調財測\nAI 需求')
  assert.equal(embedText(src('a', { summary: null }), 100), 'Nvidia raises guidance')
  assert.equal(embedText(src('a', { title_zh: '輝達上調財測輝達上調財測輝達上調財測輝達上調財測' }), 20).length, 20)
  // 翻譯沒過（title_zh 空）→ 原文
  assert.equal(embedText(src('a', { title_zh: '' }), 100), 'Nvidia raises guidance\nAI demand')
})

test('AC4 回應解析：向量數量、維度、數值都要對；Neurons 用回應附的，沒附用 token 估；沒 token 數就丟錯', () => {
  const r = parseEmbedReply(reply([[1, 0, 0, 0]]), 1, cfg)
  assert.deepEqual(r, { vecs: [[1, 0, 0, 0]], tokens: 100, neurons: 0.11 })
  const est = parseEmbedReply(reply([[1, 0, 0, 0]], { cost_metric_name_1: 'input_tokens', cost_metric_value_1: 1_000_000 }), 1, cfg)
  assert.equal(est.neurons, 1075)
  assert.throws(() => parseEmbedReply(reply([[1, 0, 0, 0]]), 2, cfg), /數量/)
  assert.throws(() => parseEmbedReply(reply([[1, 0, 0]]), 1, cfg), /維度/)
  assert.throws(() => parseEmbedReply(reply([[1, 0, NaN, 0]]), 1, cfg), /維度或數值/)
  assert.throws(() => parseEmbedReply(reply([[1, 0, 0, 0]], {}), 1, cfg), /token/)
  assert.throws(() => parseEmbedReply({ success: false, errors: [{ message: 'quota' }] }, 1, cfg), /quota/)
})

test('pgvector 文字格式', () => {
  assert.equal(vecLiteral([0.5, -0.25, 1e-9, 0.1234567]), '[0.500000,-0.250000,0,0.123457]')
})

test('AC5 同事件合併只新併舊：達門檻併進最像那則的群、未達自己開一群、排除來源一律自己一群', () => {
  const nb: Neighbor[] = [
    { item_id: 'old1', cluster_id: 'seed0', score: 0.9 },
    { item_id: 'old2', cluster_id: 'old2', score: 0.7 },
  ]
  assert.deepEqual(assignCluster({ item_id: 'new', source: 'cnbc-top' }, nb, cfg.cluster), { item_id: 'new', cluster_id: 'seed0', seed_item_id: 'old1', score: 0.9 })
  assert.deepEqual(assignCluster({ item_id: 'new', source: 'cnbc-top' }, nb.slice(1), cfg.cluster), { item_id: 'new', cluster_id: 'new', seed_item_id: null, score: null })
  assert.deepEqual(assignCluster({ item_id: 'new', source: 'cnbc-top' }, [], cfg.cluster).cluster_id, 'new')
  assert.equal(assignCluster({ item_id: 'new', source: 'fed-press' }, nb, cfg.cluster).cluster_id, 'new')
})

test('ingest 第五步：依時間先後一則一則寫（後面的找得到前面的當同事件）、記用量、額度用完就停', async () => {
  const saved: { e: EmbeddingRow; c: ClusterRow }[] = []
  const usage: unknown[] = []
  const texts: string[][] = []
  const deps = {
    now: () => new Date('2026-10-20T10:00:00Z'),
    pending: async (limit: number) => [src('a'), src('bb', { title: 'Nvidia raises guidance again' }), src('ccc')].slice(0, limit),
    usedToday: async () => 0,
    ai: async (t: string[]) => {
      texts.push(t)
      return reply(t.map(() => [1, 0, 0, 0]))
    },
    sha: async (s: string) => `sha:${s.length}`,
    // 已經寫進去的都當鄰居（分數 0.95）
    neighbors: async (): Promise<Neighbor[]> => saved.map((s) => ({ item_id: s.e.item_id, cluster_id: s.c.cluster_id, score: 0.95 })).reverse(),
    save: async (e: EmbeddingRow, c: ClusterRow) => void saved.push({ e, c }),
    saveUsage: async (u: unknown) => void usage.push(u),
    prune: async (days: number) => (days === 45 ? 7 : -1),
  }
  const r = await embedPending(cfg, 2500, deps)
  assert.deepEqual(r, { embedded: 3, clusters_joined: 2, budget_stop: false, pruned: 7 }, '先刪超過保留期的向量（照設定的天數）')
  assert.deepEqual(texts.map((t) => t.length), [2, 1], '一批 batch 則')
  assert.deepEqual(saved.map((s) => s.c.cluster_id), ['a', 'a', 'a'], '第一則開群，後面兩則併進去')
  assert.equal(saved[0].e.vec, '[1.000000,0,0,0]')
  assert.equal(usage.length, 2)
  assert.deepEqual(usage[0], { ts: '2026-10-20T10:00:00.000Z', task: 'news_embed', model_id: '@cf/baai/bge-m3', input_tokens: 100, output_tokens: 0, neurons: 0.11 })

  const stop = await embedPending(cfg, 2500, { ...deps, usedToday: async () => 2500 })
  assert.deepEqual(stop, { embedded: 0, clusters_joined: 0, budget_stop: true, pruned: 7 }, '額度用完照樣刪過期的（不花額度）')
})

test('AC5 相似新聞：同事件在前、再補向量最近的；去重、去掉自己、低於門檻不列、最多 limit', () => {
  const got = mergeSimilar(
    'me',
    [
      { item_id: 'e2', score: 0.88 },
      { item_id: 'e1', score: 0.93 },
      { item_id: 'me', score: 1 },
    ],
    [
      { item_id: 'e1', score: 0.93 },
      { item_id: 'n1', score: 0.7 },
      { item_id: 'n0', score: 0.5 },
    ],
    cfg.similar,
  )
  assert.deepEqual(got, [
    { item_id: 'e1', score: 0.93, same_event: true },
    { item_id: 'e2', score: 0.88, same_event: true },
    { item_id: 'n1', score: 0.7, same_event: false },
  ])
  assert.equal(mergeSimilar('me', [], [1, 2, 3, 4, 5].map((k) => ({ item_id: `n${k}`, score: 0.9 })), cfg.similar).length, 3)
})

test('搜尋字：去空白、不可空、不可太長', () => {
  assert.equal(parseSearchQuery(new URLSearchParams('q=%20輝達%20'), cfg.search), '輝達')
  assert.throws(() => parseSearchQuery(new URLSearchParams(''), cfg.search), /缺 q/)
  assert.throws(() => parseSearchQuery(new URLSearchParams('q=%20'), cfg.search), /缺 q/)
  assert.throws(() => parseSearchQuery(new URLSearchParams(`q=${'字'.repeat(11)}`), cfg.search), /太長/)
})

test('設定檢查', () => {
  assert.equal(validateEmbedConfig(cfg), cfg)
  assert.throws(() => validateEmbedConfig(undefined), /embed/)
  assert.throws(() => validateEmbedConfig({ ...cfg, model: 'bge' }), /model/)
  assert.throws(() => validateEmbedConfig({ ...cfg, retention_days: 0 }), /retention_days/)
  assert.throws(() => validateEmbedConfig({ ...cfg, cluster: { ...cfg.cluster, min_score: 1.2 } }), /cluster/)
  assert.throws(() => validateEmbedConfig({ ...cfg, similar: { limit: 0, min_score: 0.5 } }), /similar/)
  assert.throws(() => validateEmbedConfig({ ...cfg, search: { ...cfg.search, max_query_chars: 0 } }), /search/)
})
