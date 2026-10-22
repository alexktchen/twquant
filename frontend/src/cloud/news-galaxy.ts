// 新聞全景星雲圖（T-418）的型別與純函式：3D 投影、亮度、標籤避讓、點選。畫面在 NewsGalaxy.tsx。
// 資料由排程（scripts/news_galaxy.py）算好放新聞庫，/pub/news/galaxy 讀最新一筆。

export type GalaxyItem = {
  id: string
  x: number
  y: number
  z: number
  /** 主題群號 */
  k: number
  title: string
  src: string
  kind: 'intl' | 'tw'
  /** 有譯文 */
  tr: boolean
  t: string
  url: string
  /** 同事件群代號 */
  ev: string | null
  /** 提到的亮點（實體代號） */
  m: string[]
  /** 提到的實體名（最多 6 個，給檢視框） */
  ml: string[]
}
export type GalaxyCluster = { k: number; name: string; n: number; x: number; y: number; z: number; top: string[] }
export type GalaxyHub = { id: string; label: string; type: string; sub: string | null; n: number; x: number; y: number; z: number }
export type GalaxyData = { built: string; days: number; items: GalaxyItem[]; clusters: GalaxyCluster[]; hubs: GalaxyHub[]; links: number }

export type Vec3 = { x: number; y: number; z: number }
/** 投影後：畫面 x、y、深度（越大越近）、透視倍率 */
export type Proj = [number, number, number, number]
export type View = { s: number; x: number; y: number }
/** 目前的轉向（單位四元數 w,x,y,z）：用四元數而不是兩個角度，上下左右都能一直轉，不會卡在頭頂 */
export type Quat = [number, number, number, number]

export const FOV = 3.4
/** 電腦版一開的比例（每單位約 300px）＝亮度 1 */
export const REF_S = 300

export const ORIGIN: Vec3 = { x: 0, y: 0, z: 0 }

export const qMul = (a: Quat, b: Quat): Quat => [
  a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3],
  a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
  a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1],
  a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0],
]
/** 繞 (ax, ay, az)（單位向量）轉 rad */
export function qAxis(ax: number, ay: number, az: number, rad: number): Quat {
  const s = Math.sin(rad / 2)
  return [Math.cos(rad / 2), ax * s, ay * s, az * s]
}
export function qNorm(q: Quat): Quat {
  const n = Math.hypot(q[0], q[1], q[2], q[3]) || 1
  return [q[0] / n, q[1] / n, q[2] / n, q[3] / n]
}
/** 先繞直軸轉 yaw、再繞橫軸轉 pitch（一開始的角度用這兩個數字講比較好懂） */
export const qFromYawPitch = (yaw: number, pitch: number): Quat => qMul(qAxis(1, 0, 0, pitch), qAxis(0, 1, 0, yaw))
/** 兩個轉向有多像：1＝一樣（q 與 −q 是同一個轉向） */
export const qSame = (a: Quat, b: Quat): number => Math.abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3])
/** 從 a 往 b 轉 t（0～1），走短的那邊 */
export function qSlerp(a: Quat, b: Quat, t: number): Quat {
  let d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]
  const bb: Quat = d < 0 ? [-b[0], -b[1], -b[2], -b[3]] : b
  d = Math.abs(d)
  if (d > 0.9995) return qNorm([a[0] + (bb[0] - a[0]) * t, a[1] + (bb[1] - a[1]) * t, a[2] + (bb[2] - a[2]) * t, a[3] + (bb[3] - a[3]) * t])
  const th = Math.acos(d), s = Math.sin(th), ka = Math.sin((1 - t) * th) / s, kb = Math.sin(t * th) / s
  return [a[0] * ka + bb[0] * kb, a[1] * ka + bb[1] * kb, a[2] * ka + bb[2] * kb, a[3] * ka + bb[3] * kb]
}

