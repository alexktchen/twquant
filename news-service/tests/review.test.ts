import assert from 'node:assert/strict'
import test from 'node:test'
import { DECISIONS_MAX, edgeKey, groupDrafts, isOwner, parseReviewBody, planReview, type DraftEdge, type EntityInfo } from '../src/review.ts'

const ent = (id: string, type: EntityInfo['type'], label: string, sub: string | null = null): [string, EntityInfo] => [id, { id, type, label, sub }]
const entities = new Map<string, EntityInfo>([
  ent('us:NVDA', 'us', '輝達', 'NVDA'),
  ent('tw:2330', 'tw', '台積電', '2330'),
  ent('tw:2382', 'tw', '廣達', '2382'),
  ent('th:ai', 'theme', 'AI', '主題'),
  ent('grp:伺服器', 'group', '伺服器', '族群'),
])
const draft = (src: string, dst: string, type: DraftEdge['type'] = 'supplier', label: string | null = null): DraftEdge => ({ src, dst, type, label, rank: null, source: 'config' })

test('AC2 只有擁有者能核可：名單大小寫與空白不影響；沒登入、名單沒設定 → 都不行', () => {
  assert.equal(isOwner('Owner@Example.com', ' owner@example.com , other@example.com'), true)
  assert.equal(isOwner('other@example.com', 'owner@example.com,other@example.com'), true)
  assert.equal(isOwner('guest@example.com', 'owner@example.com'), false)
  assert.equal(isOwner(null, 'owner@example.com'), false)
  assert.equal(isOwner('owner@example.com', undefined), false)
  assert.equal(isOwner('owner@example.com', ''), false)
  assert.equal(isOwner('', ','), false)
})

test('草稿分組：同一個起點、同一種線一組；供應商在前、再主題、再同業；組間依起點代號、組內依終點代號排；查不到的實體用 id 頂著', () => {
  const g = groupDrafts(
    [draft('us:NVDA', 'tw:2382'), draft('th:ai', 'grp:伺服器', 'theme', 'AI 伺服器'), draft('us:NVDA', 'tw:2330'), draft('us:XYZ', 'tw:9999')],
    entities,
  )
  assert.deepEqual(
    g.map((x) => [x.type, x.src_label, x.items.map((i) => i.dst_label)]),
    [
      ['supplier', '輝達', ['台積電', '廣達']],
      ['supplier', 'us:XYZ', ['tw:9999']],
      ['theme', 'AI', ['伺服器']],
    ],
  )
  assert.deepEqual(g[0].items[0], { dst: 'tw:2330', dst_label: '台積電', dst_type: 'tw', dst_sub: '2330', label: null })
  assert.equal(g[2].items[0].label, 'AI 伺服器')
})

test('送出的決定：格式、線種、決定、重複、數量都要對', () => {
  const ok = parseReviewBody({ decisions: [{ src: 'us:NVDA', dst: 'tw:2330', type: 'supplier', decision: 'approved', extra: 1 }] })
  assert.deepEqual(ok, [{ src: 'us:NVDA', dst: 'tw:2330', type: 'supplier', decision: 'approved' }])
  assert.throws(() => parseReviewBody(null), /decisions/)
  assert.throws(() => parseReviewBody({ decisions: [] }), /decisions/)
  assert.throws(() => parseReviewBody({ decisions: [{ src: 'a', dst: 'b', type: 'belongs', decision: 'approved' }] }), /線種/)
  assert.throws(() => parseReviewBody({ decisions: [{ src: 'a', dst: 'b', type: 'supplier', decision: 'ok' }] }), /decision/)
  assert.throws(() => parseReviewBody({ decisions: [{ src: '', dst: 'b', type: 'supplier', decision: 'approved' }] }), /src/)
  const d = { src: 'a', dst: 'b', type: 'supplier', decision: 'approved' }
  assert.throws(() => parseReviewBody({ decisions: [d, { ...d, decision: 'rejected' }] }), /重複/)
  assert.throws(() => parseReviewBody({ decisions: Array.from({ length: DECISIONS_MAX + 1 }, (_, i) => ({ ...d, dst: `t${i}` })) }), /最多/)
})

test('AC3 核可：草稿關掉、插一條 approved（說明、名次、來源照舊），valid_from＝核可時間＝紀錄時間；退回只關不插；已不是草稿的略過', () => {
  const drafts = new Map([draft('us:NVDA', 'tw:2330'), draft('th:ai', 'grp:伺服器', 'theme', 'AI 伺服器')].map((e) => [edgeKey(e), e]))
  const now = new Date('2026-10-20T02:00:00Z')
  const p = planReview(
    [
      { src: 'us:NVDA', dst: 'tw:2330', type: 'supplier', decision: 'approved' },
      { src: 'th:ai', dst: 'grp:伺服器', type: 'theme', decision: 'rejected' },
      { src: 'us:NVDA', dst: 'tw:2382', type: 'supplier', decision: 'approved' },
    ],
    drafts,
    'owner@example.com',
    now,
  )
  assert.deepEqual(p.close, [
    { src: 'us:NVDA', dst: 'tw:2330', type: 'supplier' },
    { src: 'th:ai', dst: 'grp:伺服器', type: 'theme' },
  ])
  assert.deepEqual(p.insert, [{ src: 'us:NVDA', dst: 'tw:2330', type: 'supplier', label: null, rank: null, source: 'config', status: 'approved', valid_from: '2026-10-20T02:00:00.000Z' }])
  assert.deepEqual(
    p.reviews.map((r) => [r.key, r.decision, r.reviewer, r.reviewed_at]),
    [
      ['us:NVDA|tw:2330|supplier', 'approved', 'owner@example.com', '2026-10-20T02:00:00.000Z'],
      ['th:ai|grp:伺服器|theme', 'rejected', 'owner@example.com', '2026-10-20T02:00:00.000Z'],
    ],
  )
  assert.deepEqual(p.skipped, ['us:NVDA|tw:2382|supplier'])
})
