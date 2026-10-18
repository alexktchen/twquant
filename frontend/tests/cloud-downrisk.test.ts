import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { downRiskTag, downRiskText } from '../src/cloud/downrisk.ts'

test('T-391 AC-1：只有 flagged 才有「模型:易跌」小標；title 寫機率、寫明剔掉它們平均報酬反而降、名單不刪減', () => {
  assert.equal(downRiskTag(null), null)
  assert.equal(downRiskTag(undefined), null)
  assert.equal(downRiskTag({ p: 0.6, flagged: false }), null)
  const t = downRiskTag({ p: 0.374, flagged: true })!
  assert.equal(t.text, '模型:易跌')
  assert.match(t.title, /模型機率 37%/)
  assert.match(t.title, /4\.29% 掉到 3\.86%/)
  assert.match(t.title, /不是叫你別買/)
  assert.match(t.title, /名單不因此刪減/)
  assert.equal(downRiskText({ p: 0.374, flagged: true }), '模型易跌機率 37%（今天名單裡前 5）')
  assert.equal(downRiskText({ p: 0.12, flagged: false }), '模型易跌機率 12%')
  assert.equal(downRiskText(null), '')
})

test('T-391 AC-2（靜態）：名單 API 讀 pick_down_risk、以線＋代號對應（只有大盤精選有）；名單列有畫', () => {
  const lib = readFileSync(new URL('../functions/_lib/down-risk.ts', import.meta.url), 'utf8')
  assert.ok(lib.includes('serving.pick_down_risk') && lib.includes('readRows('))
  const picks = readFileSync(new URL('../functions/api/picks/index.ts', import.meta.url), 'utf8')
  assert.ok(picks.includes('readDownRisk(db, target)') && picks.includes('down_risk: downRisk.get(`${code}|${r.stock_id}`) ?? null'))
  const app = readFileSync(new URL('../src/cloud/CloudApp.tsx', import.meta.url), 'utf8')
  assert.ok(app.includes('downRiskTag(p.down_risk)'))
})