/**
 * 拖曳轉動（使用者 2026-10-21：「應該要可以上下左右一直旋轉 現在好像會卡住180度」）：照「畫面上」的方向轉，
 * 往右拖＝前面的往右、往下拖＝前面的往下，不管現在轉成什麼樣子都一樣，上下左右都沒有盡頭。dx、dy 是弧度。
 */
export const dragRotate = (q: Quat, dx: number, dy: number): Quat => qNorm(qMul(qMul(qAxis(0, 1, 0, dx), qAxis(1, 0, 0, -dy)), q))

/** 依轉向 q 轉、透視投影到畫面。c＝轉動中心（放在畫面中央）：預設整團的中心，選了東西就換成它。 */
export function project(o: Vec3, q: Quat, v: View, c: Vec3 = ORIGIN): Proj {
  const ox = o.x - c.x, oy = o.y - c.y, oz = o.z - c.z
  const [w, qx, qy, qz] = q
  // v' = v + 2w(u×v) + 2u×(u×v)，u＝(qx,qy,qz)
  const tx = 2 * (qy * oz - qz * oy), ty = 2 * (qz * ox - qx * oz), tz = 2 * (qx * oy - qy * ox)
  const x1 = ox + w * tx + (qy * tz - qz * ty)
  const y1 = oy + w * ty + (qz * tx - qx * tz)
  const z1 = oz + w * tz + (qx * ty - qy * tx)
  const f = FOV / (FOV - z1)
  return [v.x + x1 * v.s * f, v.y + y1 * v.s * f, z1, f]
}

/** 轉起來不迷航（使用者 2026-10-21：「有時候再旋轉很容易迷航」）：拖曳每 px 轉幾弧度（原 0.008，手機滑 200px 就轉 92°；現在 57°） */
export const DRAG_RAD = 0.005

/** 往目標靠：每過 1 秒剩下的距離乘 e^−rate（跟幀率無關；rate 8 ≈ 0.3 秒走完九成） */
export const ease = (a: number, b: number, dt: number, rate = 8): number => b + (a - b) * Math.exp(-rate * dt)

/**
 * 選取那顆移到中央後，檢視框放它右邊；窄畫面（手機）右邊放不下 → 框貼在圖的下緣（按鈕上面），
 * 那顆改放在框上方空間的正中，不被框蓋住、手指從中間拖也不會拖到框。bottom＝下緣留給按鈕的高度。
 */
export const dockInspector = (W: number, inspW: number): boolean => W < 2 * (inspW + 26)
export const pivotScreenY = (H: number, inspH: number | null, bottom = 50, top = 40): number => (inspH === null ? H / 2 : Math.max(top, (H - bottom - inspH) / 2))

/** 遠的淡：深度 −1.15～1.15 對到 0.4～1。 */
export const depthAlpha = (z: number): number => 0.4 + 0.6 * Math.min(1, Math.max(0, (z + 1.15) / 2.3))

/** 密度係數：縮得越小點越擠 → 每顆越淡（2026-10-20 使用者：「手機看好像太亮」；手機圖區平均亮度 43 → 25）。 */
export const density = (s: number): number => Math.min(1, Math.max(0.32, Math.pow(s / REF_S, 1.1)))

/** 亮點基本半徑：則數開根號，設上限（「AI」被提到 466 次會變成一大坨白光）。 */
export const hubRadius = (n: number): number => 6 + Math.min(Math.sqrt(n), 9) * 2.2

/** 亮點顏色類：台股、國外（美股與國外公司）、族群與主題。 */
export const hubClass = (type: string): 'tw' | 'abroad' | 'cluster' => (type === 'tw' ? 'tw' : type === 'us' || type === 'company' ? 'abroad' : 'cluster')

/** 標籤要放的位置與大小（優先序照陣列順序） */
export type LabelWant = { key: string; x: number; y: number; w: number; h: number }

/**
 * 標籤避讓：依序放，夾在畫面內（下緣留 bottom 給按鈕），跟已放的重疊（含 pad）就不顯示。回每個 key 的位置；不顯示的不在結果裡。
 */
