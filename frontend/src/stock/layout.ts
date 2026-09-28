// 個股儀表板可拖拉版面（T-322）的純邏輯：順序、寬高、存讀。不 import 執行期模組，Node 測試可直接載入。

/** 版面參數：12 欄格、寬度最少幾欄、高度最少幾 px。 */
export const GRID = { cols: 12, minCols: 3, minHeight: 120 } as const

export type CardDefault = { id: string; w: number; tall?: boolean }
/** 使用者調過的單卡尺寸：w＝欄數；h＝px，null＝跟內容走。 */
export type CardSize = { w: number; h: number | null }
export type Layout = { order: string[]; sizes: Record<string, CardSize> }

/**
 * 合併存檔與目前的卡片清單：存檔裡不存在的卡丟掉，新卡依預設位置插在原本的鄰居之後，
 * 所以程式新增面板時使用者的版面不會亂掉、也不會漏卡。
 */
export function resolveLayout(defaults: CardDefault[], saved: Layout | null): Layout {
  const ids = defaults.map((d) => d.id)
  const known = new Set(ids)
  const order = (saved?.order ?? []).filter((id, i, a) => known.has(id) && a.indexOf(id) === i)
  for (let i = 0; i < ids.length; i++) {
    if (order.includes(ids[i])) continue
    const prev = ids.slice(0, i).reverse().find((x) => order.includes(x))
    order.splice(prev === undefined ? 0 : order.indexOf(prev) + 1, 0, ids[i])
  }
  const sizes: Record<string, CardSize> = {}
  for (const d of defaults) {
    const s = saved?.sizes[d.id]
    sizes[d.id] = s ? { w: clampCols(s.w), h: s.h === null ? null : Math.max(GRID.minHeight, s.h) } : { w: d.w, h: null }
  }
  return { order, sizes }
}

/** 把 dragged 移到 target 的位置（往後拖放在 target 之後、往前拖放在 target 之前）。 */
export function moveCard(order: string[], dragged: string, target: string): string[] {
  const from = order.indexOf(dragged)
  const to = order.indexOf(target)
  if (from < 0 || to < 0 || from === to) return order
  const next = order.filter((x) => x !== dragged)
  next.splice(to, 0, dragged)
  return next
}

export const clampCols = (w: number): number => Math.max(GRID.minCols, Math.min(GRID.cols, Math.round(w)))

/** 拖曳右下角：由卡片左緣到游標的像素寬換算成欄數（含欄距），四捨五入對齊欄線。 */
export function colsFromWidth(px: number, gridWidth: number, gap: number, cols: number = GRID.cols): number {
  const colW = (gridWidth - gap * (cols - 1)) / cols
  return clampCols((px + gap) / (colW + gap))
}

/** Parse a saved layout; anything malformed yields null instead of a half-read layout. */
export function parseLayout(raw: string | null): Layout | null {
  if (!raw) return null
  try {
    const v: unknown = JSON.parse(raw)
    if (!v || typeof v !== 'object') return null
    const o = v as Record<string, unknown>
    if (!Array.isArray(o.order) || !o.order.every((x) => typeof x === 'string')) return null
    if (!o.sizes || typeof o.sizes !== 'object') return null
    const sizes: Record<string, CardSize> = {}
    for (const [k, s] of Object.entries(o.sizes as Record<string, unknown>)) {
      const r = s as Record<string, unknown>
      if (!r || typeof r.w !== 'number' || !(r.h === null || typeof r.h === 'number')) return null
      sizes[k] = { w: r.w, h: r.h as number | null }
    }
    return { order: o.order as string[], sizes }
  } catch {
    return null
  }
}
