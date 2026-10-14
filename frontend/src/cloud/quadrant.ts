// 族群法人象限圖（D-177 修訂三／T-358）：x＝法人（外資、投信、自營商）近 5 個交易日買賣超合計，
// y＝加速＝（近 5 日合計 − 再前 5 日合計）÷ 5，每天比前一段多買多少；泡泡大小＝族群當天成交金額。
// 右上持續加碼、右下買氣降溫、左上賣壓收斂、左下持續減碼。數字由排程算好（serving.group_inst_daily）。
// 純函數：刻度、座標換算、象限與文字，不碰 DOM，供 node 單元測試。

/** 一個族群在象限圖上的點（/api/groups 的 inst[族群]）；金額單位 TWD。 */
export type InstPoint = {
  x: number
  y: number
  net: number // 當天三大法人買賣超
  f5: number | null // 外資、投信、自營商近 5 日
  t5: number | null
  d5: number | null
  trail: { d: string; x: number; y: number }[] // 最近幾天的位置，舊到新
}
export type Quadrant = 'add' | 'cool' | 'ease' | 'cut'

/** 象限的名字（x、y 剛好是 0 算在流入、加速那一邊）。 */
export const QUADRANT_NAME: Record<Quadrant, string> = { add: '持續加碼', cool: '買氣降溫', ease: '賣壓收斂', cut: '持續減碼' }

export function quadrant(x: number, y: number): Quadrant {
  if (x >= 0) return y >= 0 ? 'add' : 'cool'
  return y >= 0 ? 'ease' : 'cut'
}

/** 1 億。 */
export const YI = 1e8

/**
 * 軸用「對稱對數」：0 附近接近線性、越大越壓縮，幾億和幾百億的族群能畫在同一張圖上（同 CMoney 那張圖的刻度）。
 * c＝線性與對數的交界（億）。
 */
export function symlog(v: number, c = 1): number {
  return Math.sign(v) * Math.log10(1 + Math.abs(v) / c)
}

const TICK_STEPS = [1, 5, 10, 50, 100, 500, 1000, 5000, 10000]

/** 一邊的刻度（億，正的；圖上正負各畫一次）：不超過 maxAbs 的 1、5、10、50…；太多（> 4 個）只留 1、10、100…。 */
export function ticks(maxAbs: number): number[] {
  const t = TICK_STEPS.filter((v) => v <= maxAbs)
  return t.length > 4 ? t.filter((v) => Math.log10(v) % 1 === 0) : t
}

export type Box = { left: number; top: number; width: number; height: number }

/** 兩軸的範圍：把所有點（含軌跡）的 |x|、|y|（億）取最大，左右（上下）對稱，0 在正中間。 */
export function extent(points: InstPoint[]): { x: number; y: number } {
  let x = 1
  let y = 1
  for (const p of points) {
    for (const q of [p, ...p.trail]) {
      x = Math.max(x, Math.abs(q.x) / YI)
      y = Math.max(y, Math.abs(q.y) / YI)
    }
  }
  return { x, y }
}

/** 億 → 畫面座標；ext 是 extent() 的結果，留 8% 邊。 */
export function scaler(ext: { x: number; y: number }, box: Box) {
  const sx = symlog(ext.x) * 1.08
  const sy = symlog(ext.y) * 1.08
  return {
    x: (yi: number) => box.left + ((symlog(yi) + sx) / (2 * sx)) * box.width,
    y: (yi: number) => box.top + ((sy - symlog(yi)) / (2 * sy)) * box.height,
  }
}

/** 泡泡半徑：面積跟成交比重成正比，最大的 rMax、最小不小於 rMin。 */
export function radius(share: number, maxShare: number, rMin: number, rMax: number): number {
  if (!(share > 0) || !(maxShare > 0)) return rMin
  return Math.max(rMin, rMax * Math.sqrt(Math.min(share, maxShare) / maxShare))
}

/** 「+840.1 億」「−3.2 億」；小於 1 億寫到小數兩位。 */
export function yiText(v: number): string {
  const a = Math.abs(v) / YI
  const s = a >= 1 ? a.toFixed(1) : a.toFixed(2)
  return `${v < 0 ? '−' : '+'}${s} 億`
}

/** 「10/14」。 */
export const md = (d: string) => `${d.slice(5, 7)}/${d.slice(8, 10)}`

/** 一句話講族群的法人：「法人近 5 日 +840.1 億（外資 +700.0 億、投信 +100.1 億、自營商 +40.0 億）・加速 +12.3 億/天・持續加碼」。 */
export function instText(p: InstPoint): string {
  const parts = [p.f5, p.t5, p.d5].every((v) => v !== null)
    ? `（外資 ${yiText(p.f5!)}、投信 ${yiText(p.t5!)}、自營商 ${yiText(p.d5!)}）`
    : ''
  return `法人近 5 日 ${yiText(p.x)}${parts}・加速 ${yiText(p.y)}/天・${QUADRANT_NAME[quadrant(p.x, p.y)]}`
}
