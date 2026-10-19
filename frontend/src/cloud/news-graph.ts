// 新聞關係圖（「心智爆炸圖」，使用者 2026-10-19；SPEC-017 §7、D-200）的資料形狀與版面純函式（不碰 DOM，供 node 單元測試）。
// 資料＝新聞服務 /v1/items/:id/graph 的回應：一棵樹（每個實體只留最好的那條路徑，見 news-service/src/graph.ts）。
// 版面＝放射狀樹：新聞在圓心，一度、二度、三度各一圈；每個點分到的角度跟它底下的葉子數成正比，子點在父點的角度範圍內。

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

/** 三類顏色（dataviz 驗證：深色底全兩兩通過、色盲 ΔE ≥ 9.4）：國外＝藍、台股＝橘、族群／主題＝青。顏色只表示種類，另配圖示與文字。 */
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

export type Placed = GNode & { x: number; y: number; angle: number; /** 這圈的半徑（橫向；縱向再乘 aspect） */ r: number; aspect: number }

/** 一棵樹的子點表（依分數高到低、再依名稱，結果穩定）。 */
export function childrenOf(nodes: GNode[]): Map<string | null, GNode[]> {
  const m = new Map<string | null, GNode[]>()
  for (const n of nodes) m.set(n.parent, [...(m.get(n.parent) ?? []), n])
  for (const list of m.values()) list.sort((a, b) => b.score - a.score || a.label.localeCompare(b.label, 'zh-Hant'))
  return m
}

/** 每個點分角度時的保底份量：底下葉子少的點（例：只連一家的博通）也要有位置寫名字（2026-10-19 截圖博通、超微疊在一起）。 */
const BIAS: Record<number, number> = { 1: 1.2, 2: 0.4, 3: 0 }

/**
 * 放射狀樹的座標（圓心 0,0；y 往下）。radii[d-1]＝第 d 度那圈的半徑；aspect＞1 把圈拉成直的橢圓（手機螢幕是直的）。
 * 第一度從正上方順時針排；每個點分到的角度＝（底下葉子數＋保底份量）的比例，子點平分父點的角度範圍。
 */
export function radialLayout(nodes: GNode[], radii: [number, number, number], aspect = 1): Placed[] {
  const kids = childrenOf(nodes)
  const leaves = new Map<string, number>()
  const count = (n: GNode): number => {
    const c = kids.get(n.id) ?? []
    const v = c.length === 0 ? 1 : c.reduce((a, k) => a + count(k), 0)
    leaves.set(n.id, v)
    return v
  }
  const roots = kids.get(null) ?? []
  for (const r of roots) count(r)
  const weight = (n: GNode) => (leaves.get(n.id) ?? 1) + BIAS[Math.min(n.degree, 3)]
  const total = roots.reduce((a, r) => a + weight(r), 0) || 1
  const out: Placed[] = []
  const place = (list: GNode[], from: number, span: number, sum: number) => {
    let at = from
    for (const n of list) {
      const w = (span * weight(n)) / sum
      const angle = at + w / 2
      const r = radii[Math.min(n.degree, 3) - 1]
      out.push({ ...n, angle, r, aspect, x: Number((r * Math.cos(angle)).toFixed(2)), y: Number((r * aspect * Math.sin(angle)).toFixed(2)) })
      const c = kids.get(n.id) ?? []
      if (c.length > 0) place(c, at, w, c.reduce((a, k) => a + weight(k), 0))
      at += w
    }
  }
  place(roots, -Math.PI / 2, 2 * Math.PI, total)
  return out
}

