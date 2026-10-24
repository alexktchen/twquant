import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { validateConfig } from '../src/ingest.ts'
import { parseReply, requestBody, sentimentPending, type SentimentDeps, type SentimentItem, type SentimentRow, type UsageRow } from '../src/sentiment.ts'

const cfg = validateConfig(JSON.parse(readFileSync(new URL('../config/news.json', import.meta.url), 'utf8'))).sentiment

const item: SentimentItem = {
  item_id: 'a1',
  content_sha: 's1',
  title: '台積電搶下聯電大單',
  summary: '先進製程訂單轉單',
  stocks: [
    { id: 'tw:2330', label: '台積電' },
    { id: 'tw:2303', label: '聯電' },
  ],
}
const reply = (items: unknown, usage = { prompt_tokens: 300, completion_tokens: 30 }) => ({ success: true, result: { response: JSON.stringify({ items }), usage } })

test('D-205 A：請求帶標題、摘要、公司代號與名稱；系統提示關推理、要求 JSON', () => {
  const b = requestBody(cfg, item) as { messages: { role: string; content: string }[]; response_format: unknown }
  assert.match(b.messages[0].content, /不是你對股價的預測/)
  assert.ok(b.messages[0].content.endsWith(cfg.system_suffix))
  const user = JSON.parse(b.messages[1].content)
  assert.deepEqual(user.公司, [
    { id: '2330', 名稱: '台積電' },
    { id: '2303', 名稱: '聯電' },
  ])
  assert.ok(b.response_format)
})

test('D-205 A：回應要每檔都有、不能多、標籤要看得懂；同一則新聞對不同股票可以不一樣', () => {
  const r = parseReply(reply([{ id: '2330', label: '好' }, { id: '2303', label: '壞' }]), ['tw:2330', 'tw:2303'])
  assert.deepEqual([...r.labels], [['tw:2330', 1], ['tw:2303', -1]])
  assert.equal(r.inputTokens, 300)
  assert.throws(() => parseReply(reply([{ id: '2330', label: '好' }]), ['tw:2330', 'tw:2303']), /少了/)
  assert.throws(() => parseReply(reply([{ id: '9999', label: '好' }]), ['tw:2330']), /沒問的/)
  assert.throws(() => parseReply(reply([{ id: '2330', label: '大好' }]), ['tw:2330']), /看不懂/)
  assert.throws(() => parseReply({ success: false, errors: ['x'] }, ['tw:2330']), /回報失敗/)
})

function world(replies: unknown[], used = 0) {
  const saved: SentimentRow[] = []
  const usage: UsageRow[] = []
  let calls = 0
  const deps: SentimentDeps = {
    now: () => new Date('2026-10-24T08:00:00Z'),
    pending: async () => [item, { ...item, item_id: 'b2', content_sha: 's2' }],
    usedToday: async () => used,
    ai: async () => replies[calls++],
    save: async (rows, u) => {
      saved.push(...rows)
      usage.push(...u)
    },
  }
  return { deps, saved, usage, calls: () => calls }
}

test('D-205 A：一則一次問完、存標籤與用量；壞回應不存標籤但照記用量、下一輪再問', async () => {
  const w = world([reply([{ id: '2330', label: '好' }, { id: '2303', label: '壞' }]), reply([{ id: '2330', label: '好' }])])
  const r = await sentimentPending(cfg, w.deps)
  assert.deepEqual({ ...r, errors: r.errors.length }, { items: 1, labels: 2, failed: 1, budget_stop: false, by_label: { '-1': 1, '0': 0, '1': 1 }, errors: 1 })
  assert.match(r.errors[0], /b2：.*少了/)
  assert.deepEqual(w.saved.map((s) => [s.item_id, s.entity_id, s.label, s.prompt_version]), [
    ['a1', 'tw:2330', 1, cfg.prompt_version],
    ['a1', 'tw:2303', -1, cfg.prompt_version],
  ])
  assert.equal(w.usage.length, 2)
  assert.ok(w.usage.every((u) => u.task === 'news_sentiment'))
})

test('D-205 A／D-218：情緒自己的額度用完就停、不呼叫模型', async () => {
  const w = world([], cfg.daily_neurons)
  const r = await sentimentPending(cfg, w.deps)
  assert.equal(r.budget_stop, true)
  assert.equal(w.calls(), 0)
  assert.equal(w.saved.length, 0)
})
