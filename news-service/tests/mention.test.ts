import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { validateConfig } from '../src/ingest.ts'
import {
  buildIndex,
  detectMentions,
  detectPending,
  type EntityAliases,
  type MentionConfig,
  type MentionDeps,
  type MentionRun,
  type MentionSaveHit,
  type PendingItem,
} from '../src/mention.ts'

const config = validateConfig(JSON.parse(readFileSync(new URL('../config/news.json', import.meta.url), 'utf8')))
const cfg: MentionConfig = config.mention

const entities: EntityAliases[] = [
  { id: 'tw:2618', label: '長榮航', aliases: ['長榮航', '長榮航空'] },
  { id: 'tw:2603', label: '長榮', aliases: ['長榮'] },
  { id: 'tw:1216', label: '統一', aliases: ['統一'] },
  { id: 'tw:6505', label: '台塑化', aliases: ['台塑化'] },
  { id: 'tw:1301', label: '台塑', aliases: ['台塑'] },
  { id: 'tw:2330', label: '台積電', aliases: ['台積電'] },
  { id: 'us:AAPL', label: '蘋果', aliases: ['Apple', 'AAPL', '蘋果'] },
  { id: 'us:NVDA', label: '輝達', aliases: ['Nvidia', 'NVIDIA', 'NVDA', '輝達', '英偉達'] },
  { id: 'us:META', label: 'Meta', aliases: ['Meta', 'META', 'Meta Platforms'] },
  { id: 'us:ON', label: '安森美', aliases: ['ON', 'onsemi', 'ON Semiconductor'] },
  { id: 'us:MU', label: '美光', aliases: ['Micron', '美光', 'MU'] },
  { id: 'co:intel', label: '英特爾', aliases: ['Intel', 'IN'] },
  { id: 'th:ai', label: 'AI', aliases: ['AI', 'Ai', '人工智慧'] },
  { id: 'tw:9999', label: '華', aliases: ['華', 'Xy'] },
]
const index = buildIndex(entities, cfg)
const ids = (texts: Array<string | null | undefined>) => detectMentions(texts, index).map((h) => h.entity)

test('T-402：設定的 mention／graph 兩段缺了或型別不對 → 丟錯', () => {
  assert.match(cfg.rule_version, /^\d{4}-\d{2}-\d{2}\.\d+$/, '規則版本像 2026-10-20.1；改了比對規則就要加一號（表的主鍵含它，會自動重掃）')
  assert.throws(() => validateConfig({ ...config, mention: { ...cfg, blockers: [''] } }), /mention\.blockers/)
  assert.ok(cfg.stopwords.includes('ON'), 'YAML 的 ON 要加引號，不然會變成 true')
  assert.throws(() => validateConfig({ ...config, mention: undefined }), /news 設定錯誤：mention/)
  assert.throws(() => validateConfig({ ...config, mention: { ...cfg, latin_min: 0 } }), /mention\.latin_min/)
  assert.throws(() => validateConfig({ ...config, mention: { ...cfg, stopwords: 'ON' } }), /mention\.stopwords/)
  assert.throws(() => validateConfig({ ...config, mention: { ...cfg, rule_version: '' } }), /mention\.rule_version/)
  assert.throws(() => validateConfig({ ...config, graph: undefined }), /news 設定錯誤：graph/)
  assert.throws(() => validateConfig({ ...config, graph: { ...config.graph, weights: { ...config.graph.weights, peer: '0.4' } } }), /graph\.weights\.peer/)
  assert.throws(() => validateConfig({ ...config, graph: { ...config.graph, peers_per_group: 1.5 } }), /graph\.peers_per_group/)
  assert.throws(() => validateConfig({ ...config, graph: { ...config.graph, mentions_per_item: 0 } }), /graph\.mentions_per_item/)
})

test('T-402：中文長名先比、命中的範圍不再給短名（長榮航空 吃掉 長榮；台塑化 先於 台塑）', () => {
  // 拿掉 stopwords，只看長短名的規則
  const all = buildIndex(entities, { ...cfg, stopwords: [] })
  const ids2 = (texts: string[]) => detectMentions(texts, all).map((h) => h.entity)
  assert.deepEqual(ids2(['長榮航空第3季獲利創高']), ['tw:2618'])
  assert.deepEqual(ids2(['台塑化調降油價']), ['tw:6505'])
  // 短名出現在別處照樣命中
  assert.deepEqual(ids2(['台塑化與台塑同步調價']), ['tw:6505', 'tw:1301'])
  assert.deepEqual(ids2(['長榮航空、長榮同步上漲']), ['tw:2618', 'tw:2603'])
  // 中文是子字串比對，不需要字界
  assert.deepEqual(ids(['外資買超台積電3萬張']), ['tw:2330'])
})

