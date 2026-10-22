import assert from 'node:assert/strict'
import test from 'node:test'
import { parseEvidenceQuery, pickEvidence, validateEvidenceConfig } from '../src/evidence.ts'

const cfg = validateEvidenceConfig({ max_ids: 3, max_entities: 2 })

test('T-437 AC1 證據查詢參數：逗號分開、去重複、上限、格式；缺就丟錯', () => {
  assert.deepEqual(parseEvidenceQuery('a, b,a,', 'tw:2330,us:NVDA', cfg), { ids: ['a', 'b'], entities: ['tw:2330', 'us:NVDA'] })
  assert.throws(() => parseEvidenceQuery(null, 'tw:2330', cfg), /缺 ids/)
  assert.throws(() => parseEvidenceQuery('a', '', cfg), /缺 entities/)
  assert.throws(() => parseEvidenceQuery('a,b,c,d', 'tw:2330', cfg), /ids 最多 3/)
  assert.throws(() => parseEvidenceQuery('a', 'tw:2330,us:NVDA,th:ai', cfg), /entities 最多 2/)
  assert.throws(() => parseEvidenceQuery('a', '2330', cfg), /entities 要像 tw:2330/)
  assert.throws(() => validateEvidenceConfig({ max_ids: 0, max_entities: 2 }), /evidence\.max_ids/)
})

test('T-437 AC2 同一則同一實體比對過幾版 → 留最新；輸出照新聞 id、實體 id 排', () => {
  const got = pickEvidence([
    { item_id: 'b', entity_id: 'tw:2330', evidence: '舊：台積電法說', created_at: '2026-10-20T01:00:00Z' },
    { item_id: 'b', entity_id: 'tw:2330', evidence: '新：台積電法說會', created_at: new Date('2026-10-21T01:00:00Z') },
    { item_id: 'a', entity_id: 'us:NVDA', evidence: 'Nvidia earnings', created_at: '2026-10-21T02:00:00Z' },
    { item_id: 'a', entity_id: 'tw:2330', evidence: '輝達與台積電', created_at: '2026-10-21T02:00:00Z' },
  ])
  assert.deepEqual(got, { a: { 'tw:2330': '輝達與台積電', 'us:NVDA': 'Nvidia earnings' }, b: { 'tw:2330': '新：台積電法說會' } })
  assert.deepEqual(Object.keys(got), ['a', 'b'])
  assert.deepEqual(pickEvidence([]), {})
})
