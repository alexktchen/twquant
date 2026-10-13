import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { RISK_AGREE, RISK_POINTS, RISK_VERSION, riskAccepted, riskRecord } from '../src/cloud/risk.ts'

test('投資風險警語：同意過目前這一版才算；警語改版要重新同意', () => {
  assert.equal(riskAccepted(null), false)
  assert.equal(riskAccepted(riskRecord('2026-10-13')), true)
  assert.equal(riskRecord('2026-10-13'), `${RISK_VERSION}:2026-10-13`)
  assert.equal(riskAccepted('0:2026-10-01'), false) // 舊版的同意不算
  assert.equal(riskAccepted(riskRecord('2026-10-13', '1'), '2'), false)
  assert.equal(riskAccepted('亂寫的'), false)
})

test('投資風險警語：有賺有賠、不構成投資建議、過去不代表未來、非投顧、自負盈虧都要寫到', () => {
  const all = RISK_POINTS.join('')
  for (const must of ['有賺有賠', '不構成任何投資建議', '不代表未來', '不是證券投資顧問', '自負盈虧']) {
    assert.ok(all.includes(must), `警語要包含「${must}」`)
  }
  assert.ok(RISK_AGREE.includes('同意'))
})

test('投資風險警語：關於頁同步列出同樣的警語；隱私權政策說明瀏覽器會記住同意紀錄', () => {
  const about = readFileSync(new URL('../public/about.html', import.meta.url), 'utf8')
  for (const p of RISK_POINTS) assert.ok(about.includes(p), `關於頁少了：${p.slice(0, 20)}…`)
  const privacy = readFileSync(new URL('../public/privacy.html', import.meta.url), 'utf8')
  assert.ok(privacy.includes('投資風險警語'))
})
