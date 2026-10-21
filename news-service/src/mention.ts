// 新聞提到哪些實體（一度）——別名比對（T-402／SPEC-017 §6）。純函式；資料庫的查詢由呼叫端注入（_shared/io.ts 的 mentionDeps）。
// 中文別名（含任何 CJK 字）：子字串比對。拉丁別名：大小寫要一樣、前後要字界（前後不是英數字）。
// 長的別名先比、命中的範圍不再給短的用：「長榮航空」吃掉後「長榮」不會再命中、「台塑化」先於「台塑」。
// 中英文別名一起排長短（「SK海力士」先於「SK」），所以英文的長名也會蓋掉被它包住的短名。

export type MentionConfig = {
  /** 比對規則版本：寫進 mentions／mention_runs，規則改了才知道哪些要重掃 */
  rule_version: string
  /** 每次 ingest 最多比對幾則 */
  per_run_limit: number
  /** 拉丁別名至少幾個字元；全大寫 2～5 字的 ticker 例外 */
  latin_min: number
  /** 中文別名至少幾個字 */
  cjk_min: number
  /** 這麼短（含）的中文別名要「站得住」：前後至少一邊不是中文字（2026-10-20 抽查：122 個兩字股名有 30 個被一般用詞咬到——
   *  「全新產品」「達新台幣」「紐約時報」「截至上午」「兆元晶片」；「鴻海、緯穎」「富喬（1815）」「焦點股》台虹：」照樣命中） */
  cjk_bound_len: number
  /** 整條別名等於這些字串就不建索引 */
  stopwords: string[]
  /** 擋字：照樣比對、先佔掉那段字，但不算提到任何實體（擋掉股名被長詞咬到，例：東南亞 → 南亞） */
  blockers?: string[]
}
export type EntityAliases = { id: string; label: string; aliases: string[] }
type AliasEntry = {
  alias: string
  /** 拉丁別名（要字界）；含 CJK 的是 false */
  latin: boolean
  /** 短的中文別名（cjk_bound_len 以內）：前後至少一邊不是中文字才算命中 */
  short: boolean
  /** 同一條別名可能屬於好幾個實體（依代號排） */
  entities: string[]
}
export type AliasIndex = {
  /** 別名的第一個 UTF-16 字元 → 以它開頭的別名（長的在前） */
  byFirst: Map<string, AliasEntry[]>
  /** 證據前後各取幾個字 */
  context: number
}
export type MentionHit = { entity: string; alias: string; evidence: string }

/** 證據字串：命中處前後各最多這麼多字（契約 §2 定的格式，不是可調參數） */
const EVIDENCE_CONTEXT = 20
const CJK_RE = /[⺀-⿟々㐀-䶿一-鿿豈-﫿\u{20000}-\u{3134f}]/u
const TICKER_RE = /^[A-Z]{2,5}$/
const WORD_RE = /[A-Za-z0-9]/

const cpLen = (s: string): number => [...s].length

/** configs/news.yaml 的 mention 段 → MentionConfig；缺了或型別不對 → 丟錯。 */
export function validateMentionConfig(x: unknown): MentionConfig {
  const c = x as Partial<MentionConfig> | null
  const fail = (why: string): never => {
    throw new Error(`news 設定錯誤：mention.${why}`)
  }
  if (!c || typeof c !== 'object') fail('（整段缺少）')
  if (typeof c!.rule_version !== 'string' || !c!.rule_version) fail('rule_version')
  for (const k of ['per_run_limit', 'latin_min', 'cjk_min'] as const) if (!Number.isInteger(c![k]) || (c![k] as number) < 1) fail(k)
  if (!Number.isInteger(c!.cjk_bound_len) || (c!.cjk_bound_len as number) < 0) fail('cjk_bound_len')
  if (!Array.isArray(c!.stopwords) || c!.stopwords.some((w) => typeof w !== 'string' || !w)) fail('stopwords')
  if (c!.blockers !== undefined && (!Array.isArray(c!.blockers) || c!.blockers.some((w) => typeof w !== 'string' || !w))) fail('blockers')
  return c as MentionConfig
}

/** 建索引：label 也算別名；去掉 stopwords、太短的；長的先比（同長依字串排，同輸入同輸出）。 */
export function buildIndex(entities: EntityAliases[], cfg: MentionConfig): AliasIndex {
  const stop = new Set(cfg.stopwords)
  const byAlias = new Map<string, Set<string>>()
  for (const e of entities) {
    for (const raw of [e.label, ...e.aliases]) {
      const a = (raw ?? '').trim()
      if (!a || stop.has(a)) continue
      if (CJK_RE.test(a)) {
        if (cpLen(a) < cfg.cjk_min) continue
      } else if (!TICKER_RE.test(a) && cpLen(a) < cfg.latin_min) continue
      let s = byAlias.get(a)
      if (!s) byAlias.set(a, (s = new Set()))
      s.add(e.id)
    }
  }
  // 擋字：沒有實體的別名——長的先佔位置，所以裡面的短股名不會再命中
  for (const b of cfg.blockers ?? []) if (!byAlias.has(b)) byAlias.set(b, new Set())
  const entries: AliasEntry[] = [...byAlias].map(([alias, ids]) => ({
    alias,
    latin: !CJK_RE.test(alias),
    short: CJK_RE.test(alias) && cpLen(alias) <= cfg.cjk_bound_len,
    entities: [...ids].sort(),
  }))
  entries.sort((a, b) => cpLen(b.alias) - cpLen(a.alias) || (a.alias < b.alias ? -1 : a.alias > b.alias ? 1 : 0))
  const byFirst = new Map<string, AliasEntry[]>()
  for (const en of entries) {
    const k = en.alias[0]
    let l = byFirst.get(k)
    if (!l) byFirst.set(k, (l = []))
    l.push(en)
  }
  return { byFirst, context: EVIDENCE_CONTEXT }
}

