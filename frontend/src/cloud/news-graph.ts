// 新聞關係圖（SPEC-017 §7、D-200）的資料形狀與版面純函式（不碰 DOM，供 node 單元測試）。
// 資料＝新聞服務 /v1/items/:id/graph 的回應：一棵樹（每個實體只留最好的那條路徑，見 news-service/src/graph.ts）。
// 版面＝心智圖（使用者原話「心智爆炸圖」；2026-10-19 五個版本後「你幫我想一個」）：新聞在正中間，分支往外長，
// 一度、二度、三度一層一層往外；每一層的字直接寫在分支末端、底下一條類別色的底線，沒有點、沒有球。
// 分支＝由粗到細的彩帶（根部粗、末端細），顏色＝到達那一站的類別；越相關越粗；AI 草擬的畫虛線。

export type GType = 'tw' | 'us' | 'company' | 'group' | 'theme'
export type GVia = 'mention' | 'supplier' | 'belongs' | 'member' | 'peer' | 'theme' | 'cooccur'
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
  /** 從新聞學的關係（2026-10-20）：via_label 寫「新聞常一起提到・N 則」 */
  cooccur: '新聞常一起提到',
}
/** 線的說明：同業線寫族群名（「同族群・伺服器」）；其他用通稱。 */
export const viaText = (n: GNode): string => n.via === 'cooccur' && n.via_label ? n.via_label : (n.via === 'peer' && n.via_label ? (n.via_label === '同業' || n.via_label === '結盟' ? n.via_label : `同族群・${n.via_label}`) : VIA_LABEL[n.via])

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

/** 給「清單」看（不看圖也讀得到）：依度數、再依分數。 */
export const tableRows = (nodes: GNode[]): GNode[] => [...nodes].sort((a, b) => a.degree - b.degree || b.score - a.score || a.label.localeCompare(b.label, 'zh-Hant'))

/** 最相關的台股（設計評審 2026-10-19：圖要直接回答「這則新聞最牽動哪幾檔台股」）：分數高到低、同分看度數。 */
export const topTw = (nodes: GNode[], k = 3): GNode[] =>
  nodes
    .filter((n) => n.type === 'tw')
    .sort((a, b) => b.score - a.score || a.degree - b.degree || a.label.localeCompare(b.label, 'zh-Hant'))
    .slice(0, k)

/** 二度的點底下還收著幾個三度（寫「＋N」，告訴人點了會展開）；已展開或沒有子點 → 0。 */
export function hiddenKids(n: GNode, nodes: GNode[], opened: Set<string>): number {
  if (n.degree !== 2 || opened.has(n.id)) return 0
  return nodes.filter((x) => x.parent === n.id).length
}

// ── 心智圖的版面 ─────────────────────────────────────────────
export type Depth = 1 | 2 | 3
export type Side = 1 | -1
export type MindOpts = {
  /** 每一層一格字的高度（名字＋第二行） */
  rowH: Record<Depth, number>
  /** 每一層字格的寬（底線就是這麼長） */
  slotW: Record<Depth, number>
  /** 層與層之間的分支長度 */
  gap: number
  /** 三度縮排在二度底下（窄螢幕）的縮排量；null＝三度自己一欄 */
  indent3: number | null
  /** 左右都長（桌機）還是只往右長（手機） */
  twoSided: boolean
  centerW: number
  centerH: number
  pad: number
}
export type MItem = {
  n: GNode
  depth: Depth
  cls: GClass
  side: Side
  /** 字格的左緣、寬、上緣、高 */
  x: number
  w: number
  top: number
  h: number
  /** 底線的 y（分支接這裡） */
  y: number
  /** 還收著幾個三度 */
  more: number
}
export type MindLayout = { items: MItem[]; width: number; height: number; cx: number; cy: number }

/**
 * 心智圖座標。每個點在自己子樹的正中間（分支像扇子往外張開）；只有「三度縮排在二度底下」時二度在子樹最上面、三度列在它下面（像大綱）。
 * 二度的三度只在點開（opened）後才排進來。雙邊時一度輪流分到左右（高的那棵先、往比較矮的那邊放）。
 */
