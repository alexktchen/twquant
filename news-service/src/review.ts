// 核可頁（T-403／SPEC-017 §6、D-200 §三）：AI 草擬的線（國外公司→台灣供應商、主題→族群／台股、同業）要擁有者核可才算數。
// 核可 → 草稿那條關掉（valid_to）、插一條 approved，valid_from＝核可時間（AC3）；退回 → 草稿關掉，圖上不再出現。
// 每個決定另記一筆 news.reviews（只增），同步腳本（scripts/news_sync_entities.py）照最新一筆決定，不會把核可過的又改回草稿。
// 只有擁有者能核可（AC2）：擁有者名單是 Edge Function 祕密 NEWS_OWNER_EMAILS，登入身分由網站驗過 Cloudflare Access 權杖後轉送。純函式。
import type { EntityType } from './graph.ts'

/** 核可頁處理的線種（族群線 belongs、新聞學到的 cooccur 不是草擬的，不在這裡） */
export const REVIEW_TYPES = ['supplier', 'theme', 'peer'] as const
export type ReviewType = (typeof REVIEW_TYPES)[number]
export type Decision = 'approved' | 'rejected'
/** 一次最多送幾個決定（一組最多十幾條；防呆） */
export const DECISIONS_MAX = 100

export type DraftEdge = { src: string; dst: string; type: ReviewType; label: string | null; rank: number | null; source: string }
export type EntityInfo = { id: string; type: EntityType; label: string; sub: string | null }

/** 線的身分：src|dst|type（同一對、同一種線現役只有一條）。 */
export const edgeKey = (e: { src: string; dst: string; type: string }): string => `${e.src}|${e.dst}|${e.type}`

/** 登入者是不是擁有者。owners＝逗號分隔的 email（祕密）；沒設定 → 沒有人能核可。 */
export function isOwner(email: string | null, owners: string | undefined): boolean {
  if (!email || !owners) return false
  const want = email.trim().toLowerCase()
  return owners
    .split(',')
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean)
    .includes(want)
}

export type ReviewItem = { dst: string; dst_label: string; dst_type: EntityType | null; dst_sub: string | null; label: string | null }
export type ReviewGroup = { src: string; src_label: string; src_type: EntityType | null; type: ReviewType; items: ReviewItem[] }

const TYPE_ORDER: Record<ReviewType, number> = { supplier: 0, theme: 1, peer: 2 }

/** 草稿依「起點＋線種」分組（核可頁一組一張卡：例「輝達 → 台灣供應商」8 條）；實體表沒有的起點／終點用 id 頂著。 */
export function groupDrafts(drafts: DraftEdge[], entities: Map<string, EntityInfo>): ReviewGroup[] {
  const by = new Map<string, ReviewGroup>()
  for (const d of drafts) {
    const k = `${d.type}|${d.src}`
    let g = by.get(k)
    if (!g) {
      const s = entities.get(d.src)
      g = { src: d.src, src_label: s?.label ?? d.src, src_type: s?.type ?? null, type: d.type, items: [] }
      by.set(k, g)
    }
    const t = entities.get(d.dst)
    g.items.push({ dst: d.dst, dst_label: t?.label ?? d.dst, dst_type: t?.type ?? null, dst_sub: t?.sub ?? null, label: d.label })
  }
  const groups = [...by.values()]
  for (const g of groups) g.items.sort((a, b) => (a.dst < b.dst ? -1 : a.dst > b.dst ? 1 : 0))
  // 依代號排（不用中文排序：Deno 與 node 的語系排序不保證一樣）
  return groups.sort((a, b) => TYPE_ORDER[a.type] - TYPE_ORDER[b.type] || (a.src < b.src ? -1 : a.src > b.src ? 1 : 0))
}

export type DecisionIn = { src: string; dst: string; type: ReviewType; decision: Decision }

/** POST /v1/review 的內容：{ decisions: [{ src, dst, type, decision }] }。不合法 → 丟錯（端點回 400）。 */
export function parseReviewBody(body: unknown): DecisionIn[] {
  const b = body as { decisions?: unknown } | null
  if (!b || !Array.isArray(b.decisions) || b.decisions.length === 0) throw new Error('缺 decisions（至少一個決定）')
  if (b.decisions.length > DECISIONS_MAX) throw new Error(`一次最多 ${DECISIONS_MAX} 個決定`)
  const seen = new Set<string>()
  return b.decisions.map((x, i) => {
    const d = x as Partial<DecisionIn> | null
    const bad = (why: string): never => {
      throw new Error(`第 ${i + 1} 個決定：${why}`)
    }
    if (!d || typeof d.src !== 'string' || !d.src || typeof d.dst !== 'string' || !d.dst) bad('缺 src 或 dst')
    if (!REVIEW_TYPES.includes(d!.type as ReviewType)) bad(`線種只能是 ${REVIEW_TYPES.join('／')}`)
    if (d!.decision !== 'approved' && d!.decision !== 'rejected') bad('decision 只能是 approved 或 rejected')
    const k = edgeKey(d as DecisionIn)
    if (seen.has(k)) bad('同一條線重複')
    seen.add(k)
    return { src: d!.src!, dst: d!.dst!, type: d!.type as ReviewType, decision: d!.decision as Decision }
  })
}

export type ReviewRow = { target: 'edge'; key: string; decision: Decision; reviewer: string; reviewed_at: string }
export type ApprovedEdgeRow = DraftEdge & { status: 'approved'; valid_from: string }
export type ReviewPlan = {
  /** 要關掉的草稿線（valid_to＝核可時間） */
  close: { src: string; dst: string; type: ReviewType }[]
  /** 核可的線：同樣的說明、名次、來源，status approved，valid_from＝核可時間 */
  insert: ApprovedEdgeRow[]
  reviews: ReviewRow[]
  /** 已經不是草稿（別人先處理了、或同步關掉了）→ 略過，回給網站 */
  skipped: string[]
}

/** 決定 → 要寫什麼。drafts＝目前現役的草稿線（以 edgeKey 對）；now＝核可時間（同一批同一個時間）。 */
export function planReview(decisions: DecisionIn[], drafts: Map<string, DraftEdge>, reviewer: string, now: Date): ReviewPlan {
  const at = now.toISOString()
  const plan: ReviewPlan = { close: [], insert: [], reviews: [], skipped: [] }
  for (const d of decisions) {
    const k = edgeKey(d)
    const draft = drafts.get(k)
    if (!draft) {
      plan.skipped.push(k)
      continue
    }
    plan.close.push({ src: draft.src, dst: draft.dst, type: draft.type })
    if (d.decision === 'approved') plan.insert.push({ ...draft, status: 'approved', valid_from: at })
    plan.reviews.push({ target: 'edge', key: k, decision: d.decision, reviewer, reviewed_at: at })
  }
  return plan
}
