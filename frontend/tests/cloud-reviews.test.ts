import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { reviewOf } from '../functions/_lib/reviews.ts'
import { DIMS, MAX_SCORE, REVIEW_NOTE, scoreText, stanceTone, topOf } from '../src/cloud/pickreview.ts'

test('T-394 四面向評分：四個分數都在 1～5 才顯示；首選才帶理由', () => {
  const base = {
    news: 3, tech: 5, chips: 5, fund: 5, is_top: true, top_reason: '創 52 週新高', source: 'model',
    news_view: '近期無重大消息', tech_view: ' 創 52 週新高，均線多頭排列 ', chips_view: null, fund_view: '', stance: '偏多', conclusion: '技術與基本面雙雙走強，可觀察回檔機會',
  }
  assert.deepEqual(reviewOf(base), {
    news: 3, tech: 5, chips: 5, fund: 5, total: 18, top: true, reason: '創 52 週新高', ai: true,
    views: { news: '近期無重大消息', tech: '創 52 週新高，均線多頭排列', chips: null, fund: null },
    stance: '偏多', conclusion: '技術與基本面雙雙走強，可觀察回檔機會',
  })
  assert.equal(reviewOf({ ...base, tech: null }), null)
  assert.equal(reviewOf({ ...base, fund: 6 }), null)
  assert.deepEqual(reviewOf({ ...base, is_top: false })?.reason, null)
  assert.equal(reviewOf({ ...base, source: 'rule' })?.ai, false)
})

test('T-394 四個面向的名稱與說明；首選從名單找', () => {
  assert.deepEqual(DIMS.map((d) => d.name), ['消息', '技術', '籌碼', '基本'])
  assert.equal(new Set(DIMS.map((d) => d.color)).size, 4)
  const empty = { views: { news: null, tech: null, chips: null, fund: null }, stance: null, conclusion: null }
  assert.equal(scoreText({ news: 3, tech: 5, chips: 4, fund: 2, total: 14, top: false, reason: null, ai: true, ...empty }), `消息 3、技術 5、籌碼 4、基本 2（共 14／${MAX_SCORE * 4}）`)
  assert.match(REVIEW_NOTE, /沒有驗證過/)
  assert.deepEqual(['偏多', '中性', '偏空', null].map(stanceTone), ['up', 'flat', 'down', 'flat'])
  const v = { news: null, tech: null, chips: null, fund: null }
  const r = { news: 3, tech: 5, chips: 5, fund: 5, total: 18, top: true, reason: 'x', ai: true, views: v, stance: null, conclusion: null }
  assert.equal(topOf([{ sid: 'a', review: null }, { sid: 'b', review: r }])?.sid, 'b')
  assert.equal(topOf([{ sid: 'a' }]), null)
})

test('T-394 名單 API 每檔附評分；名單上方首選卡、名單列四根直條、個股頁分數卡', () => {
  const api = readFileSync(new URL('../functions/api/picks/index.ts', import.meta.url), 'utf8')
  assert.match(api, /readReviews\(db, target\)/)
  assert.match(api, /review: reviews\.get\(`\$\{code\}\|\$\{r\.stock_id\}`\) \?\? null/)
  const app = readFileSync(new URL('../src/cloud/CloudApp.tsx', import.meta.url), 'utf8')
  assert.match(app, /<TopPickCard /)
  assert.match(app, /<ScoreBars r=\{p\.review\} \/>/)
  assert.match(app, /<ReviewTiles r=\{review\} \/>/)
  assert.match(app, /p\.review\?\.conclusion \?/)  // 名單列先顯示分析師結論，沒有才用一句話
})
