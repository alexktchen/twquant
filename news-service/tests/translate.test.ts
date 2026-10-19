import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { validateConfig } from '../src/ingest.ts'
import {
  PROMPT_VERSION,
  check,
  chineseQuantities,
  englishQuantities,
  missingNumbers,
  neurons,
  parseReply,
  passes,
  requestBody,
  foreignTokens,
  normalize,
  untranslatedWords,
  s2tMap,
  simplifiedIn,
  translatePending,
  type Pending,
  type TranslateDeps,
  type TranslationRow,
  type UsageRow,
} from '../src/translate.ts'

const cfg = validateConfig(JSON.parse(readFileSync(new URL('../config/news.json', import.meta.url), 'utf8'))).translate

test('T-401 AC1：英文數量換算（billion→億、trillion→兆、million、%、千分位）', () => {
  assert.deepEqual(
    englishQuantities('EQT strikes $6.5bn deal; China GDP grows 4.5% to $1.2 trillion, 300,000 jobs, 25 million barrels, 2026').map((q) => [q.value, q.pct]),
    [
      [6.5e9, false],
      [4.5, true],
      [1.2e12, false],
      [300000, false],
      [2.5e7, false],
      [2026, false],
    ],
  )
  assert.deepEqual(
    chineseQuantities('EQT以65億美元收購；中國GDP成長4.5%至1.2兆美元，30萬個職缺，2,500萬桶，2026年').map((q) => [q.value, q.pct]),
    [
      [6.5e9, false],
      [4.5, true],
      [1.2e12, false],
      [300000, false],
      [2.5e7, false],
      [2026, false],
    ],
  )
  assert.deepEqual(missingNumbers('Nvidia jumps 4.2% on $3.5 billion deal', '輝達大漲4.2%，交易金額35億美元'), [])
  assert.deepEqual(missingNumbers('Nvidia jumps 4.2% on $3.5 billion deal', '輝達大漲4.2%，交易金額3.5億美元'), ['3.5 billion'], '3.5 billion 寫成 3.5 億要擋')
  assert.deepEqual(missingNumbers('Yields fall 4%', '殖利率下跌4'), ['4%'], '百分比不能變成一般數字')
})

test('T-401 AC2：簡體字擋下；台灣也用的字（台、只、后、里、准、件）不在清單裡', () => {
  assert.deepEqual(simplifiedIn('台积电营收创新高', cfg.s2t_pairs), ['积', '电', '营', '创'])
  assert.deepEqual(simplifiedIn('台積電營收創新高', cfg.s2t_pairs), [])
  const map = s2tMap(cfg.s2t_pairs)
  for (const ch of '台只干里后准云范党布件面才松谷出') assert.ok(!map.has(ch), `「${ch}」繁體也用，不能列進簡體字`)
  assert.equal(map.size, 3795)
  assert.equal(map.has('峰'), false, '台灣標準字形（峰會）不能轉成峯')
  // 2026-10-19 實測模型寫出「首席执行官」「降温」：手寫字表漏了，改用 OpenCC 算出的完整字表
  assert.deepEqual(simplifiedIn('Lindt 首席执行官離職，財報前降温', cfg.s2t_pairs), ['执', '温'])
})

const src: Pending = { id: 'a1', title: 'Nvidia shares jump 4% after $3.5 billion deal', summary: 'The chipmaker rose to a record.', content_sha: 's1' }

test('T-401 AC2：譯文的英文字原文沒有就擋（公司名拼錯）；原文有的、大小寫不同的照過', () => {
  const orig = 'Exclusive: Wooptix targets leading chipmakers; MSCI and FTSE'
  assert.deepEqual(foreignTokens(orig, 'Woopitx 針對晶片廠推進量測技術'), ['Woopitx'])
  assert.deepEqual(foreignTokens(orig, 'Wooptix 進軍晶片廠；msci 與 FTSE'), [])
  assert.equal(passes(check(cfg, { id: 'w', title: orig, summary: null, content_sha: 'x' }, { id: 'w', title_zh: 'Woopitx 進軍晶片廠，MSCI', summary_zh: '' })), false)
})