/** 父點到子點的曲線（SVG path d）：從父點沿半徑方向出發、彎到子點，像從中心炸開。 */
export function edgePath(from: { x: number; y: number }, to: Placed): string {
  const mid = to.r * 0.62
  const cx = Number((mid * Math.cos(to.angle)).toFixed(2))
  const cy = Number((mid * to.aspect * Math.sin(to.angle)).toFixed(2))
  return `M${from.x},${from.y} Q${cx},${cy} ${to.x},${to.y}`
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

/** 線粗：分數 0～1 → 1～4.5px（越粗越相關；最強的一度是 4.5）。 */
export const strokeOf = (score: number): number => Number((1 + 3.5 * Math.max(0, Math.min(1, score))).toFixed(2))

/** 三度的名字什麼時候顯示：它的上一層被點開、或它自己被選到（其他時候只畫小點，畫面不擠）。 */
export function showLabel(n: GNode, opened: Set<string>, selected: string | null): boolean {
  if (n.degree < 3) return true
  return n.id === selected || (n.parent !== null && opened.has(n.parent))
}

/** 給「清單」看（不看圖也讀得到）：依度數、再依分數。 */
export const tableRows = (nodes: GNode[]): GNode[] => [...nodes].sort((a, b) => a.degree - b.degree || b.score - a.score || a.label.localeCompare(b.label, 'zh-Hant'))

/** 最相關的台股（設計評審 2026-10-19：圖要直接回答「這則新聞最牽動哪幾檔台股」）：分數高到低、同分看度數。 */
export const topTw = (nodes: GNode[], k = 3): GNode[] =>
  nodes
    .filter((n) => n.type === 'tw')
    .sort((a, b) => b.score - a.score || a.degree - b.degree || a.label.localeCompare(b.label, 'zh-Hant'))
    .slice(0, k)

/** 二度的點底下還收著幾個三度（畫「＋N」小徽章，告訴人點了會展開）；已展開或沒有子點 → 0。 */
export function hiddenKids(n: GNode, nodes: GNode[], opened: Set<string>): number {
  if (n.degree !== 2 || opened.has(n.id)) return 0
  return nodes.filter((x) => x.parent === n.id).length
}

// ── 3D 版（使用者 2026-10-19：「可以有 3D 感嗎」）────────────────────────────
// 放射狀樹的角度與半徑照舊，三圈放在一個斜著看的軌道盤上（各圈高度不同：內低外高的漏斗，側看時三圈仍分得開）；
// 整盤繞垂直軸轉、再往下俯視，透視投影成畫面座標：近的大、遠的小。

export type P3 = { x: number; y: number; z: number }
/** 投影後的畫面座標（y 往下）；z 越大越靠近看的人；s＝近大遠小的倍率。 */
export type Proj = { x: number; y: number; z: number; s: number }

/** 每個點在 3D 的位置（y 往下為正）：heights[d-1]＝第 d 度那圈的高度。 */
export function orbitLayout(placed: Placed[], heights: [number, number, number]): Map<string, P3> {
  const out = new Map<string, P3>()
  for (const p of placed) out.set(p.id, { x: p.r * Math.cos(p.angle), y: heights[Math.min(p.degree, 3) - 1], z: p.r * Math.sin(p.angle) })
  return out
}

/** 透視投影：先繞垂直軸轉 yaw、再前後傾 pitch（負＝從上往下看），dist＝鏡頭到圓心的距離。 */
export function project(p: P3, yaw: number, pitch: number, dist: number): Proj {
  const x1 = p.x * Math.cos(yaw) - p.z * Math.sin(yaw)
  const z1 = p.x * Math.sin(yaw) + p.z * Math.cos(yaw)
  const y2 = p.y * Math.cos(pitch) - z1 * Math.sin(pitch)
  const z2 = p.y * Math.sin(pitch) + z1 * Math.cos(pitch)
  const s = dist / Math.max(1, dist - z2)
  return { x: x1 * s, y: y2 * s, z: z2, s }
}

/** 3D 的線：從父點彎到子點（控制點＝子點往圓心縮，跟平面版一樣像從中心炸開），三點各自投影。 */
export function edgePath3(from: Proj, ctrl: Proj, to: Proj): string {
  const f = (n: number) => n.toFixed(1)
  return `M${f(from.x)},${f(from.y)} Q${f(ctrl.x)},${f(ctrl.y)} ${f(to.x)},${f(to.y)}`
}

/** 高度 y、半徑 r 的水平軌道環投影成的路徑：繞著轉的時候形狀不變，是畫面上最明顯的立體線索。 */
export function orbitPath(r: number, y: number, yaw: number, pitch: number, dist: number, steps = 64): string {
  const pts: string[] = []
  for (let i = 0; i <= steps; i++) {
    const a = (2 * Math.PI * i) / steps
    const q = project({ x: r * Math.cos(a), y, z: r * Math.sin(a) }, yaw, pitch, dist)
    pts.push(`${i === 0 ? 'M' : 'L'}${q.x.toFixed(1)},${q.y.toFixed(1)}`)
  }
  return pts.join('')
}

/** 三圈投影後的外框（最外圈的最左／右／上／下），算「整盤放得下畫面」的縮放用。 */
export function orbitBounds(radii: [number, number, number], heights: [number, number, number], yaw: number, pitch: number, dist: number): { w: number; top: number; bottom: number } {
  let left = 0
  let right = 0
  let top = 0
  let bottom = 0
  radii.forEach((r, i) => {
    for (let k = 0; k < 48; k++) {
      const a = (2 * Math.PI * k) / 48
      const q = project({ x: r * Math.cos(a), y: heights[i], z: r * Math.sin(a) }, yaw, pitch, dist)
      left = Math.min(left, q.x)
      right = Math.max(right, q.x)
      top = Math.min(top, q.y)
      bottom = Math.max(bottom, q.y)
    }
  })
  return { w: right - left, top, bottom }
}
