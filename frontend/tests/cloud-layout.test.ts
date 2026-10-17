import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { CARD_ID_RE, LAYOUT_MAX_CARDS, parseLayout } from '../functions/_lib/layout.ts'
import { HOME_CARDS, defaultLayout, initialLayout, normalizeLayout, sameLayout, toggleCollapsed, withOrder, type CardId } from '../src/cloud/layout.ts'
import { CAL_KEY, HEADS_KEY, HEAT_KEY, MOOD_KEY, NEWS_KEY } from '../src/cloud/prefs.ts'

const ids = HOME_CARDS.map((c) => c.id)

test('使用者 2026-10-17：首頁版面預設——每張卡一次、熱力圖在最下面、新聞頭條與行事曆預設收起', () => {
  const d = defaultLayout()
  assert.deepEqual(d.order, ids)
  assert.equal(new Set(d.order).size, d.order.length)
  assert.equal(d.order.at(-1), 'heat')
  assert.deepEqual(d.collapsed, ['heads', 'cal'])
  assert.ok(ids.every((id) => CARD_ID_RE.test(id)), '網站的卡片代號都符合伺服器的格式')
  assert.ok(ids.length <= LAYOUT_MAX_CARDS)
})

test('使用者 2026-10-17：存起來的版面——不認得的丟掉、重複只留一個、以後新增的卡插在預設位置旁、壞資料用預設', () => {
  const saved = { order: ['heat', 'brief', 'bogus', 'brief', 'news'], collapsed: ['heat', 'bogus', 'mood'] }
  const l = normalizeLayout(saved)
  // 存的時候只有 heat、brief、news：其他卡插在預設順序裡前一張卡之後
  assert.deepEqual(l.order.slice(0, 2), ['heat', 'brief'])
  assert.equal(l.order.indexOf('heads'), l.order.indexOf('brief') + 1, '新聞頭條接在今日一句話後面')
  assert.equal(l.order.length, ids.length)
  assert.deepEqual([...l.order].sort(), [...ids].sort())
  // 收起：存的（heat、mood）＋新插入的卡用預設（heads、cal 預設收起）
  assert.deepEqual(new Set(l.collapsed), new Set(['heat', 'mood', 'heads', 'cal']))
  assert.deepEqual(l.collapsed, l.order.filter((id) => l.collapsed.includes(id)), '收起清單照順序排')
  for (const bad of [null, 'x', { order: 'brief' }, {}]) assert.ok(sameLayout(normalizeLayout(bad), defaultLayout()))
})

test('使用者 2026-10-17：收起↔展開、換順序', () => {
  const d = defaultLayout()
  const t = toggleCollapsed(d, 'brief')
  assert.ok(t.collapsed.includes('brief'))
  assert.ok(!toggleCollapsed(t, 'brief').collapsed.includes('brief'))
  assert.deepEqual(d.collapsed, ['heads', 'cal'], '原本那份不變')
  const order = ['heat', ...ids.filter((x) => x !== 'heat')] as CardId[]
  const w = withOrder(t, order)
  assert.equal(w.order[0], 'heat')
  assert.deepEqual(new Set(w.collapsed), new Set(t.collapsed))
})

test('使用者 2026-10-17：打開首頁——有存版面就用；沒有就沿用改版前各卡記的展開／收起', () => {
  const stored = JSON.stringify({ order: ['cal', ...ids.filter((x) => x !== 'cal')], collapsed: [] })
  assert.equal(initialLayout(stored, () => null).order[0], 'cal')
  assert.ok(sameLayout(initialLayout('{壞掉', () => null), defaultLayout()))
  const legacy: Record<string, string> = { [HEAT_KEY]: '0', [HEADS_KEY]: '1', [MOOD_KEY]: '0', [NEWS_KEY]: '1', [CAL_KEY]: '1' }
  const l = initialLayout(null, (k) => legacy[k] ?? null)
  assert.deepEqual(new Set(l.collapsed), new Set(['heat', 'mood']), '以前收起熱力圖、市場氛圍，打開新聞頭條、行事曆')
  assert.ok(sameLayout(initialLayout(null, () => null), defaultLayout()))
})

test('使用者 2026-10-17：伺服器只收格式對的版面', () => {
  assert.deepEqual(parseLayout({ order: ['brief', 'heat'], collapsed: ['heat'] }), { order: ['brief', 'heat'], collapsed: ['heat'] })
  assert.deepEqual(parseLayout({ order: ['brief'] }), { order: ['brief'], collapsed: [] }, '沒給 collapsed＝都展開')
  assert.equal(parseLayout({ order: [], collapsed: [] }), null, '至少一張')
  assert.equal(parseLayout({ order: ['brief', 'brief'], collapsed: [] }), null, '重複')
  assert.equal(parseLayout({ order: ['Brief'], collapsed: [] }), null, '大寫不收')
  assert.equal(parseLayout({ order: ['brief'], collapsed: ['heat'] }), null, '收起的卡要在順序裡')
  assert.equal(parseLayout({ order: ['brief', 1], collapsed: [] }), null)
  assert.equal(parseLayout({ order: Array.from({ length: LAYOUT_MAX_CARDS + 1 }, (_, i) => `c${'x'.repeat((i % 10) + 1)}${String.fromCharCode(97 + Math.floor(i / 10))}`) }), null, '太多張')
  assert.equal(parseLayout(null), null)
  assert.equal(parseLayout('brief'), null)
})

test('使用者 2026-10-17：版面端點——身分取自登入憑證、寫入只收本站、可恢復預設；隱私權政策寫明存什麼', () => {
  const api = readFileSync(new URL('../functions/api/layout.ts', import.meta.url), 'utf8')
  for (const s of ['accessEmail(request)', 'onRequestGet', 'onRequestPut', 'onRequestDelete', 'ensureLayout(db)', 'parseLayout(body)', 'LAYOUT_BODY_MAX'])
    assert.ok(api.includes(s), s)
  assert.equal(api.match(/sameOrigin\(request\)/g)?.length, 2, 'PUT 與 DELETE 都擋跨站')
  const privacy = readFileSync(new URL('../public/privacy.html', import.meta.url), 'utf8')
  assert.ok(privacy.includes('首頁版面'))
})