export function mindLayout(nodes: GNode[], opened: Set<string>, o: MindOpts, viewW: number): MindLayout {
  const kids = childrenOf(nodes)
  const depthOf = (n: GNode): Depth => Math.min(Math.max(n.degree, 1), 3) as Depth
  const visible = (n: GNode): GNode[] => (n.degree === 2 && !opened.has(n.id) ? [] : (kids.get(n.id) ?? []))
  const stacks = (n: GNode) => n.degree === 2 && o.indent3 !== null && visible(n).length > 0
  const span = (n: GNode): number => {
    const sum = visible(n).reduce((a, k) => a + span(k), 0)
    const h = o.rowH[depthOf(n)]
    return stacks(n) ? h + sum : Math.max(h, sum)
  }
  const roots = kids.get(null) ?? []
  const sideOf = new Map<string, Side>()
  if (o.twoSided) {
    let r = 0
    let l = 0
    for (const n of [...roots].sort((a, b) => span(b) - span(a) || b.score - a.score || a.label.localeCompare(b.label, 'zh-Hant'))) {
      if (r <= l) {
        sideOf.set(n.id, 1)
        r += span(n)
      } else {
        sideOf.set(n.id, -1)
        l += span(n)
      }
    }
  } else for (const n of roots) sideOf.set(n.id, 1)
  const sideH = (s: Side) => roots.filter((n) => sideOf.get(n.id) === s).reduce((a, n) => a + span(n), 0)
  const height = Math.max(sideH(1), sideH(-1), o.centerH) + 2 * o.pad
  const cy = height / 2
  const sideW = o.gap + o.slotW[1] + o.gap + o.slotW[2] + (o.indent3 !== null ? 0 : o.gap + o.slotW[3])
  const cx = o.twoSided ? Math.max(viewW / 2, o.pad + sideW + o.centerW / 2) : o.pad + o.centerW / 2
  const width = o.twoSided ? Math.max(viewW, 2 * (o.pad + sideW) + o.centerW) : cx + o.centerW / 2 + sideW + o.pad

  const xFor = (depth: Depth, side: Side): number => {
    const edge = cx + side * (o.centerW / 2)
    const x1 = side === 1 ? edge + o.gap : edge - o.gap - o.slotW[1]
    if (depth === 1) return x1
    const x2 = side === 1 ? x1 + o.slotW[1] + o.gap : x1 - o.gap - o.slotW[2]
    if (depth === 2) return x2
    if (o.indent3 !== null) return side === 1 ? x2 + o.indent3 : x2 + o.slotW[2] - o.indent3 - o.slotW[3]
    return side === 1 ? x2 + o.slotW[2] + o.gap : x2 - o.gap - o.slotW[3]
  }
  const items: MItem[] = []
  const place = (n: GNode, side: Side, top: number) => {
    const d = depthOf(n)
    const h = o.rowH[d]
    const sp = span(n)
    const st = stacks(n)
    const c = visible(n)
    const sum = c.reduce((a, k) => a + span(k), 0)
    const myTop = st ? top : top + (sp - h) / 2
    items.push({ n, depth: d, cls: classOf(n.type), side, x: xFor(d, side), w: o.slotW[d], top: myTop, h, y: myTop + h, more: hiddenKids(n, nodes, opened) })
    // 子樹比自己矮（例：只有一個子點）→ 子點也放在自己的正中間
    let cur = st ? top + h : top + (sp - sum) / 2
    for (const k of c) {
      place(k, side, cur)
      cur += span(k)
    }
  }
  for (const s of [1, -1] as Side[]) {
    let cur = cy - sideH(s) / 2
    for (const n of roots.filter((r) => sideOf.get(r.id) === s)) {
      place(n, s, cur)
      cur += span(n)
    }
  }
  return { items, width, height, cx, cy }
}

/** 分支根部與末端的粗細（px），一度最粗；再乘上相關度（0.6～1）。 */
export const TAPER: Record<Depth, [number, number]> = { 1: [8, 4], 2: [4.2, 2.2], 3: [2.4, 1.6] }
export const relevance = (score: number): number => 0.6 + 0.4 * Math.max(0, Math.min(1, score))

