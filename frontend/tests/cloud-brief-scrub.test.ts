// T-435／D-212（使用者 2026-10-22：「首頁那邊的名單跟該擋的都要擋」）：首頁「今日一句話」（晨報／盤後快訊）不能露出名單。
// 新的晨報不讀名單（tests/test_morning_brief.py 守著）；已經存起來的舊晨報，沒有名單權限的人讀到的是拿掉名單痕跡的版本。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { scrubBrief } from '../functions/_lib/brief-scrub.ts'

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')

// 2026-10-21 那種舊晨報：規則句、事實、模型的句子都有名單
const old = {
  date: '2026-10-21',
  edition: 'morning',
  rule_text: '美股收紅；資金流入 半導體、航運；今日名單 大盤精選 15 檔（新增 3）、潛伏飆股 12 檔（新增 2）。',
  headline: '外資賣壓未歇，台股開盤恐承壓',
  bullets: [
    '【研判】國際盤走高、夜盤逆價差收斂，台股可望開高',
    '【觀察】留意 1234 黑松自結財報公告',
    '【資金】名單上被動元件族群資金流入',
    '【觀察】8084 巨虹列變更交易，留意處置',
  ],
  risk_note: '名單股巨虹列變更交易方法',
  facts: [
    '2026-10-20 加權指數 -1.23%，收 27,000 點',
    '今天名單「大盤精選」15 檔，其中新增 3 檔',
    '重大訊息 1234 黑松：公告本公司115年8月自結合併財務報告（名單）',
    '重大訊息 8084 巨虹：本公司接獲通知列為變更交易方法（名單、變更交易／下市）',
    '重大訊息 9999 測試：董事會決議現金增資（減資／增資／私募）',
  ],
  extras: null,
  adr_premium: 0.28,
}

test('T-435 AC1 舊晨報給沒有名單權限的人：名單檔數、「名單」標記、只因在名單才挑進來的公告與提到它的句子都拿掉', () => {
  const s = scrubBrief(old)!
  assert.equal(s.rule_text, '美股收紅；資金流入 半導體、航運。')
  assert.deepEqual(s.facts, [
    '2026-10-20 加權指數 -1.23%，收 27,000 點',
    '重大訊息 8084 巨虹：本公司接獲通知列為變更交易方法（變更交易／下市）', // 本來就會因為「變更交易」挑進來，只拿掉標記
    '重大訊息 9999 測試：董事會決議現金增資（減資／增資／私募）',
  ])
  // 黑松只因為在名單上才進晨報 → 提到它的那點也拿掉；提到「名單」的拿掉；巨虹本來就會進晨報，提到它的留著
  assert.deepEqual(s.bullets, ['【研判】國際盤走高、夜盤逆價差收斂，台股可望開高', '【觀察】8084 巨虹列變更交易，留意處置'])
  assert.equal(s.headline, '外資賣壓未歇，台股開盤恐承壓')
  assert.equal(s.risk_note, null)
  assert.equal(JSON.stringify(s).includes('名單'), false)
  assert.equal(JSON.stringify(s).includes('黑松'), false)
  assert.equal(s.adr_premium, 0.28) // 其他欄位照舊
})

test('T-435 AC1 新晨報（不讀名單）原樣通過；null、舊資料沒有的欄位不出錯；規則句只剩名單時用沒資料的句子', () => {
  const clean = { ...old, rule_text: '美股收紅。', bullets: ['【研判】台股可望開高'], risk_note: '外資持續調節', facts: ['2026-10-20 加權指數 -1.23%，收 27,000 點'] }
  assert.deepEqual(scrubBrief(clean), clean)
  assert.equal(scrubBrief(null), null)
  const bare = scrubBrief({ date: '2026-10-18', rule_text: '今日名單 大盤精選 15 檔（新增 3）。', headline: null, bullets: null, risk_note: null, facts: null })!
  assert.equal(bare.rule_text, '今天沒有可用的資料。')
  assert.equal(bare.facts, null)
  assert.equal(bare.bullets, null)
  const headline = scrubBrief({ ...old, headline: '名單股集中在被動元件' })!
  assert.equal(headline.headline, null) // 網站就改顯示規則句
})

test('T-435 AC2 首頁與晨報歷史兩支端點：沒有名單權限就給拿掉痕跡的版本，兩種內容分開暫存', () => {
  const war = read('functions/api/warroom/index.ts')
  assert.match(war, /const brief = withPicks \? latest : scrubBrief\(latest\)/)
  const hist = read('functions/api/warroom/brief.ts')
  assert.match(hist, /const full = await allowed\(ctx\.request, 'picks'\)/)
  assert.match(hist, /cached\(gatedKey\(cacheKey\(ctx\.request\), full\)/)
  assert.match(hist, /const brief = full \? stored : scrubBrief\(stored\)/)
  assert.equal(hist.match(/cached\(/g)?.length, 1)
})

test('T-435 AC3 沒登入時首頁的說明也不提名單（名單只給 admin；族群資金已搬到盤勢）', () => {
  const app = read('src/cloud/CloudApp.tsx')
  const gate = /<Gate title="登入後看今日戰情" text="([^"]*)"/.exec(app)
  assert.ok(gate)
  assert.doesNotMatch(gate[1], /名單|族群/)
})
