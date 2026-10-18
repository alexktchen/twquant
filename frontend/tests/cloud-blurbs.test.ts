import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { blurbOf } from '../functions/_lib/blurbs.ts'
import { blurbTitle } from '../src/cloud/blurb.ts'

test('T-393 一句話：AI 寫的標 ai、規則句不標；空白不顯示', () => {
  assert.deepEqual(blurbOf({ text: ' 月營收大增，但近 5 日跌 6.0% ', source: 'model' }), { text: '月營收大增，但近 5 日跌 6.0%', ai: true })
  assert.deepEqual(blurbOf({ text: '近 60 日漲 57%', source: 'rule' }), { text: '近 60 日漲 57%', ai: false })
  assert.equal(blurbOf({ text: '  ', source: 'model' }), null)
  assert.equal(blurbOf({ text: null, source: null }), null)
  assert.match(blurbTitle({ text: 'x', ai: true }), /不是投資建議/)
  assert.match(blurbTitle({ text: 'x', ai: false }), /規則/)
})

test('T-393 一句話：名單 API 每檔附上、名單列與個股頁都顯示', () => {
  const api = readFileSync(new URL('../functions/api/picks/index.ts', import.meta.url), 'utf8')
  assert.match(api, /readBlurbs\(db, target\)/)
  assert.match(api, /blurb: blurbs\.get\(`\$\{code\}\|\$\{r\.stock_id\}`\) \?\? null/)
  const app = readFileSync(new URL('../src/cloud/CloudApp.tsx', import.meta.url), 'utf8')
  assert.match(app, /className="cq-blurb"/)
  assert.match(app, /className=\{`cq-blurb-full/)
})
