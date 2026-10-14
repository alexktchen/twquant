// 族群熱力圖（T-355）：方塊大小＝族群當天的成交金額（佔全市場的比重），顏色＝平均漲跌（紅漲綠跌）或資金倍數。
// 純函數：版面（squarified treemap，Bruls 等 2000）與配色，不碰 DOM，供 node 單元測試。

export type Rect = { x: number; y: number; w: number; h: number }
export type Tile<K> = Rect & { key: K }
export type HeatMode = 'ret' | 'ratio'

/**
 * 把 items 依 value 切成方塊鋪滿 box，面積與 value 成正比，盡量接近正方形（大的在左上）。
 * value ≤ 0 或不是有限數的略過；同樣大的照輸入順序。
 */
export function squarify<K>(items: { key: K; value: number }[], box: Rect): Tile<K>[] {
  const valid = items.filter((it) => Number.isFinite(it.value) && it.value > 0)
  const total = valid.reduce((s, it) => s + it.value, 0)
  if (total <= 0 || box.w <= 0 || box.h <= 0) return []
  const scale = (box.w * box.h) / total
  const nodes = valid
    .map((it, i) => ({ key: it.key, area: it.value * scale, i }))
    .sort((a, b) => b.area - a.area || a.i - b.i)
  const out: Tile<K>[] = []
  let rect = { ...box }
  let row: typeof nodes = []
  // 這一列放在短邊上時，最扁的方塊有多扁（長寬比，越接近 1 越好）
  const worst = (r: typeof nodes, side: number) => {
    const s = r.reduce((a, n) => a + n.area, 0)
    const max = Math.max(...r.map((n) => n.area))
    const min = Math.min(...r.map((n) => n.area))
    return Math.max((side * side * max) / (s * s), (s * s) / (side * side * min))
  }
  const place = (r: typeof nodes) => {
    const s = r.reduce((a, n) => a + n.area, 0)
    if (rect.w >= rect.h) {
      // 寬的剩餘空間：這一列是左邊一欄，由上往下排
      const w = s / rect.h
      let y = rect.y
      for (const n of r) {
        const h = n.area / w
        out.push({ key: n.key, x: rect.x, y, w, h })
        y += h
      }
      rect = { x: rect.x + w, y: rect.y, w: rect.w - w, h: rect.h }
    } else {
      const h = s / rect.w
      let x = rect.x
      for (const n of r) {
        const w = n.area / h
        out.push({ key: n.key, x, y: rect.y, w, h })
        x += w
      }
      rect = { x: rect.x, y: rect.y + h, w: rect.w, h: rect.h - h }
    }
  }
  for (let i = 0; i < nodes.length; ) {
    const side = Math.min(rect.w, rect.h)
    const n = nodes[i]
    if (row.length === 0 || worst([...row, n], side) <= worst(row, side)) {
      row.push(n)
      i += 1
    } else {
      place(row)
      row = []
    }
  }
  if (row.length) place(row)
  return out
}

/** 顏色飽和到哪：漲跌 ±5%；資金倍數 2.5 倍（流入）／0.4 倍（流出）。 */
export const RET_FULL = 0.05
export const RATIO_HIGH = 2.5
export const RATIO_LOW = 0.4

type RGB = [number, number, number]
const BASE: RGB = [42, 45, 53] // 沒漲沒跌／平常的資金：深灰
const UP: RGB = [246, 70, 93] // 同 --up
const DOWN: RGB = [14, 203, 129] // 同 --down
const IN: RGB = [255, 122, 0] // 資金流入：同 --accent
const OUT: RGB = [61, 120, 210] // 資金比平常少：藍

function mix(to: RGB, t: number): string {
  const k = Math.min(Math.max(t, 0), 1)
  const c = BASE.map((b, i) => Math.round(b + (to[i] - b) * k))
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`
}

/** 方塊顏色。ret 模式紅漲綠跌、越多越深；ratio 模式 1 倍是灰，越多越橘、越少越藍。算不出來（null）是灰。 */
export function heatColor(v: number | null, mode: HeatMode): string {
  if (v === null || !Number.isFinite(v)) return mix(BASE, 0)
  if (mode === 'ret') return v === 0 ? mix(BASE, 0) : mix(v > 0 ? UP : DOWN, 0.18 + (0.82 * Math.abs(v)) / RET_FULL)
  if (v >= 1) return mix(IN, (v - 1) / (RATIO_HIGH - 1))
  return mix(OUT, (1 - v) / (1 - RATIO_LOW))
}

/** 方塊裡寫得下幾行：0＝太小只上色，1＝族群名，2＝再加主要數字，3＝再加另一個數字。 */
export function tileLines(t: Rect): 0 | 1 | 2 | 3 {
  if (t.w < 40 || t.h < 22) return 0
  if (t.h < 42 || t.w < 52) return 1
  return t.h >= 60 ? 3 : 2
}
