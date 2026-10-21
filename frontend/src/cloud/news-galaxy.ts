// 新聞全景星雲圖（T-418）的型別與純函式：3D 投影、亮度、標籤避讓、點選。畫面在 NewsGalaxy.tsx。
// 資料由排程（scripts/news_galaxy.py）算好放新聞庫，/api/news/galaxy 讀最新一筆。

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
export type Rot = { yaw: number; pitch: number }

export const FOV = 3.4
/** 電腦版一開的比例（每單位約 300px）＝亮度 1 */
export const REF_S = 300

/** 繞直軸轉 yaw、再繞橫軸轉 pitch，透視投影到畫面。 */
export function project(o: Vec3, r: Rot, v: View): Proj {
  const cy = Math.cos(r.yaw), sy = Math.sin(r.yaw), cp = Math.cos(r.pitch), sp = Math.sin(r.pitch)
  const x1 = o.x * cy + o.z * sy
  const z1 = -o.x * sy + o.z * cy
  const y1 = o.y * cp - z1 * sp
  const z2 = o.y * sp + z1 * cp
  const f = FOV / (FOV - z2)
  return [v.x + x1 * v.s * f, v.y + y1 * v.s * f, z2, f]
}

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