export function placeLabels(want: LabelWant[], W: number, H: number, bottom = 48, pad = 3): Map<string, { x: number; y: number }> {
  const placed: [number, number, number, number][] = []
  const out = new Map<string, { x: number; y: number }>()
  for (const l of want) {
    const x = Math.min(Math.max(l.x, l.w / 2 + 4), W - l.w / 2 - 4)
    const y = Math.min(Math.max(l.y, l.h / 2 + 4), H - l.h / 2 - bottom)
    const r: [number, number, number, number] = [x - l.w / 2 - pad, y - l.h / 2 - pad, x + l.w / 2 + pad, y + l.h / 2 + pad]
    if (placed.some((q) => r[0] < q[2] && r[2] > q[0] && r[1] < q[3] && r[3] > q[1])) continue
    placed.push(r)
    out.set(l.key, { x, y })
  }
  return out
}

/** 主題標籤放在群中心往畫面外側推一點（不蓋住群本身）。 */
export function clusterLabelAt(p: Proj, center: { x: number; y: number }): { x: number; y: number } {
  const dx = p[0] - center.x, dy = p[1] - center.y, n = Math.hypot(dx, dy) || 1
  return { x: p[0] + (dx / n) * 54, y: p[1] + (dy / n) * 40 }
}

/** 點選：先找亮點（半徑內），再找最近的新聞光點（16px 內）；都沒有 → null。 */
export function pickAt(
  x: number,
  y: number,
  hubs: { id: string; p: Proj; r: number }[],
  items: { id: string; p: Proj }[],
): { kind: 'hub' | 'item'; id: string } | null {
  let best: { kind: 'hub' | 'item'; id: string } | null = null
  let bd = Infinity
  for (const h of hubs) {
    const d = Math.hypot(h.p[0] - x, h.p[1] - y)
    if (d < Math.max(14, h.r * 0.7) && d < bd) {
      bd = d
      best = { kind: 'hub', id: h.id }
    }
  }
  if (best) return best
  for (const it of items) {
    const d = Math.hypot(it.p[0] - x, it.p[1] - y)
    if (d < 16 && d < bd) {
      bd = d
      best = { kind: 'item', id: it.id }
    }
  }
  return best
}

const TYPE_LABEL: Record<string, string> = { tw: '台股', us: '美股', company: '國外公司', group: '族群', theme: '主題' }
export const hubTypeLabel = (t: string): string => TYPE_LABEL[t] ?? t

/** 「近 7 天 1,500 則・16 個主題・10/20 20:27 更新」 */
export function galaxySummary(d: GalaxyData): string {
  const t = new Date(Date.parse(d.built) + 8 * 3600_000)
  const hm = `${t.getUTCMonth() + 1}/${t.getUTCDate()} ${String(t.getUTCHours()).padStart(2, '0')}:${String(t.getUTCMinutes()).padStart(2, '0')}`
  return `近 ${d.days} 天 ${d.items.length.toLocaleString('en-US')} 則・${d.clusters.length} 個主題・${hm} 更新`
}

/** 單獨看：「常一起出現」列幾個、每個主題先列幾則、每則列幾個其他亮點 */
export const FOCUS_RELATED = 12
export const FOCUS_GROUP_ROWS = 5
export const FOCUS_ROW_HUBS = 3

/** item＝同一件事裡最新的一則；n＝這件事有幾則 */
export type FocusRow = { item: GalaxyItem; n: number; srcs: string[]; others: GalaxyHub[] }
export type FocusGroup = { k: number; name: string; rows: FocusRow[] }
export type HubFocus = { hub: GalaxyHub; total: number; related: { hub: GalaxyHub; n: number }[]; shown: number; groups: FocusGroup[] }

