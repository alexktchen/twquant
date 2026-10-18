import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { REVENUE_DUE_DAY, REVENUE_WARN_DAYS, RISK_AHEAD_DAYS, revenueDue, risksFor, type Inputs } from '../functions/_lib/risks.ts'
import { riskSummary, riskTags } from '../src/cloud/risks.ts'

const empty = (): Inputs => ({ disp: [], att: [], divs: [], confs: [], revenuePublished: new Set() })

test('T-387 AC-1：處置——期間內「處置中到 m/d」、十天內開始「m/d 起處置」、已結束不列；細節帶原因與撮合間隔', () => {
  const inp = empty()
  inp.disp = [
    { stock_id: '3230', effective_start: '2026-10-19', effective_end: '2026-10-23', reason: '連續3個營業日', match_minutes: 2 },
    { stock_id: '3597', effective_start: '2026-10-05', effective_end: '2026-10-09', reason: null, match_minutes: null },
    { stock_id: '1111', effective_start: '2026-11-05', effective_end: '2026-11-11', reason: 'x', match_minutes: 5 },
  ]
  const soon = risksFor('3230', '2026-10-16', inp)
  assert.deepEqual(soon.map((r) => [r.kind, r.text]), [['disposition_soon', '10/19 起處置']])
  assert.equal(soon[0].detail, '連續3個營業日；每 2 分鐘撮合一次')
  const during = risksFor('3230', '2026-10-21', inp)
  assert.deepEqual(during.map((r) => [r.kind, r.text, r.date]), [['disposition', '處置中到 10/23', '2026-10-23']])
  assert.deepEqual(risksFor('3597', '2026-10-16', inp), [])
  assert.deepEqual(risksFor('1111', '2026-10-16', inp), [], '超過十天後才開始的不列')
  assert.ok(RISK_AHEAD_DAYS >= 7)
})

test('T-387 AC-2：注意股、除權息、法說會——只看名單日之後十天內（除權息不含當天、法說會含當天）；細節文字', () => {
  const inp = empty()
  inp.att = [{ stock_id: '3354', clauses: '1,5', info: '最近六個營業日累積漲幅達33.02%(第一款)\n凱基證券受託買進占32.51%(第五款)' }]
  inp.divs = [
    { stock_id: '3354', date: '2026-10-22', kind: 'cash', cash_dividend: 1.5, stock_ratio: 0 },
    { stock_id: '3354', date: '2026-10-16', kind: 'cash', cash_dividend: 9, stock_ratio: 0 }, // 當天：已經除息了，不列
    { stock_id: '3354', date: '2026-11-20', kind: 'both', cash_dividend: 1, stock_ratio: 0.1 }, // 太遠
  ]
  inp.confs = [
    { stock_id: '3354', date: '2026-10-16', conf_time: '14:00', location: '線上' },
    { stock_id: '3354', date: '2026-10-30', conf_time: null, location: null },
  ]
  const rs = risksFor('3354', '2026-10-16', inp)
  assert.deepEqual(
    rs.map((r) => [r.kind, r.text]),
    [
      ['attention', '注意股'],
      ['conference', '10/16 法說會'],
      ['dividend', '10/22 除息'],
    ],
  )
  assert.equal(rs[0].detail, '最近六個營業日累積漲幅達33.02%(第一款)')
  assert.equal(rs[1].detail, '14:00 線上')
  assert.equal(rs[2].detail, '現金 1.5 元')
  assert.deepEqual(risksFor('9999', '2026-10-16', inp), [])
})

test('T-387 AC-3：月營收——每月 10 日前要公布上月營收；截止前五天內、還沒入庫才提醒；已公布或離截止還遠不提醒', () => {
  assert.deepEqual(revenueDue('2026-10-07'), { ym: '2026-09', due: '2026-10-10' })
  assert.deepEqual(revenueDue('2026-01-03'), { ym: '2025-12', due: '2026-01-10' })
  assert.equal(REVENUE_DUE_DAY, 10)
  const inp = empty()
  const on07 = risksFor('2330', '2026-10-07', inp)
  assert.deepEqual(on07.map((r) => [r.kind, r.text, r.date]), [['revenue', '09 月營收待公布', '2026-10-10']])
  assert.match(on07[0].detail!, /10\/10 前要公布/)
  inp.revenuePublished = new Set(['2330'])
  assert.deepEqual(risksFor('2330', '2026-10-07', inp), [], '已入庫不提醒')
  assert.deepEqual(risksFor('2330', `2026-10-0${10 - REVENUE_WARN_DAYS - 1}`.replace('-00', '-0'), empty()).filter((r) => r.kind === 'revenue').length, 0, '離截止還遠')
  assert.deepEqual(risksFor('2330', '2026-10-11', empty()), [], '過了截止日不再提醒（資料會自己進來）')
})

test('T-387 AC-4：小標與這條線的摘要；沒有風險 → 空／null', () => {
  const tags = riskTags([{ kind: 'disposition', date: '2026-10-23', text: '處置中到 10/23', detail: '連續3個營業日；每 2 分鐘撮合一次' }])
  assert.equal(tags[0].text, '處置中到 10/23')
  assert.match(tags[0].title, /^連續3個營業日；每 2 分鐘撮合一次。處置股每幾分鐘才撮合一次/)
  assert.deepEqual(riskTags(null), [])
  assert.equal(riskSummary([{ risks: null }, { risks: [] }]), null)
  const s = riskSummary([
    { risks: [{ kind: 'disposition', date: null, text: 'x', detail: null }, { kind: 'dividend', date: null, text: 'y', detail: null }] },
    { risks: [{ kind: 'dividend', date: null, text: 'y', detail: null }] },
    { risks: null },
  ])
  assert.equal(s, '3 檔裡 1 檔處置、2 檔近期除權息')
})

test('T-387 AC-5（靜態）：名單 API 每檔附 risks、五張表都容許還沒建；名單列有畫', () => {
  const picks = readFileSync(new URL('../functions/api/picks/index.ts', import.meta.url), 'utf8')
  assert.ok(picks.includes('readRisks(db, target, sids)') && picks.includes('risks: risks.get(r.stock_id) ?? null'))
  const lib = readFileSync(new URL('../functions/_lib/risks.ts', import.meta.url), 'utf8')
  for (const t of ['serving.disposition_stocks', 'serving.attention_stocks', 'serving.dividend_calendar', 'serving.conference_calendar', 'serving.monthly_revenue'])
    assert.ok(lib.includes(t), t)
  assert.equal(lib.match(/readRows\(/g)?.length, 5)
  const app = readFileSync(new URL('../src/cloud/CloudApp.tsx', import.meta.url), 'utf8')
  assert.ok(app.includes('riskTags(p.risks)'))
})
