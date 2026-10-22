// 證據句（T-437；使用者 2026-10-23：「如果我想要用在工作上我要怎麼定位那段字？」）：
// 比對名字時存下的「命中字前後各 20 字」（news.mentions.evidence）拿出來給網站。/v1/evidence?ids=<新聞 id,…>&entities=<實體 id,…>。
// 純函式；查資料庫由 api/index.ts 做。只有標題與摘要有比對過，證據不會是全文中段的句子。

export type EvidenceConfig = {
  /** 一次最多問幾則 */
  max_ids: number
  /** 一次最多問幾個實體 */
  max_entities: number
}

export function validateEvidenceConfig(x: unknown): EvidenceConfig {
  const c = x as Partial<EvidenceConfig> | null
  const fail = (why: string): never => {
    throw new Error(`news 設定錯誤：evidence.${why}`)
  }
  if (!c || typeof c !== 'object') fail('缺')
  for (const k of ['max_ids', 'max_entities'] as const) if (!Number.isInteger(c![k]) || (c![k] as number) < 1) fail(k)
  return c as EvidenceConfig
}

const ENTITY_RE = /^[a-z]+:[^\s,]{1,60}$/
const ITEM_RE = /^[^\s,]{1,200}$/

function splitList(raw: string | null, what: string, max: number, re: RegExp, like: string): string[] {
  if (!raw) throw new Error(`缺 ${what}`)
  const out = [...new Set(raw.split(',').map((s) => s.trim()).filter(Boolean))]
  if (out.length === 0) throw new Error(`缺 ${what}`)
  if (out.length > max) throw new Error(`${what} 最多 ${max} 個：${out.length}`)
  const bad = out.filter((s) => !re.test(s))
  if (bad.length) throw new Error(`${what} 要像 ${like}：${bad.slice(0, 3).join(',')}`)
  return out
}

/** 查詢參數：ids＝新聞 id（逗號分開）、entities＝實體 id（tw:2330、us:NVDA、th:ai…）。不合就丟錯（呼叫端回 400）。 */
export function parseEvidenceQuery(ids: string | null, entities: string | null, cfg: EvidenceConfig): { ids: string[]; entities: string[] } {
  return {
    ids: splitList(ids, 'ids', cfg.max_ids, ITEM_RE, 'cnbc-top-0'),
    entities: splitList(entities, 'entities', cfg.max_entities, ENTITY_RE, 'tw:2330'),
  }
}

export type EvidenceRow = { item_id: string; entity_id: string; evidence: string; created_at: Date | string }

/** 同一則同一實體可能比對過好幾版內容（標題改過）：留最新那版。回 {新聞 id: {實體 id: 證據句}}。 */
export function pickEvidence(rows: EvidenceRow[]): Record<string, Record<string, string>> {
  const at = (r: EvidenceRow) => (r.created_at instanceof Date ? r.created_at.getTime() : Date.parse(r.created_at))
  const best = new Map<string, EvidenceRow>()
  for (const r of rows) {
    const k = `${r.item_id}\u0000${r.entity_id}`
    const cur = best.get(k)
    if (!cur || at(r) > at(cur)) best.set(k, r)
  }
  const out: Record<string, Record<string, string>> = {}
  for (const r of [...best.values()].sort((a, b) => a.item_id.localeCompare(b.item_id) || a.entity_id.localeCompare(b.entity_id))) {
    ;(out[r.item_id] ??= {})[r.entity_id] = r.evidence
  }
  return out
}
