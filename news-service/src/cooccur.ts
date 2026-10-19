// 從新聞學關係（使用者 2026-10-20：「新聞本身就會提到股票了，那些股票也要漸漸的建入關聯」）。
// 兩個實體在近 window_days 天裡同一則新聞一起被提到達 min_items 則 → 長出一條 cooccur 線（說明「新聞常一起提到・N 則」）；
// 次數隨新聞增加；掉到門檻以下（舊新聞滑出窗口）→ 線關掉（valid_to）。這是統計出來的事實，不是 AI 推測，所以 status＝approved。
// 排除：到處都提的實體（像「AI」）、兩邊都不是公司（台股、美股、國外公司）的配對、已經有族群／供應商／主題線的兩方。純函式。
import type { EntityType } from './graph.ts'

export type CooccurConfig = {
  /** 看近幾天的新聞 */
  window_days: number
  /** 一起被提到至少幾則才算 */
  min_items: number
  /** 被超過這個比例的新聞提到的實體不配對（到處都提，沒有資訊量） */
  hub_share: number
}
export type PairCount = { a: string; b: string; n: number }
/** 一條現役的 cooccur 線（src < dst，只存一個方向；src→dst 與 dst→src 都會走，見 graph.ts expandEdges） */
export type CooccurEdge = { src: string; dst: string; label: string }

/** 「新聞常一起提到」那條線上的說明。 */
export const cooccurLabel = (n: number): string => `新聞常一起提到・${n} 則`

const COMPANY: ReadonlySet<EntityType> = new Set(['tw', 'us', 'company'])

/**
 * 要有哪些 cooccur 線。pairs＝每一對（a < b）一起出現的則數；mentionsOf＝每個實體被幾則提到；total＝窗口裡有提到實體的新聞則數；
 * linked＝已經有其他種線的兩方（"a|b"，a < b）；typeOf＝實體類別（不在實體表的略過）。
 */
export function desiredCooccur(
  pairs: PairCount[],
  mentionsOf: Map<string, number>,
  total: number,
  linked: Set<string>,
  typeOf: Map<string, EntityType>,
  cfg: CooccurConfig,
): CooccurEdge[] {
  const hub = (e: string) => (mentionsOf.get(e) ?? 0) > cfg.hub_share * total
  const out: CooccurEdge[] = []
  for (const p of pairs) {
    const [a, b] = p.a < p.b ? [p.a, p.b] : [p.b, p.a]
    if (a === b || p.n < cfg.min_items) continue
    const ta = typeOf.get(a)
    const tb = typeOf.get(b)
    if (!ta || !tb) continue
    if (!COMPANY.has(ta) && !COMPANY.has(tb)) continue
    if (hub(a) || hub(b) || linked.has(`${a}|${b}`)) continue
    out.push({ src: a, dst: b, label: cooccurLabel(p.n) })
  }
  return out.sort((x, y) => x.src.localeCompare(y.src) || x.dst.localeCompare(y.dst))
}

/** 現役 vs 想要：要關的、要新增的、只改了次數（原地改說明）的。 */
export function diffCooccur(existing: CooccurEdge[], desired: CooccurEdge[]): { close: CooccurEdge[]; insert: CooccurEdge[]; relabel: CooccurEdge[] } {
  const key = (e: CooccurEdge) => `${e.src}|${e.dst}`
  const old = new Map(existing.map((e) => [key(e), e]))
  const want = new Map(desired.map((e) => [key(e), e]))
  return {
    close: existing.filter((e) => !want.has(key(e))),
    insert: desired.filter((e) => !old.has(key(e))),
    relabel: desired.filter((e) => old.has(key(e)) && old.get(key(e))!.label !== e.label),
  }
}

export function validateCooccurConfig(x: unknown): CooccurConfig {
  const c = x as Partial<CooccurConfig> | null
  const fail = (why: string): never => {
    throw new Error(`news 設定錯誤：cooccur.${why}`)
  }
  if (!c || typeof c !== 'object') fail('（整段缺少）')
  for (const k of ['window_days', 'min_items'] as const) if (!Number.isInteger(c![k]) || (c![k] as number) < 1) fail(k)
  if (c!.min_items! < 2) fail('min_items（至少 2，一則新聞不算關係）')
  if (typeof c!.hub_share !== 'number' || c!.hub_share <= 0 || c!.hub_share > 1) fail('hub_share')
  return c as CooccurConfig
}

// ── ingest 第四步（注入 IO，Edge Function 的 _shared/io.ts 接 SQL）──
export type CooccurDeps = {
  /** 窗口裡每一對一起出現的則數、每個實體被幾則提到、有提到實體的新聞則數 */
  counts: (windowDays: number) => Promise<{ pairs: PairCount[]; mentionsOf: Map<string, number>; total: number }>
  /** 現役實體的類別 */
  types: () => Promise<Map<string, EntityType>>
  /** 已經有其他種現役線的兩方（"a|b"，a < b） */
  linked: () => Promise<Set<string>>
  existing: () => Promise<CooccurEdge[]>
  apply: (d: { close: CooccurEdge[]; insert: CooccurEdge[]; relabel: CooccurEdge[] }) => Promise<void>
}

export async function learnCooccur(cfg: CooccurConfig, deps: CooccurDeps): Promise<{ edges: number; added: number; closed: number; relabeled: number }> {
  const { pairs, mentionsOf, total } = await deps.counts(cfg.window_days)
  const desired = desiredCooccur(pairs, mentionsOf, total, await deps.linked(), await deps.types(), cfg)
  const d = diffCooccur(await deps.existing(), desired)
  if (d.close.length || d.insert.length || d.relabel.length) await deps.apply(d)
  return { edges: desired.length, added: d.insert.length, closed: d.close.length, relabeled: d.relabel.length }
}
