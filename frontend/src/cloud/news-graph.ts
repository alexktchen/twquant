// 新聞關係圖（SPEC-017 §7、D-200）的資料形狀與版面純函式（不碰 DOM，供 node 單元測試）。
// 資料＝新聞服務 /v1/items/:id/graph 的回應：一棵樹（每個實體只留最好的那條路徑，見 news-service/src/graph.ts）。
// 版面＝地鐵路線圖（使用者 2026-10-19 選的：「像地鐵告示一樣」）：新聞是起點站，主幹往下接一度的站；
// 每個一度底下往右岔出它的二度，二度點開再往右岔出三度。站＝實心白圓＋類別色邊，線＝類別色、越粗越相關、草擬畫虛線。

export type GType = 'tw' | 'us' | 'company' | 'group' | 'theme'
export type GVia = 'mention' | 'supplier' | 'belongs' | 'member' | 'peer' | 'theme'
export type GNode = {
  id: string
  type: GType
  label: string
  /** 代號、ticker、國家或「族群」 */
  sub: string
  /** 美股前一天收盤漲跌（%） */
  chg?: number
  degree: number
  score: number
  /** 上一層；一度的上一層是新聞本身 → null */
  parent: string | null
  via: GVia
  via_label?: string
  /** 路徑上有 AI 草擬、還沒核可的線 */
  draft: boolean
}
export type NewsGraph = {
  item: { id: string; source_name: string; url: string; first_seen_at: string; title: string; title_zh: string | null }
  rule_version: string
  nodes: GNode[]
}

/** 三類顏色（dataviz 驗證：深色底全兩兩通過、色盲 ΔE ≥ 9.4）：國外＝藍、台股＝橘、族群／主題＝青。顏色只表示種類，另配文字。 */
export type GClass = 'abroad' | 'tw' | 'cluster'
export const classOf = (t: GType): GClass => (t === 'tw' ? 'tw' : t === 'us' || t === 'company' ? 'abroad' : 'cluster')
export const CLASS_LABEL: Record<GClass, string> = { abroad: '國外', tw: '台股', cluster: '族群／主題' }

export const VIA_LABEL: Record<GVia, string> = {
  mention: '新聞提到',
  supplier: '供應商',
  belongs: '屬於族群',
  member: '族群成員',
  peer: '同族群',
  theme: '相關主題',
}
/** 線的說明：同業線寫族群名（「同族群・伺服器」）；其他用通稱。 */
export const viaText = (n: GNode): string => (n.via === 'peer' && n.via_label ? (n.via_label === '同業' || n.via_label === '結盟' ? n.via_label : `同族群・${n.via_label}`) : VIA_LABEL[n.via])

/** 一棵樹的子點表（依分數高到低、再依名稱，結果穩定）。 */
export function childrenOf(nodes: GNode[]): Map<string | null, GNode[]> {
  const m = new Map<string | null, GNode[]>()
  for (const n of nodes) m.set(n.parent, [...(m.get(n.parent) ?? []), n])
  for (const list of m.values()) list.sort((a, b) => b.score - a.score || a.label.localeCompare(b.label, 'zh-Hant'))
  return m
}

/** 從新聞到這個點的路徑（一度在前）；不在圖上 → []。 */
export function pathTo(nodes: GNode[], id: string): GNode[] {
  const by = new Map(nodes.map((n) => [n.id, n]))
  const out: GNode[] = []
  let cur = by.get(id)
  while (cur) {
    out.unshift(cur)
    cur = cur.parent ? by.get(cur.parent) : undefined
  }
  return out
}

/** 線粗：分數 0～1 → 3～7px（地鐵線要粗才看得出粗細差；越粗越相關）。 */
export const lineWidth = (score: number): number => Number((3 + 4 * Math.max(0, Math.min(1, score))).toFixed(2))

/** 給「清單」看（不看圖也讀得到）：依度數、再依分數。 */
export const tableRows = (nodes: GNode[]): GNode[] => [...nodes].sort((a, b) => a.degree - b.degree || b.score - a.score || a.label.localeCompare(b.label, 'zh-Hant'))