type Cand = { start: number; end: number; entry: AliasEntry }

const isWord = (ch: string | undefined): boolean => ch !== undefined && WORD_RE.test(ch)
const isCjk = (ch: string | undefined): boolean => ch !== undefined && CJK_RE.test(ch)

/** 一段文字裡所有可能的命中（還沒處理重疊）。 */
function candidates(text: string, index: AliasIndex): Cand[] {
  const out: Cand[] = []
  for (let i = 0; i < text.length; i++) {
    const list = index.byFirst.get(text[i])
    if (!list) continue
    for (const entry of list) {
      if (!text.startsWith(entry.alias, i)) continue
      const end = i + entry.alias.length
      if (entry.latin && (isWord(text[i - 1]) || isWord(text[end]))) continue
      // 短的中文股名夾在兩個中文字中間（「全新產品」「達新台幣」）多半是一般用詞，不算；旁邊有標點、數字、括號、空白或開頭結尾才算
      if (entry.short && isCjk(text[i - 1]) && isCjk(text[end])) continue
      out.push({ start: i, end, entry })
    }
  }
  return out
}

function evidenceOf(text: string, start: number, end: number, context: number): string {
  const before = [...text.slice(0, start)].slice(-context).join('')
  const after = [...text.slice(end)].slice(0, context).join('')
  return `${before}${text.slice(start, end)}${after}`.replace(/\s*[\r\n]+\s*/g, ' ').trim()
}

/** texts＝[title_zh, summary_zh, title, summary]（null 跳過）。同一實體只回一次（第一個命中）。evidence＝命中字串前後各最多 20 字。 */
export function detectMentions(texts: Array<string | null | undefined>, index: AliasIndex): MentionHit[] {
  const hits: Array<MentionHit & { t: number; pos: number }> = []
  texts.forEach((text, t) => {
    if (!text) return
    // 長的先占位置；同長的依出現位置
    const cands = candidates(text, index).sort((a, b) => b.end - b.start - (a.end - a.start) || a.start - b.start)
    const taken: Array<[number, number]> = []
    for (const c of cands) {
      if (taken.some(([s, e]) => c.start < e && s < c.end)) continue
      taken.push([c.start, c.end])
      const evidence = evidenceOf(text, c.start, c.end, index.context)
      for (const entity of c.entry.entities) hits.push({ entity, alias: c.entry.alias, evidence, t, pos: c.start })
    }
  })
  hits.sort((a, b) => a.t - b.t || a.pos - b.pos || (a.entity < b.entity ? -1 : a.entity > b.entity ? 1 : 0))
  const seen = new Set<string>()
  const out: MentionHit[] = []
  for (const h of hits) {
    if (seen.has(h.entity)) continue
    seen.add(h.entity)
    out.push({ entity: h.entity, alias: h.alias, evidence: h.evidence })
  }
  return out
}

// ── ingest 第三步：把還沒比對過的這一版內容比對完、寫進 mentions／mention_runs（契約 §4）──

export type PendingItem = { item_id: string; content_sha: string; title: string | null; summary: string | null; title_zh: string | null; summary_zh: string | null }
export type MentionSaveHit = { item_id: string; content_sha: string; entity: string; evidence: string }
export type MentionRun = { item_id: string; content_sha: string; n: number }
export type MentionDeps = {
  now: () => Date
  /** news.entities where valid_to is null */
  entities: () => Promise<EntityAliases[]>
  /** raw_items 目前這一版（同 /v1/items 的 lateral 取最新版）且 mention_runs 沒有 (item_id, content_sha) 的，新的先 */
  pending: (limit: number) => Promise<PendingItem[]>
  save: (hits: MentionSaveHit[], runs: MentionRun[], ruleVersion: string) => Promise<void>
}

/** 比對還沒比對過的新聞（最多 per_run_limit 則）；0 命中的也記一列 mention_runs，下次不再掃。 */
export async function detectPending(cfg: MentionConfig, deps: MentionDeps): Promise<{ scanned: number; hits: number }> {
  const items = await deps.pending(cfg.per_run_limit)
  if (items.length === 0) return { scanned: 0, hits: 0 }
  const index = buildIndex(await deps.entities(), cfg)
  const hits: MentionSaveHit[] = []
  const runs: MentionRun[] = []
  for (const it of items) {
    const found = detectMentions([it.title_zh, it.summary_zh, it.title, it.summary], index)
    for (const h of found) hits.push({ item_id: it.item_id, content_sha: it.content_sha, entity: h.entity, evidence: h.evidence })
    runs.push({ item_id: it.item_id, content_sha: it.content_sha, n: found.length })
  }
  await deps.save(hits, runs, cfg.rule_version)
  return { scanned: items.length, hits: hits.length }
}