/**
 * 單獨看一個亮點（使用者 2026-10-21：「針對這樣的選擇把它再單獨拎出來閱讀…可以快速看到關聯」）：
 * 提到它的新聞照主題分組（則數多的組在前、組內新的在前），同一件事只留最新一則、記下哪幾家報；
 * 「常一起出現」＝這些新聞裡還提到哪些亮點、各幾則。給 withId → 只留同時提到兩個的。找不到亮點 → null。
 */
export function hubFocus(d: GalaxyData, hubId: string, withId: string | null = null): HubFocus | null {
  const byId = new Map(d.hubs.map((h) => [h.id, h]))
  const hub = byId.get(hubId)
  if (!hub) return null
  const all = d.items.filter((it) => it.m.includes(hubId))
  const co = new Map<string, number>()
  for (const it of all) for (const m of new Set(it.m)) if (m !== hubId && byId.has(m)) co.set(m, (co.get(m) ?? 0) + 1)
  const related = [...co]
    .map(([id, n]) => ({ hub: byId.get(id)!, n }))
    .sort((a, b) => b.n - a.n || b.hub.n - a.hub.n || a.hub.label.localeCompare(b.hub.label))
    .slice(0, FOCUS_RELATED)
  const picked = withId ? all.filter((it) => it.m.includes(withId)) : all
  const newest = (a: GalaxyItem, b: GalaxyItem) => (a.t < b.t ? 1 : a.t > b.t ? -1 : 0)
  const events = new Map<string, GalaxyItem[]>()
  for (const it of [...picked].sort(newest)) {
    const key = it.ev ?? `item:${it.id}`
    events.set(key, [...(events.get(key) ?? []), it])
  }
  const groups = new Map<number, FocusRow[]>()
  for (const g of events.values()) {
    const item = g[0]
    const others = [...new Set(item.m)].filter((m) => m !== hubId && m !== withId && byId.has(m)).map((m) => byId.get(m)!)
    groups.set(item.k, [...(groups.get(item.k) ?? []), { item, n: g.length, srcs: [...new Set(g.map((x) => x.src))], others }])
  }
  const names = new Map(d.clusters.map((c) => [c.k, c.name]))
  return {
    hub,
    total: all.length,
    related,
    shown: picked.length,
    groups: [...groups]
      .map(([k, rows]) => ({ k, name: names.get(k) ?? '其他', rows }))
      .sort((a, b) => b.rows.length - a.rows.length || newest(a.rows[0].item, b.rows[0].item)),
  }
}

// ── 搜尋與牽連（T-436；使用者 2026-10-22：「想要在那頁下一個字來搜尋就出現他是怎麼牽連在一起的」，像 graphify 的圖）──
// 牽連＝同一則新聞一起被提到。兩個亮點之間的線有多粗＝有幾則新聞同時提到兩個。資料就是星雲圖的快照，不另外打 API。

/** 搜尋最多列幾個、一個亮點畫幾條最強的牽連、兩個亮點之間的路徑最多幾度（隔幾層） */
export const SEARCH_MAX = 8
export const WEB_MAX = 12
export const PATH_MAX_HOPS = 4

/** 亮點 → （另一個亮點 → 同時提到兩個的則數）；對稱 */
export type HubGraph = Map<string, Map<string, number>>

/** 從快照算亮點兩兩同時被幾則提到（同一則重複提到只算一次）。 */
export function hubGraph(d: GalaxyData): HubGraph {
  const ids = new Set(d.hubs.map((h) => h.id))
  const g: HubGraph = new Map()
  for (const id of ids) g.set(id, new Map())
  for (const it of d.items) {
    const ms = [...new Set(it.m)].filter((m) => ids.has(m))
    for (let i = 0; i < ms.length; i++)
      for (let j = i + 1; j < ms.length; j++) {
        const a = g.get(ms[i])!, b = g.get(ms[j])!
        a.set(ms[j], (a.get(ms[j]) ?? 0) + 1)
        b.set(ms[i], (b.get(ms[i]) ?? 0) + 1)
      }
  }
  return g
}