test('T-402：英文大小寫要一樣、前後要字界', () => {
  assert.deepEqual(ids(['Apple unveils new iPhone']), ['us:AAPL'])
  assert.deepEqual(ids(['apple prices rise']), [], '小寫 apple 不算')
  assert.deepEqual(ids(['Pineapple farms expand']), [], '字中間不算')
  assert.deepEqual(ids(['Applesauce maker']), [], '後面接字母不算')
  assert.deepEqual(ids(["Apple's supplier"]), ['us:AAPL'], '撇號是字界')
  assert.deepEqual(ids(['Nvidia-backed startup']), ['us:NVDA'], '連字號是字界')
  assert.deepEqual(ids(['NVIDIA earnings']), ['us:NVDA'])
  assert.deepEqual(ids(['nvidia earnings']), [])
  assert.deepEqual(ids(['輝達NVDA盤後漲5%']), ['us:NVDA'], '中文字旁邊的 ticker 也算字界')
  assert.deepEqual(ids(['Micron beats forecasts']), ['us:MU'])
  assert.deepEqual(ids(['micron-scale chips']), [])
})

test('T-402：全大寫 2～5 字是 ticker（短於 latin_min 也建，要字界）；其他太短的拉丁別名不建', () => {
  assert.deepEqual(ids(['MU shares jump']), ['us:MU'])
  assert.deepEqual(ids(['MUST read']), [], 'ticker 也要字界')
  assert.deepEqual(ids(['AI spending surges']), ['th:ai'])
  assert.deepEqual(ids(['SAID the chairman']), [])
  assert.deepEqual(ids(['Ai is here']), [], 'Ai 不是全大寫、又短於 latin_min → 不建')
  assert.deepEqual(ids(['Xy corp']), [], '同上')
  assert.deepEqual(ids(['華碩']), [], '一個字的中文名短於 cjk_min → 不建')
  assert.deepEqual(ids(['IN focus']), ['co:intel'], 'IN 是兩字全大寫 → 當 ticker')
})

test('T-402：stopwords 整條別名不建索引（ON、META、統一），但同實體的其他別名照常；真實公司名（長榮）不是停用詞', () => {
  assert.deepEqual(ids(['Shares turn ON after data']), [])
  assert.deepEqual(ids(['ON Semiconductor cuts outlook']), ['us:ON'], '包含 stopword 的長名不受影響')
  assert.deepEqual(ids(['META falls']), [])
  assert.deepEqual(ids(['Meta falls']), ['us:META'])
  assert.deepEqual(ids(['兩岸統一議題']), [], '統一在 stopwords（多半不是指那家公司）')
  assert.deepEqual(ids(['長榮海運運價']), ['tw:2603'], '長榮是真實公司，2026-10-20 起不放停用詞')
})

test('T-402：同一實體多個別名、出現多次 → 只回一次（第一個命中）；結果依出現位置排', () => {
  const hits = detectMentions(['輝達與台積電合作，Nvidia 執行長黃仁勳說 NVIDIA 將…', null, 'Nvidia and TSMC', 'NVDA'], index)
  assert.deepEqual(
    hits.map((h) => [h.entity, h.alias]),
    [
      ['us:NVDA', '輝達'],
      ['tw:2330', '台積電'],
    ],
  )
  // 位置：前面的文字先、同一段裡前面的先
  assert.deepEqual(ids(['台積電供貨給蘋果', null, 'Micron']), ['tw:2330', 'us:AAPL', 'us:MU'])
  assert.deepEqual(ids([null, null, 'Micron and Apple']), ['us:MU', 'us:AAPL'])
})

test('T-402：evidence＝命中字串前後各最多 20 字，不含換行', () => {
  const pre = '一二三四五六七八九十甲乙丙丁戊己庚辛壬癸子丑'
  const post = '寅卯辰巳午未申酉戌亥ABCDEFGHIJKLMN'
  const [h] = detectMentions([`${pre}台積電${post}`], index)
  assert.equal(h.evidence, `${[...pre].slice(-20).join('')}台積電${[...post].slice(0, 20).join('')}`)
  assert.equal([...h.evidence].length, 43)
  const [s] = detectMentions(['外資\n買超台積電\r\n漲停'], index)
  assert.equal(s.evidence, '外資 買超台積電 漲停')
  assert.ok(!/[\r\n]/.test(s.evidence))
  // 前面不足 20 字就取到開頭為止
  assert.equal(detectMentions(['Apple rises'], index)[0].evidence, 'Apple rises')
})

test('T-402：空文字、null、undefined 都跳過，沒有命中回空陣列', () => {
  assert.deepEqual(detectMentions([], index), [])
  assert.deepEqual(detectMentions([null, undefined, ''], index), [])
  assert.deepEqual(detectMentions(['今天天氣很好'], index), [])
  assert.deepEqual(detectMentions(['台積電'], buildIndex([], cfg)), [])
})

test('T-402：同輸入同輸出（別名表順序不影響結果）', () => {
  const texts = ['長榮航空與台塑化、台積電', null, 'Apple and Nvidia', 'MU']
  const a = detectMentions(texts, buildIndex(entities, cfg))
  const b = detectMentions(texts, buildIndex([...entities].reverse(), cfg))
  assert.deepEqual(a, b)
})