test('T-401：自動修正——簡體字轉繁體、對岸用語換台灣用語、英文名拼錯一兩個字母改回原文；有兩個候選就不猜', () => {
  const s = { id: 'x', title: 'Wooptix targets chipmakers; Lindt CEO exits', summary: null }
  const n = normalize(cfg, s, { id: 'x', title_zh: 'Woopix 與 Lindt 首席执行官離職，国内需求降温', summary_zh: '' })
  assert.equal(n.t.title_zh, 'Wooptix 與 Lindt 執行長離職，國內需求降溫')
  assert.deepEqual(n.fixes.sort(), ['Woopix→Wooptix', '国→國', '内→內', '执→執', '温→溫', '首席執行官→執行長'].sort())
  const two = normalize(cfg, { id: 'y', title: 'Alpha Corp and Alphb Inc', summary: null }, { id: 'y', title_zh: 'Alphx 公司', summary_zh: '' })
  assert.equal(two.t.title_zh, 'Alphx 公司', '兩個都只差一字：不猜')
})

test('T-401 AC2：留著沒翻的英文一般字詞就擋；專有名詞、縮寫、iPhone 不算；第三季自動改成第3季', () => {
  assert.deepEqual(untranslatedWords('日本首相立憲 rejects 貶值日圓與高利率批評'), ['rejects'])
  assert.deepEqual(untranslatedWords('MSCI、FTSE、iPhone、Huawei-JAC Maextro、5G、AI'), [])
  const q = normalize(cfg, { id: 'q', title: "China's Q3 GDP narrowly beats forecasts", summary: null }, { id: 'q', title_zh: '中國第三季GDP小幅優於預期', summary_zh: '' })
  assert.equal(q.t.title_zh, '中國第3季GDP小幅優於預期')
  assert.ok(q.fixes.includes('第三季→第3季'))
  assert.equal(passes(check(cfg, { id: 'q', title: "China's Q3 GDP narrowly beats forecasts", summary: null }, q.t, q.fixes)), true)
})

test('T-401 AC3：只記錄的檢查不擋（方向相反、中文數字、對岸用語、多出的數字）', () => {
  const c = check(cfg, src, { id: 'a1', title_zh: '輝達股價下跌4%，35億美元交易後芯片股重挫七成，10月', summary_zh: '這家晶片廠創新高。' })
  assert.equal(c.direction_flip, true)
  assert.deepEqual(c.chinese_numbers, ['七成'])
  assert.deepEqual(c.mainland_terms, ['芯片'])
  assert.deepEqual(c.extra_numbers, ['10'])
  assert.equal(passes(c), true, '這些都只記錄')
  assert.equal(passes(check(cfg, src, { id: 'a1', title_zh: '輝達大漲4%', summary_zh: '' })), false, '少了 35 億就擋')
})

test('T-401：請求——指令在 system（含術語表與 /no_think）、資料在 user；回應解析與記帳', () => {
  const body = requestBody(cfg, [src]) as { messages: { role: string; content: string }[]; response_format: { type: string } }
  assert.equal(body.messages[0].role, 'system')
  assert.ok(body.messages[0].content.includes('Nvidia＝輝達') && body.messages[0].content.endsWith(' /no_think'))
  assert.ok(!body.messages[0].content.includes('3.5 billion deal'), '新聞內容不能進 system')
  assert.deepEqual(JSON.parse(body.messages[1].content), { items: [{ id: 'a1', title: src.title, summary: src.summary }] })
  assert.equal(body.response_format.type, 'json_schema')

  const ok = parseReply({ success: true, result: { response: '<think>x</think>```json\n{"items":[{"id":"a1","title_zh":" 輝達 ","summary_zh":""}]}\n```', usage: { prompt_tokens: 1000, completion_tokens: 100 } } })
  assert.deepEqual(ok, { items: [{ id: 'a1', title_zh: '輝達', summary_zh: '' }], inputTokens: 1000, outputTokens: 100 })
  const obj = parseReply({ success: true, result: { choices: [{ message: { content: { items: [] } } }], usage: { prompt_tokens: 1, completion_tokens: 1 } } })
  assert.deepEqual(obj.items, [])
  assert.throws(() => parseReply({ success: false, errors: [{ code: 4006 }] }), /4006/)
  assert.throws(() => parseReply({ success: true, result: { response: '{"items":[]}' } }), /usage/)
  assert.throws(() => parseReply({ success: true, result: { response: null, usage: { prompt_tokens: 1, completion_tokens: 3000 } } }), /內容為空/)
  assert.ok(Math.abs(neurons(cfg, 1_000_000, 100_000) - (4625 + 3047.5)) < 1e-9)
})

