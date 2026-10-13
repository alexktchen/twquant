// 追蹤股排序的純函式（拖曳時算落點、移動、靠邊自動捲動），供 node 單元測試。

/** 把 from 位置的項目移到 to，其他依序遞補；位置超出範圍 → 原樣（新陣列）。 */
export function moveItem<T>(list: T[], from: number, to: number): T[] {
  const out = [...list]
  if (from < 0 || from >= out.length || to < 0 || to >= out.length || from === to) return out
  const [item] = out.splice(from, 1)
  out.splice(to, 0, item)
  return out
}

/** 手指（游標）在 y 時對到第幾列：落在哪一列的上下緣之間就是那一列；高於第一列 → 0，低於最後一列 → 最後一列。 */
export function rowAt(rects: { top: number; bottom: number }[], y: number): number {
  if (rects.length === 0) return -1
  const i = rects.findIndex((r) => y >= r.top && y < r.bottom)
  if (i >= 0) return i
  return y < rects[0].top ? 0 : rects.length - 1
}

/** 拖到捲動區上下緣 zone 像素內時每一格畫面要捲幾像素（越靠邊越快，最多 max）；不在邊緣 → 0。 */
export function edgeScroll(y: number, top: number, bottom: number, zone = 48, max = 14): number {
  if (y < top + zone) return -Math.ceil(max * Math.min(1, (top + zone - y) / zone))
  if (y > bottom - zone) return Math.ceil(max * Math.min(1, (y - (bottom - zone)) / zone))
  return 0
}
