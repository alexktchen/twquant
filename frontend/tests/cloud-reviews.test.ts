import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { itemsOf, reviewOf } from '../functions/_lib/reviews.ts'
import { DIMS, itemNote, REVIEW_NOTE, reviewNote, scoreText, stanceTone, topOf } from '../src/cloud/pickreview.ts'

test('T-394 四面向評分（舊資料，scale 空）：四個分數都在 1～5 才顯示；首選才帶理由', () => {
  const base = {
    news: 3, tech: 5, chips: 5, fund: 5, is_top: true, top_reason: '創 52 週新高', source: 'model',
    news_view: '近期無重大消息', tech_view: ' 創 52 週新高，均線多頭排列 ', chips_view: null, fund_view: '', stance: '偏多', conclusion: '技術與基本面雙雙走強，可觀察回檔機會',
  }
  assert.deepEqual(reviewOf(base), {
    news: 3, tech: 5, chips: 5, fund: 5, total: 18, max: 5, items: [], top: true, reason: '創 52 週新高', ai: true,
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
  assert.equal(scoreText({ news: 3, tech: 5, chips: 4, fund: 2, total: 14, max: 5, items: [], top: false, reason: null, ai: true, ...empty }), '消息 3、技術 5、籌碼 4、基本 2（共 14／20）')
  assert.equal(scoreText({ news: 5, tech: 8, chips: 4, fund: 6, total: 23, max: 10, items: [], top: false, reason: null, ai: true, ...empty }), '消息 5、技術 8、籌碼 4、基本 6（共 23／40）')
  assert.match(REVIEW_NOTE, /沒有驗證過/)
  assert.deepEqual(['偏多', '中性', '偏空', null].map(stanceTone), ['up', 'flat', 'down', 'flat'])
  const v = { news: null, tech: null, chips: null, fund: null }
  const r = { news: 3, tech: 5, chips: 5, fund: 5, total: 18, max: 5, items: [], top: true, reason: 'x', ai: true, views: v, stance: null, conclusion: null }
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

test('T-426 10 分制（D-206）：scale＝10 收 0～10、帶 20 個小項；主力賣超的說明不說成會跌', () => {
  const items = [
    { dim: '籌碼', name: '主力', points: -1, text: '主力近 5 日賣超 3,000 張' },
    { dim: '技術', name: '均線', points: 1, text: '收盤站在 5、20、60 日均線之上' },
    { dim: '技術', name: '亂', points: 2, text: 'x' },
    { dim: '別的', name: 'y', points: 0, text: 'z' },
  ]
  const row = { news: 5, tech: 10, chips: 0, fund: 6, is_top: false, top_reason: null, source: 'model', scale: '10', items: JSON.stringify(items) }
  const v = reviewOf(row)
  assert.ok(v)
  assert.equal(v.max, 10)
  assert.equal(v.total, 21)
  assert.deepEqual(v.items.map((i) => [i.dim, i.name, i.points]), [['chips', '主力', -1], ['tech', '均線', 1]])  // 格式不對的丟掉
  assert.equal(reviewOf({ ...row, tech: 11 }), null)
  assert.equal(reviewOf({ ...row, scale: null }), null)  // 舊量表 0 分不合法
  assert.deepEqual(itemsOf('不是 JSON'), [])
  assert.match(reviewNote(v), /規則算.*5 分（中性）.*沒有驗證過/)
  assert.equal(reviewNote({ max: 5 }), REVIEW_NOTE)
  assert.match(itemNote(v.items[0]) ?? '', /不代表會跌/)
  assert.equal(itemNote(v.items[1]), null)
  const api = readFileSync(new URL('../functions/_lib/reviews.ts', import.meta.url), 'utf8')
  assert.match(api, /to_jsonb\(r\) ->> 'scale'/)  // 舊表還沒加欄也讀得到
  const ui = readFileSync(new URL('../src/cloud/Review.tsx', import.meta.url), 'utf8')
  assert.match(ui, /看 \{items\.length\} 項怎麼算/)
})
