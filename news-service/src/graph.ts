// 資訊關係圖的「幾度關聯」（SPEC-017 §6，T-404 的核心；D-199 後使用者要「心智爆炸圖」先拿來做示範）。
// 從新聞直接提到的實體（一度）出發，沿著線往外走，最多 max_degree 度；每走一條線乘上那種線的係數、
// 再除以出發點出度的 fanout_power 次方（大族群、輝達這種樞紐不會把分數灑到幾百檔）。
// 每個實體只留分數最高的那條路徑 → 結果是一棵樹（畫成心智圖）。同分依度數、再依代號排，同輸入同輸出。純函式。

export type EntityType = 'tw' | 'us' | 'company' | 'group' | 'theme'
/** id 帶前綴：tw:2330、us:NVDA、co:samsung、grp:晶圓製造、th:AI晶片 */
export type Entity = { id: string; type: EntityType; label: string }
/** supplier＝國外公司→台灣供應商；belongs＝台股→族群；member＝族群→成員（由 belongs 反推）；peer＝同業（雙向）；theme＝主題→族群 */
export type EdgeType = 'supplier' | 'belongs' | 'member' | 'peer' | 'theme'
export type Edge = {
  src: string
  dst: string
  type: EdgeType
  /** AI 草擬、還沒核可（網站畫虛線） */
  draft?: boolean
  /** 線上的說明（例：同業線寫是哪個族群「伺服器」） */
  label?: string
}

export type GraphConfig = {
  weights: Record<EdgeType, number>
  /** 出度打折：分數 ÷ 出度^fanout_power */
  fanout_power: number
  max_degree: number
  /** 每則最多留幾個實體（含一度；被留下的點的祖先一定留） */
  max_links: number
}

export type Step = { entity: string; via: EdgeType | 'mention'; draft: boolean; label?: string }
export type Link = {
  entity: string
  /** 1＝新聞直接提到 */
  degree: number
  score: number
  /** 從新聞到這個實體的路徑（第一步 via=mention） */
  path: Step[]
  /** 路徑上有任何一條 AI 草擬、還沒核可的線 */
  draft: boolean
}

/** 線的方向：belongs 自動補一條反向的 member；peer 雙向。 */
export function expandEdges(edges: Edge[]): Edge[] {
  const out: Edge[] = []
  const seen = new Set<string>()
  const add = (e: Edge) => {
    const k = `${e.src}>${e.dst}>${e.type}`
    if (!seen.has(k)) {
      seen.add(k)
      out.push(e)
    }
  }
  for (const e of edges) {
    add(e)
    if (e.type === 'belongs') add({ src: e.dst, dst: e.src, type: 'member', draft: e.draft, label: e.label })
    if (e.type === 'peer') add({ src: e.dst, dst: e.src, type: 'peer', draft: e.draft, label: e.label })
  }
  return out
}

const better = (a: Link, b: Link | undefined): boolean =>
  !b || a.score > b.score + 1e-12 || (Math.abs(a.score - b.score) <= 1e-12 && (a.degree < b.degree || (a.degree === b.degree && pathKey(a) < pathKey(b))))
const pathKey = (l: Link) => l.path.map((s) => s.entity).join('>')

/** 幾度關聯：mentions＝新聞直接提到的實體 id（一度）。回傳依分數高到低排好、截到 max_links 的關聯（樹）。 */
export function propagate(mentions: string[], edges: Edge[], cfg: GraphConfig): Link[] {
  const all = expandEdges(edges)
  const out = new Map<string, Edge[]>()
  for (const e of all) out.set(e.src, [...(out.get(e.src) ?? []), e])
  const best = new Map<string, Link>()
  for (const m of [...new Set(mentions)].sort()) best.set(m, { entity: m, degree: 1, score: 1, path: [{ entity: m, via: 'mention', draft: false }], draft: false })

  // 一層一層往外（最多 max_degree－1 步）：每層只從上一層剛更新的點出發
  let frontier = [...best.values()]
  for (let d = 2; d <= cfg.max_degree; d++) {
    const next = new Map<string, Link>()
    for (const from of frontier) {
      const es = out.get(from.entity) ?? []
      const share = Math.pow(Math.max(es.length, 1), cfg.fanout_power)
      for (const e of es) {
        if (from.path.some((s) => s.entity === e.dst)) continue // 不繞回路徑上的點
        const cand: Link = {
          entity: e.dst,
          degree: d,
          score: (from.score * cfg.weights[e.type]) / share,
          path: [...from.path, { entity: e.dst, via: e.type, draft: Boolean(e.draft), ...(e.label ? { label: e.label } : {}) }],
          draft: from.draft || Boolean(e.draft),
        }
        if (better(cand, best.get(e.dst)) && better(cand, next.get(e.dst))) next.set(e.dst, cand)
      }
    }
    for (const [k, v] of next) best.set(k, v)
    frontier = [...next.values()].filter((l) => best.get(l.entity) === l)
  }

  // 截到 max_links：照分數挑，挑到的點的祖先一併留（樹不斷）
  const ranked = [...best.values()].sort((a, b) => b.score - a.score || a.degree - b.degree || a.entity.localeCompare(b.entity))
  const keep = new Set<string>()
  for (const l of ranked) {
    if (keep.size >= cfg.max_links) break
    for (const s of l.path) keep.add(s.entity)
  }
  return ranked.filter((l) => keep.has(l.entity))
}

/** 上一層是誰（畫樹用）：一度的上一層是新聞本身（null）。 */
export const parentOf = (l: Link): string | null => (l.path.length >= 2 ? l.path[l.path.length - 2].entity : null)
