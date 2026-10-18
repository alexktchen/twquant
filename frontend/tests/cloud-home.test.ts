import assert from 'node:assert/strict'
import test from 'node:test'
import { BULLET_TAGS, CARD_META, TONE_NAME, bigMoney, chgSub, headlineTone, lots, parseBullet, ptsSub, signedBig } from '../src/cloud/home.ts'
import { HOME_CARDS } from '../src/cloud/layout.ts'

test('使用者 2026-10-18「像地鐵告示」：每張首頁卡一條路線色＋一個圖示＋短標題；五條路線都有名字', () => {
  for (const c of HOME_CARDS) {
    const m = CARD_META[c.id]
    assert.ok(m, `缺 ${c.id}`)
    assert.ok(m.title.length <= 6, `${c.id} 標題要短：${m.title}`)
    assert.ok(TONE_NAME[m.tone])
  }
  const tones = new Set(Object.values(CARD_META).map((m) => m.tone))
  assert.deepEqual([...tones].sort(), ['event', 'group', 'list', 'quote', 'view'])
})

test('晨報每一點的【標籤】→ 圖示與正文；沒有標籤用旗子', () => {
  assert.deepEqual(parseBullet('【研判】國際盤重挫，台股開盤承壓'), { tag: '研判', icon: 'compass', text: '國際盤重挫，台股開盤承壓' })
  assert.deepEqual(parseBullet('【籌碼】外資連續5日賣超'), { tag: '籌碼', icon: 'landmark', text: '外資連續5日賣超' })
  assert.deepEqual(parseBullet('[觀察] 夜盤逆價差擴大'), { tag: '觀察', icon: 'eye', text: '夜盤逆價差擴大' })
  assert.deepEqual(parseBullet('【未知】x'), { tag: '未知', icon: 'flag', text: 'x' })
  assert.deepEqual(parseBullet('沒有標籤的一句'), { tag: null, icon: 'flag', text: '沒有標籤的一句' })
  assert.ok(Object.keys(BULLET_TAGS).length >= 6)
})

test('數字的小字：漲跌用 ▲▼、點數、億與兆、正負號、口數', () => {
  assert.equal(chgSub(0.0123), '▲ 1.23%')
  assert.equal(chgSub(-0.005), '▼ 0.50%')
  assert.equal(chgSub(0), '— 0.00%')
  assert.equal(chgSub(null), '—')
  assert.equal(ptsSub(-123.4, -0.0027), '▼ 123 0.27%')
  assert.equal(ptsSub(50), '▲ 50')
  assert.equal(bigMoney(1132902805597), '1.13 兆')
  assert.equal(bigMoney(45091589193), '451 億')
  assert.equal(bigMoney(80000000), '8,000 萬')
  assert.equal(signedBig(-45091589193), '−451 億')
  assert.equal(signedBig(10837059624), '+108 億')
  assert.deepEqual(lots(-93675), { value: '93,675', label: '淨空' })
  assert.deepEqual(lots(3197), { value: '3,197', label: '淨多' })
  assert.deepEqual(lots(null), { value: '—', label: '' })
})

test('新聞頭條分類 → 路線色', () => {
  assert.equal(headlineTone('美股雷達'), 'quote')
  assert.equal(headlineTone('台股新聞'), 'list')
  assert.equal(headlineTone('國際政經'), 'quote')
  assert.equal(headlineTone(null), 'view')
})
