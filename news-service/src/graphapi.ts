// 關係圖 API 的純函式（T-404，SPEC-017 §6）：資料庫裡的實體與線 → src/graph.ts 的 propagate → 心智圖的點（GNode）。
// SQL 在 supabase/functions/api；這裡只做轉換，同輸入同輸出。
import { parentOf, propagate, type Edge, type EdgeType, type EntityType, type GraphConfig, type Step } from './graph.ts'

/** news.entities 的一列（valid_to is null） */
export type EntityRow = { id: string; type: EntityType; label: string; sub: string | null }
/** news.graph_edges 的一列（valid_to is null） */
export type EdgeRow = { src: string; dst: string; type: EdgeType; status: 'approved' | 'draft'; label: string | null; rank: number | null }
/** configs/news.yaml 的 graph 段 */
export type GraphApiConfig = GraphConfig & {
  /** 同族群線：每檔連到族群裡成交金額前幾名 */
  peers_per_group: number
}

/** 心智圖的一個點；同 frontend/src/cloud/news-graph.ts 的 GNode（chg 這一版不填）。 */
export type GNode = {
  id: string
  type: EntityType
  label: string
  sub: string
  chg?: number
  degree: number
  score: number
  parent: string | null
  via: Step['via']
  via_label?: string
  draft: boolean
}

/** /v1/items 每則的一度實體 */
export type ItemMention = { id: string; type: EntityType; label: string }

/** 分數留到小數第幾位（同示範資料） */
const SCORE_DIGITS = 4

const afterColon = (id: string): string => id.slice(id.indexOf(':') + 1)

const byRank = (a: EdgeRow, b: EdgeRow): number =>
  (a.rank ?? Number.POSITIVE_INFINITY) - (b.rank ?? Number.POSITIVE_INFINITY) || a.src.localeCompare(b.src)

/**
 * 表裡的線 → 給 propagate 的線：表裡的線照放（draft 狀態 → draft:true）；另外每個族群依 rank 取前 peers_per_group 檔，
 * 族群裡每一檔都連到這幾檔（peer，label＝族群名）。兩端有一端不在實體表的線略過。
 */
export function edgesFor(rows: EdgeRow[], entities: Map<string, EntityRow>, cfg: GraphApiConfig): Edge[] {
  const known = rows
    .filter((r) => entities.has(r.src) && entities.has(r.dst))
    .sort((a, b) => a.src.localeCompare(b.src) || a.dst.localeCompare(b.dst) || a.type.localeCompare(b.type))
  const out: Edge[] = known.map((r) => ({
    src: r.src,
    dst: r.dst,
    type: r.type,
    draft: r.status === 'draft',
    ...(r.label ? { label: r.label } : {}),
  }))

  // 同族群線：族群 → 成員（belongs 的 dst 是族群）
  const groups = new Map<string, EdgeRow[]>()
  for (const r of known) if (r.type === 'belongs') groups.set(r.dst, [...(groups.get(r.dst) ?? []), r])
  for (const grp of [...groups.keys()].sort()) {
    const members = groups.get(grp)!.sort(byRank)
    const top = members.slice(0, cfg.peers_per_group)
    const label = entities.get(grp)!.label
    for (const m of members) {
      for (const h of top) {
        if (h.src === m.src) continue
        out.push({ src: m.src, dst: h.src, type: 'peer', draft: m.status === 'draft' || h.status === 'draft', label })
      }
    }
  }
  return out
}

/** 新聞直接提到的實體（一度）→ 心智圖的點（依分數高到低）。實體表沒有的 id 直接略過。 */
export function buildNodes(mentions: string[], rows: EdgeRow[], entities: Map<string, EntityRow>, cfg: GraphApiConfig): GNode[] {
  const links = propagate(
    mentions.filter((m) => entities.has(m)),
    edgesFor(rows, entities, cfg),
    cfg,
  )
  const nodes: GNode[] = []
  for (const l of links) {
    const e = entities.get(l.entity)
    if (!e) continue
    const last = l.path[l.path.length - 1]
    nodes.push({
      id: e.id,
      type: e.type,
      label: e.label,
      sub: e.sub ?? afterColon(e.id),
      degree: l.degree,
      score: Number(l.score.toFixed(SCORE_DIGITS)),
      parent: parentOf(l),
      via: last.via,
      ...(last.label ? { via_label: last.label } : {}),
      draft: l.draft,
    })
  }
  return nodes
}

/** 列表上的順序：台股、國外（美股、國外公司）、族群／主題 */
const CLASS_ORDER: Record<EntityType, number> = { tw: 0, us: 1, company: 1, group: 2, theme: 2 }

/** /v1/items 每則的 mentions：同一實體只一次，台股在前、再國外、再族群／主題（同類依 id），最多 limit 個。 */
export function itemMentions(rows: ItemMention[], limit: number): ItemMention[] {
  const seen = new Map<string, ItemMention>()
  for (const r of rows) if (!seen.has(r.id)) seen.set(r.id, { id: r.id, type: r.type, label: r.label })
  return [...seen.values()].sort((a, b) => CLASS_ORDER[a.type] - CLASS_ORDER[b.type] || a.id.localeCompare(b.id)).slice(0, limit)
}
