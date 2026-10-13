import assert from 'node:assert/strict'
import test from 'node:test'
import { MODEL_TEXT, modelView, riskLevel, scoreTags, sparkPoints, topPct, type ModelScore } from '../src/cloud/scores.ts'

const m = (over: Partial<ModelScore> & Pick<ModelScore, 'code'>): ModelScore => ({
  date: '2026-10-12',
  score: null,
  rank_pct: null,
  rank: null,
  total: null,
  avg: null,
  history: [],
  ...over,
})

test('T-345 AC6: 漲幅排名寫成「前幾 %」與名次；第 1 名不會變成前 0%', () => {
  assert.equal(topPct(1, 1952), 1)
  assert.equal(topPct(61, 1952), 4)
  assert.equal(topPct(1952, 1952), 100)
  const v = modelView(m({ code: 'rank', score: 0.01, rank_pct: 0.97, rank: 61, total: 1952 }))
  assert.equal(v.big, '前 4%')
  assert.equal(v.sub, '全市場 1,952 檔中第 61 名')
  assert.equal(v.tone, 'hot')
  assert.equal(modelView(m({ code: 'rank', score: -0.05, rank: 1500, total: 1952 })).tone, 'plain')
})

test('T-345 AC6: 飆漲機率是百分比、附池內名次與平均；不在潛伏池就寫不在，不寫 0%', () => {
  const v = modelView(m({ code: 'quiet', score: 0.2345, rank: 12, total: 241, avg: 0.0924 }))
  assert.equal(v.big, '23%')
  assert.equal(v.sub, '潛伏池 241 檔中第 12 名，池內平均 9%')
  assert.equal(v.tone, 'hot')
  const out = modelView(m({ code: 'quiet', total: 241, avg: 0.09 }))
  assert.equal(out.big, '—')
  assert.equal(out.tone, 'na')
  assert.ok(out.sub.includes('不在潛伏池') && out.sub.includes('241'))
})

test('T-345 AC6: 回檔風險倒過來數（分數高＝跌得淺），三等分成高／中／低', () => {
  // rank 是依分數由高到低：第 1 名最不會跌
  assert.deepEqual(riskLevel(m({ code: 'risk', rank: 1956, total: 1956 })), { level: '高', riskRank: 1 })
  assert.deepEqual(riskLevel(m({ code: 'risk', rank: 1, total: 1956 })), { level: '低', riskRank: 1956 })
  assert.equal(riskLevel(m({ code: 'risk', rank: 978, total: 1956 }))?.level, '中')
  assert.equal(riskLevel(m({ code: 'risk', total: 1956 })), null)
  const v = modelView(m({ code: 'risk', score: 0.2, rank: 1900, total: 1956 }))
  assert.equal(v.big, '高')
  assert.equal(v.tone, 'warn')
  assert.equal(v.sub, '1,956 檔中第 57 容易大跌')
})

test('T-345 AC6: 沒分數一律「—」並說原因；雲端還沒有這個模型的分數也講清楚', () => {
  assert.equal(modelView(m({ code: 'rank', total: 1952 })).sub, '模型今天沒有評到這檔')
  assert.equal(modelView(m({ code: 'risk', date: null })).sub, '雲端還沒有這個模型的分數')
  for (const code of ['rank', 'quiet', 'risk'] as const) assert.ok(MODEL_TEXT[code].what.length > 20)
})

test('T-345 AC6: 走勢點舊到新、越高越好（排名用贏過幾 %，機率用機率），缺值不畫', () => {
  const history = [
    { date: '2026-10-07', score: 0.03, rank_pct: 0.9 },
    { date: '2026-10-08', score: 0.05, rank_pct: null },
    { date: '2026-10-12', score: 0.04, rank_pct: 0.95 },
  ]
  assert.deepEqual(sparkPoints(m({ code: 'rank', history })).map((p) => p.value), [0.9, 0.95])
  assert.deepEqual(sparkPoints(m({ code: 'quiet', history })).map((p) => p.value), [0.03, 0.05, 0.04])
})

test('T-345 AC7: 追蹤清單的標籤：排名一定有、在池子才有機率、風險高才標', () => {
  const tags = scoreTags([
    m({ code: 'rank', rank: 61, total: 1952 }),
    m({ code: 'quiet', total: 241 }),
    m({ code: 'risk', rank: 1950, total: 1956 }),
  ])
  assert.deepEqual(tags, [
    { text: '漲幅 前 4%', tone: 'hot' },
    { text: '回檔風險高', tone: 'warn' },
  ])
  const calm = scoreTags([
    m({ code: 'rank', total: 1952 }),
    m({ code: 'quiet', score: 0.05, rank: 100, total: 241, avg: 0.09 }),
    m({ code: 'risk', rank: 10, total: 1956 }),
  ])
  assert.deepEqual(calm, [
    { text: '漲幅 —', tone: 'plain' },
    { text: '飆漲 5%', tone: 'plain' },
  ])
})