/** 拆查詢：半形／全形空白、「、」分開；最多兩個詞（兩個＝找這兩個之間的牽連） */
export function parseQuery(q: string): string[] {
  return q
    .split(/[\s　、,，]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 2)
}

export type SearchHit = { kind: 'hub'; hub: GalaxyHub } | { kind: 'item'; item: GalaxyItem }

const fold = (s: string) => s.toLowerCase()

/** 一個詞對一個亮點有多合：0＝股名或代號整個一樣、1＝開頭一樣、2＝有包含；不合 → null */
export function hubMatch(h: GalaxyHub, term: string): number | null {
  const t = fold(term), l = fold(h.label), c = h.sub ? fold(h.sub) : ''
  if (!t) return null
  if (l === t || (c && c === t)) return 0
  if (l.startsWith(t) || (c && c.startsWith(t))) return 1
  if (l.includes(t)) return 2
  return null
}

/** 最合的那個亮點（找路徑用）：合度最好、再則數多的；沒有 → null */
export function bestHub(d: GalaxyData, term: string): GalaxyHub | null {
  let best: GalaxyHub | null = null, bs = Infinity
  for (const h of d.hubs) {
    const s = hubMatch(h, term)
    if (s === null) continue
    if (s < bs || (s === bs && best && h.n > best.n)) {
      bs = s
      best = h
    }
  }
  return best
}

/**
 * 打字就列：先列亮點（股名、代號、主題；整個一樣 → 開頭一樣 → 有包含，同級則數多的在前），不夠再補標題有這個詞的新聞（新的在前）。
 * 兩個詞 → 只列第一個詞的結果（第二個詞是路徑的另一端）。
 */
export function searchGalaxy(d: GalaxyData, q: string, limit = SEARCH_MAX): SearchHit[] {
  const [term] = parseQuery(q)
  if (!term) return []
  const hubs = d.hubs
    .map((h) => ({ h, s: hubMatch(h, term) }))
    .filter((x): x is { h: GalaxyHub; s: number } => x.s !== null)
    .sort((a, b) => a.s - b.s || b.h.n - a.h.n || a.h.label.localeCompare(b.h.label))
    .slice(0, limit)
    .map((x): SearchHit => ({ kind: 'hub', hub: x.h }))
  if (hubs.length >= limit) return hubs
  const t = fold(term)
  const items = d.items
    .filter((it) => fold(it.title).includes(t))
    .sort((a, b) => (a.t < b.t ? 1 : a.t > b.t ? -1 : 0))
    .slice(0, limit - hubs.length)
    .map((it): SearchHit => ({ kind: 'item', item: it }))
  return [...hubs, ...items]
}

/** 一條牽連線：a、b 是亮點代號，n＝同時提到的則數；deg 1＝跟中心的、2＝那些亮點彼此也牽連 */
export type WebEdge = { a: string; b: string; n: number; deg: 1 | 2 }
export type HubWeb = { hub: GalaxyHub; first: { hub: GalaxyHub; n: number }[]; edges: WebEdge[] }

/**
 * 一個亮點的牽連：最常跟它一起被提到的 max 個亮點（則數多的在前、同則數亮點大的在前），
 * 加上這些亮點彼此之間的線（畫成一張小網，看得出哪幾個是一掛的）。找不到 → null。
 */
export function hubWeb(d: GalaxyData, g: HubGraph, hubId: string, max = WEB_MAX): HubWeb | null {
  const byId = new Map(d.hubs.map((h) => [h.id, h]))
  const hub = byId.get(hubId)
  const nb = g.get(hubId)
  if (!hub || !nb) return null
  const first = [...nb]
    .map(([id, n]) => ({ hub: byId.get(id)!, n }))
    .sort((a, b) => b.n - a.n || b.hub.n - a.hub.n || a.hub.label.localeCompare(b.hub.label))
    .slice(0, max)
  const edges: WebEdge[] = first.map((f) => ({ a: hubId, b: f.hub.id, n: f.n, deg: 1 }))
  for (let i = 0; i < first.length; i++)
    for (let j = i + 1; j < first.length; j++) {
      const n = g.get(first[i].hub.id)?.get(first[j].hub.id)
      if (n) edges.push({ a: first[i].hub.id, b: first[j].hub.id, n, deg: 2 })
    }
  return { hub, first, edges }
}