// ── 流程：假的模型與資料庫 ─────────────────────────────────────────────────

function world(replies: ((body: Record<string, unknown>) => unknown)[], opts: { used?: number; pending?: Pending[] } = {}) {
  const saved: TranslationRow[] = []
  const usage: UsageRow[] = []
  const calls: string[][] = []
  const deps: TranslateDeps = {
    now: () => new Date('2026-10-19T15:00:00Z'),
    pending: async (n) => (opts.pending ?? [src]).slice(0, n),
    usedToday: async () => opts.used ?? 0,
    ai: async (body) => {
      const user = JSON.parse((body.messages as { content: string }[])[1].content) as { items: { id: string }[] }
      calls.push(user.items.map((i) => i.id))
      const r = replies.shift()
      if (!r) throw new Error('沒有更多假回應')
      return r(body)
    },
    save: async (rows, u) => {
      saved.push(...rows)
      usage.push(...u)
    },
  }
  return { deps, saved, usage, calls }
}
const reply = (items: { id: string; title_zh: string; summary_zh?: string }[]) => () => ({
  success: true,
  result: { response: JSON.stringify({ items: items.map((i) => ({ summary_zh: '', ...i })) }), usage: { prompt_tokens: 800, completion_tokens: 200 } },
})

test('T-401 AC4：一批翻完、記用量；沒過的那則單獨重翻一次，過了就用', async () => {
  const b: Pending = { id: 'b2', title: 'Oil falls 2%', summary: null, content_sha: 's2' }
  const w = world(
    [
      reply([
        { id: 'a1', title_zh: '輝達大漲4%', summary_zh: '35億美元交易後晶片廠創新高' },
        { id: 'b2', title_zh: '油價下跌' },
      ]),
      reply([{ id: 'b2', title_zh: '油價下跌2%' }]),
    ],
    { pending: [src, b] },
  )
  const r = await translatePending(cfg, w.deps)
  assert.deepEqual(r, { translated: 2, failed: 0, budget_stop: false })
  assert.deepEqual(w.calls, [['a1', 'b2'], ['b2']])
  assert.deepEqual(
    w.saved.map((s) => [s.item_id, s.status, s.title_zh, s.content_sha, s.prompt_version]),
    [
      ['a1', 'ok', '輝達大漲4%', 's1', PROMPT_VERSION],
      ['b2', 'ok', '油價下跌2%', 's2', PROMPT_VERSION],
    ],
  )
  assert.equal(w.usage.length, 2)
  assert.equal(w.usage[0].task, 'news_translate')
})

test('T-401 AC4：重翻還是不過 → failed，不放譯文（網站顯示原文標「未譯」）；整批壞掉 → 每則各自重試', async () => {
  const w = world([reply([{ id: 'a1', title_zh: '輝达大涨' }]), reply([{ id: 'a1', title_zh: '輝达大涨' }])])
  const r = await translatePending(cfg, w.deps)
  assert.deepEqual(r, { translated: 0, failed: 1, budget_stop: false })
  assert.equal(w.saved[0].status, 'failed')
  assert.equal(w.saved[0].title_zh, null)
  const c = w.saved[0].checks
  assert.ok('fixes' in c && c.fixes.includes('达→達') && c.missing_numbers.length > 0, '簡體字自動轉了，但少了數字照樣擋')

  const w2 = world([
    () => {
      throw new Error('逾時')
    },
    reply([{ id: 'a1', title_zh: '輝達大漲4%', summary_zh: '35億美元' }]),
  ])
  const r2 = await translatePending(cfg, w2.deps)
  assert.deepEqual(r2, { translated: 1, failed: 0, budget_stop: false })
  assert.deepEqual(w2.calls, [['a1'], ['a1']])
})

test('T-401 AC4：今天的額度用完就停、不呼叫模型、不寫任何一列（剩下的下次翻）', async () => {
  const w = world([], { used: cfg.daily_neurons })
  const r = await translatePending(cfg, w.deps)
  assert.deepEqual(r, { translated: 0, failed: 0, budget_stop: true })
  assert.equal(w.calls.length, 0)
  assert.equal(w.saved.length, 0)
})