/** 最相關的台股（設計評審 2026-10-19：圖要直接回答「這則新聞最牽動哪幾檔台股」）：分數高到低、同分看度數。 */
export const topTw = (nodes: GNode[], k = 3): GNode[] =>
  nodes
    .filter((n) => n.type === 'tw')
    .sort((a, b) => b.score - a.score || a.degree - b.degree || a.label.localeCompare(b.label, 'zh-Hant'))
    .slice(0, k)

/** 二度的點底下還收著幾個三度（寫「＋N 站」，告訴人點了會展開）；已展開或沒有子點 → 0。 */
export function hiddenKids(n: GNode, nodes: GNode[], opened: Set<string>): number {
  if (n.degree !== 2 || opened.has(n.id)) return 0
  return nodes.filter((x) => x.parent === n.id).length
}

// ── 地鐵路線圖的版面 ─────────────────────────────────────────────
export type Depth = 1 | 2 | 3
export type RowH = Record<Depth, number>
export type Row = { n: GNode; depth: Depth; cls: GClass; /** 列的上緣 */ y: number; h: number; /** 站的中心 */ cy: number; /** 還收著幾個三度 */ more: number }

/** 路線圖的列（由上到下）：一度依分數排，每個一度底下接它的二度；二度點開才列出它的三度。top＝第一列的上緣（起點站的高度）。 */
export function routeRows(nodes: GNode[], opened: Set<string>, rowH: RowH, top: number): Row[] {
  const kids = childrenOf(nodes)
  const out: Row[] = []
  let y = top
  const push = (n: GNode, depth: Depth) => {
    const h = rowH[depth]
    out.push({ n, depth, cls: classOf(n.type), y, h, cy: y + h / 2, more: hiddenKids(n, nodes, opened) })
    y += h
  }
  for (const a of kids.get(null) ?? []) {
    push(a, 1)
    for (const b of kids.get(a.id) ?? []) {
      push(b, 2)
      if (opened.has(b.id)) for (const c of kids.get(b.id) ?? []) push(c, 3)
    }
  }
  return out
}

export type Seg = { /** 這段線接到哪一站 */ to: string; cls: GClass | 'trunk'; draft: boolean; w: number; d: string }

/** 上一站到這一站的 S 形接線（往下再往右）。 */
const bend = (x0: number, y0: number, x1: number, y1: number): string => {
  const k = Math.min(26, Math.max(10, (y1 - y0) / 2))
  return `M${x0},${y0} C${x0},${y0 + k} ${x1},${y1 - k} ${x1},${y1}`
}

/**
 * 線段，每一列一段：一度在主幹（起點站或上一個一度直直往下）；二度、三度的第一站從上一層的站 S 形接到自己的欄，
 * 之後同一層的站直直往下。線的顏色＝到站那一站的類別、粗細＝到站的分數、草擬＝到站的 draft。cols＝三欄的 x；originY＝起點站中心。
 */
export function routeLines(rows: Row[], cols: [number, number, number], originY: number): Seg[] {
  const out: Seg[] = []
  const cyOf = new Map(rows.map((r) => [r.n.id, r.cy]))
  const lastSib = new Map<string | null, number>() // 同一個上一層底下、上一站的中心
  let lastTrunk = originY
  for (const r of rows) {
    const x = cols[r.depth - 1]
    let d: string
    if (r.depth === 1) {
      d = `M${x},${lastTrunk} V${r.cy}`
      lastTrunk = r.cy
    } else {
      const prev = lastSib.get(r.n.parent)
      if (prev !== undefined) d = `M${x},${prev} V${r.cy}`
      else d = bend(cols[r.depth - 2], cyOf.get(r.n.parent ?? '') ?? originY, x, r.cy)
    }
    lastSib.set(r.n.parent, r.cy)
    out.push({ to: r.n.id, cls: r.depth === 1 ? 'trunk' : r.cls, draft: r.n.draft && r.n.via !== 'mention', w: lineWidth(r.n.score), d })
  }
  return out
}