test('T-402 detectPending：兩則待比對（一則命中、一則 0 命中）→ 都寫 mention_runs，命中的寫 mentions', async () => {
  const pending: PendingItem[] = [
    { item_id: 'a1', content_sha: 'sha-a', title: 'Nvidia and Apple rally', summary: null, title_zh: '輝達與蘋果大漲', summary_zh: '輝達領漲' },
    { item_id: 'b2', content_sha: 'sha-b', title: 'Oil prices fall', summary: 'Brent drops', title_zh: '油價下跌', summary_zh: null },
  ]
  const calls: { limit: number[]; entities: number; save: Array<{ hits: MentionSaveHit[]; runs: MentionRun[]; rv: string }> } = { limit: [], entities: 0, save: [] }
  const deps: MentionDeps = {
    now: () => new Date('2026-10-19T08:00:00Z'),
    entities: async () => {
      calls.entities++
      return entities
    },
    pending: async (limit) => {
      calls.limit.push(limit)
      return pending
    },
    save: async (hits, runs, rv) => {
      calls.save.push({ hits, runs, rv })
    },
  }
  const r = await detectPending(cfg, deps)
  assert.deepEqual(r, { scanned: 2, hits: 2 })
  assert.deepEqual(calls.limit, [cfg.per_run_limit])
  assert.equal(calls.save.length, 1)
  const [s] = calls.save
  assert.equal(s.rv, cfg.rule_version)
  assert.deepEqual(
    s.hits.map((h) => [h.item_id, h.content_sha, h.entity]),
    [
      ['a1', 'sha-a', 'us:NVDA'],
      ['a1', 'sha-a', 'us:AAPL'],
    ],
  )
  assert.equal(s.hits[0].evidence, '輝達與蘋果大漲')
  assert.deepEqual(s.runs, [
    { item_id: 'a1', content_sha: 'sha-a', n: 2 },
    { item_id: 'b2', content_sha: 'sha-b', n: 0 },
  ])

  // 沒有待比對的 → 不讀實體表、不寫
  const empty = { ...deps, pending: async () => [], entities: async () => assert.fail('不該讀實體表'), save: async () => assert.fail('不該寫') }
  assert.deepEqual(await detectPending(cfg, empty), { scanned: 0, hits: 0 })
})

test('T-402：擋字先佔掉那段字、不算提到任何實體（2026-10-20 抽查：東南亞→南亞、生產力成長→力成）', () => {
  const cfg2 = { rule_version: 'x', per_run_limit: 10, latin_min: 3, cjk_min: 2, stopwords: [], blockers: ['東南亞', '生產力'] }
  const idx = buildIndex(
    [
      { id: 'tw:1303', label: '南亞', aliases: ['南亞'] },
      { id: 'tw:6239', label: '力成', aliases: ['力成'] },
    ],
    cfg2,
  )
  const got = (t: string) => detectMentions([t], idx).map((h) => h.entity)
  assert.deepEqual(got('東南亞上市潮'), [])
  assert.deepEqual(got('製造商需維持生產力成長'), [])
  assert.deepEqual(got('南亞塑膠調漲報價、力成擴產'), ['tw:1303', 'tw:6239'], '真的提到照樣命中')
})

test('2026-10-20 二次抽查的錯命中都擋掉，真的提到照樣命中（巨大、佳能、和成、國產、綠電、懷特、惠特、基米、亞航）', () => {
  const tw = [
    { id: 'tw:9921', label: '巨大', aliases: ['巨大'] },
    { id: 'tw:2374', label: '佳能', aliases: ['佳能'] },
    { id: 'tw:1810', label: '和成', aliases: ['和成'] },
    { id: 'tw:2504', label: '國產', aliases: ['國產'] },
    { id: 'tw:8440', label: '綠電', aliases: ['綠電'] },
    { id: 'tw:4108', label: '懷特', aliases: ['懷特'] },
    { id: 'tw:6706', label: '惠特', aliases: ['惠特'] },
    { id: 'tw:6827', label: '基米-創', aliases: ['基米-創', '基米'] },
    { id: 'tw:2630', label: '亞航', aliases: ['亞航'] },
  ]
  const idx = buildIndex(tw, cfg)
  const hit = (s: string) => detectMentions([s], idx).map((h) => h.entity)
  for (const wrong of [
    '的1% Anthropic 股權即將帶來巨大收益',
    '半導體製造需要巨大的前期資本',
    '這份報告上周在市場上引起巨大震撼',
    '佳能台灣看好2026年相機需求',
    '佳能行銷台灣董事長胡大剛表示，全球相機市場在',
    'Anthropic的風險投資和成長關係',
    '中國國產模型Flash賽道爭霸戰',
    '台灣啟動首個綠電時移概念驗證計畫',
    '懷特表示歐洲應釋出更多柴油',
    '梅里迪思·惠特尼稱零售商將面臨困難',
    '俄羅斯總統弗拉基米爾·普丁表示',
    '馬來西亞安華：未同意對亞航提供財務支持',
  ])
    assert.deepEqual(hit(wrong), [], wrong)
  assert.deepEqual(hit('自行車雙雄巨大、美利達9月營收同步走弱'), ['tw:9921'])
  assert.deepEqual(hit('巨大9月營收年減'), ['tw:9921'])
  assert.deepEqual(hit('惠特獲大廠訂單'), ['tw:6706'])
})