type Pt = { x: number; y: number }
/** 分支的三次曲線控制點：一般是水平出、水平進的 S 形；hook（縮排在上一層底下的三度）是先往下再轉進去的鉤形。 */
export function branchCurve(x0: number, y0: number, x1: number, y1: number, side: Side, hook = false): [Pt, Pt, Pt, Pt] {
  if (hook) return [{ x: x0, y: y0 }, { x: x0, y: y0 + (y1 - y0) * 0.55 }, { x: x0, y: y1 }, { x: x1, y: y1 }]
  const k = Math.max(16, Math.abs(x1 - x0) * 0.45)
  return [{ x: x0, y: y0 }, { x: x0 + side * k, y: y0 }, { x: x1 - side * k, y: y1 }, { x: x1, y: y1 }]
}
const cubic = (p: [Pt, Pt, Pt, Pt], t: number): Pt => {
  const u = 1 - t
  return {
    x: u * u * u * p[0].x + 3 * u * u * t * p[1].x + 3 * u * t * t * p[2].x + t * t * t * p[3].x,
    y: u * u * u * p[0].y + 3 * u * u * t * p[1].y + 3 * u * t * t * p[2].y + t * t * t * p[3].y,
  }
}
const f1 = (v: number) => v.toFixed(1)

/** 一般的曲線（草擬的虛線用）：SVG path d。 */
export const curvePath = (p: [Pt, Pt, Pt, Pt]): string => `M${f1(p[0].x)},${f1(p[0].y)} C${f1(p[1].x)},${f1(p[1].y)} ${f1(p[2].x)},${f1(p[2].y)} ${f1(p[3].x)},${f1(p[3].y)}`

/** 由粗到細的彩帶：沿曲線取樣、兩側各推半個寬度，圍成封閉的 path（填色，不描邊）。 */
export function taperPath(p: [Pt, Pt, Pt, Pt], w0: number, w1: number, steps = 14): string {
  const left: Pt[] = []
  const right: Pt[] = []
  let nx = 0
  let ny = -1
  for (let i = 0; i <= steps; i++) {
    const t = i / steps
    const a = cubic(p, Math.max(0, t - 0.01))
    const b = cubic(p, Math.min(1, t + 0.01))
    const dx = b.x - a.x
    const dy = b.y - a.y
    const len = Math.hypot(dx, dy)
    if (len > 1e-6) {
      nx = -dy / len
      ny = dx / len
    }
    const c = cubic(p, t)
    const hw = (w0 + (w1 - w0) * t) / 2
    left.push({ x: c.x + nx * hw, y: c.y + ny * hw })
    right.push({ x: c.x - nx * hw, y: c.y - ny * hw })
  }
  const pts = [...left, ...right.reverse()]
  return `M${pts.map((q) => `${f1(q.x)},${f1(q.y)}`).join('L')}Z`
}

export type Branch = { to: string; cls: GClass; draft: boolean; /** 虛線的粗細 */ w: number; d: string }

/** 每個點一條分支：從上一層（新聞＝中心方塊的邊、其他＝上一層字格底線的末端；縮排的從上一層底線的頭）接到自己字格底線的頭。 */
export function mindBranches(lay: MindLayout, o: MindOpts): Branch[] {
  const by = new Map(lay.items.map((i) => [i.n.id, i]))
  return lay.items.map((it) => {
    const p = it.n.parent ? by.get(it.n.parent) : undefined
    let x0: number
    let y0: number
    const hook = it.depth === 3 && o.indent3 !== null
    if (!p) {
      x0 = lay.cx + it.side * (o.centerW / 2)
      y0 = lay.cy
    } else if (hook) {
      x0 = it.side === 1 ? p.x + 8 : p.x + p.w - 8
      y0 = p.y
    } else {
      x0 = it.side === 1 ? p.x + p.w : p.x
      y0 = p.y
    }
    const x1 = it.side === 1 ? it.x : it.x + it.w
    const curve = branchCurve(x0, y0, x1, it.y, it.side, hook)
    const f = relevance(it.n.score)
    const [w0, w1] = TAPER[it.depth]
    const draft = it.n.draft && it.n.via !== 'mention'
    return { to: it.n.id, cls: it.cls, draft, w: Number((((w0 + w1) / 2) * f).toFixed(2)), d: draft ? curvePath(curve) : taperPath(curve, w0 * f, w1 * f) }
  })
}