/** 兩個亮點之間的路徑：hubs 依序、n[i]＝hubs[i] 與 hubs[i+1] 同時被幾則提到 */
export type HubPath = { hubs: GalaxyHub[]; n: number[] }

/**
 * 找 a 到 b 怎麼牽在一起：度數（隔幾層）最少優先，同度數挑一路線最粗的（每一度成本 1 + 1/則數，所以多一度一定輸）。
 * 超過 maxHops 度或根本不相連 → null；a＝b → 只有它自己。
 */
export function hubPath(d: GalaxyData, g: HubGraph, a: string, b: string, maxHops = PATH_MAX_HOPS): HubPath | null {
  const byId = new Map(d.hubs.map((h) => [h.id, h]))
  if (!byId.has(a) || !byId.has(b)) return null
  const cost = new Map<string, number>([[a, 0]])
  const prev = new Map<string, string>()
  const done = new Set<string>()
  for (;;) {
    let cur: string | null = null, cc = Infinity
    for (const [id, c] of cost) if (!done.has(id) && (c < cc || (c === cc && cur !== null && id < cur))) {
      cur = id
      cc = c
    }
    if (cur === null || cur === b) break
    done.add(cur)
    for (const [nx, n] of g.get(cur) ?? []) {
      const c = cc + 1 + 1 / n
      if (c < (cost.get(nx) ?? Infinity)) {
        cost.set(nx, c)
        prev.set(nx, cur)
      }
    }
  }
  if (!cost.has(b)) return null
  const ids = [b]
  while (ids[0] !== a) ids.unshift(prev.get(ids[0])!)
  if (ids.length - 1 > maxHops) return null
  return { hubs: ids.map((id) => byId.get(id)!), n: ids.slice(1).map((id, i) => g.get(ids[i])!.get(id)!) }
}

// ── 證據句（T-437；使用者 2026-10-23：「如果我想要用在工作上我要怎麼定位那段字？」）──

/** 一次最多問新聞服務幾則（跟 news-service config evidence.max_ids 一樣） */
export const EVIDENCE_BATCH = 80

export type TextPart = { t: string; cls?: string }

/**
 * 把一段字切成「普通字」與「要標色的名字」：labels 依序找最早出現的（同位置長的先），給畫面用 <mark> 標出來。
 * 名字沒出現就整段是普通字。
 */
export function evidenceParts(text: string, labels: { label: string; cls: string }[]): TextPart[] {
  const ls = labels.filter((l) => l.label).sort((a, b) => b.label.length - a.label.length)
  const out: TextPart[] = []
  let rest = text
  while (rest) {
    let at = -1, hit: { label: string; cls: string } | null = null
    for (const l of ls) {
      const i = rest.indexOf(l.label)
      if (i >= 0 && (at < 0 || i < at)) {
        at = i
        hit = l
      }
    }
    if (!hit) {
      out.push({ t: rest })
      break
    }
    if (at > 0) out.push({ t: rest.slice(0, at) })
    out.push({ t: hit.label, cls: hit.cls })
    rest = rest.slice(at + hit.label.length)
  }
  return out
}

/** 還沒問過證據的新聞（fetched＝每個實體已問過哪些新聞）：任一個實體沒問過就要問 */
export function evidenceNeeded(ids: string[], entities: string[], fetched: Map<string, Set<string>>): string[] {
  return ids.filter((id) => entities.some((e) => !fetched.get(e)?.has(id)))
}
