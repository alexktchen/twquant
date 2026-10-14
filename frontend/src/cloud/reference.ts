// 歷史同區間參考（D-179）：名單上這一檔，過去「分數落在同一個區間」的股票照名單規則買進抱 20 天的報酬分布。
// 數字由本機 scripts/score_reference.py 算一次存 serving.score_reference；這裡只負責對區間與顯示。
// 給的是分布不是預測；每檔的數字來自它所屬的區間，不是針對這一檔算的。開發期是規則試出來的那段，只當參考。

/** serving.score_reference 的一列（/api/picks 每條線附上）。lo／hi＝區間 [lo, hi)，不分區間的線（大盤精選）為 null。 */
export type RefRow = {
  period: 'dev' | 'unseen'
  label: string
  bucket: string
  lo: number | null
  hi: number | null
  n: number
  mean: number | null
  median: number | null
  win_rate: number | null
  big_loss_rate: number | null
  p10: number | null
  p90: number | null
}

/** 筆數少於這個數標「樣本少」（同 configs/score_reference.yaml min_n）。 */
export const MIN_N = 30

/** 這一檔對到的列：有分數就找分數落在 [lo, hi) 的；不分區間的線（lo 是 null）全部適用。dev 在前。 */
export function refFor(rows: RefRow[] | null | undefined, score: number | null): RefRow[] {
  if (!rows?.length) return []
  const hit = rows.filter((r) => (r.lo === null || r.hi === null ? true : score !== null && score >= r.lo && score < r.hi))
  return hit.sort((a, b) => (a.period === b.period ? 0 : a.period === 'dev' ? -1 : 1))
}

/** 區間的中文：「分數 0.35～0.40」「分數 0.50 以上」「分數 0.25 以下」；不分區間＝「這條線」。 */
export function bucketText(r: RefRow): string {
  if (r.lo === null || r.hi === null) return '這條線'
  if (r.lo === 0) return `分數 ${r.hi.toFixed(2)} 以下`
  if (r.hi > 1) return `分數 ${r.lo.toFixed(2)} 以上`
  return `分數 ${r.lo.toFixed(2)}～${r.hi.toFixed(2)}`
}

const pct1 = (x: number) => `${x >= 0 ? '+' : ''}${(x * 100).toFixed(1)}%`

/** 一行：「中位數 +2.8%、55% 上漲、最差一成 −14.0% 以下（1,234 筆）」；沒有筆數＝「沒有可比的筆」。 */
export function refText(r: RefRow): string {
  if (r.n === 0 || r.median === null || r.win_rate === null) return '沒有可比的筆'
  const parts = [`中位數 ${pct1(r.median)}`, `${Math.round(r.win_rate * 100)}% 上漲`]
  if (r.p10 !== null) parts.push(`最差一成 ${pct1(r.p10)} 以下`)
  const few = r.n < MIN_N ? '，樣本少' : ''
  return `${parts.join('、')}（${r.n.toLocaleString()} 筆${few}）`
}

/**
 * 分布條：把 p10、中位數、p90 畫在 −limit～+limit 的橫軸上（百分比位置 0～100）。超出範圍的夾在邊上。
 * 回 null＝畫不出來（沒筆數）。
 */
export function refBar(r: RefRow, limit = 0.3): { p10: number; median: number; p90: number; zero: number } | null {
  if (r.p10 === null || r.p90 === null || r.median === null) return null
  const pos = (v: number) => Math.min(100, Math.max(0, ((v + limit) / (2 * limit)) * 100))
  return { p10: pos(r.p10), median: pos(r.median), p90: pos(r.p90), zero: pos(0) }
}
