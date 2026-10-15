// 歷史相似情境（D-182）：名單上這一檔，歷史上「大盤狀態相近、個股條件最像」的 k 次照名單規則買進抱 20 天的分布。
// 數字由每日排程算好存 serving.pick_similar；這裡只負責文字與分布條。是過去的分布，不是預測。

export type SimilarExample = { d: string; sid: string; name: string | null; r: number }

export type Similar = {
  n: number
  median: number | null
  mean: number | null
  win_rate: number | null
  big_loss_rate: number | null
  p10: number | null
  p90: number | null
  from: string | null
  to: string | null
  examples: SimilarExample[]
}

const pct1 = (x: number) => `${x >= 0 ? '+' : ''}${(x * 100).toFixed(1)}%`

/** 一行：「中位數 +2.8%、55% 上漲、虧超過 15% 的 6%、最差一成 −14.0% 以下」；沒有筆數＝「沒有可比的情境」。 */
export function similarText(s: Similar): string {
  if (s.n === 0 || s.median === null || s.win_rate === null) return '沒有可比的情境'
  const parts = [`中位數 ${pct1(s.median)}`, `${Math.round(s.win_rate * 100)}% 上漲`]
  if (s.big_loss_rate !== null) parts.push(`虧超過 15% 的 ${Math.round(s.big_loss_rate * 100)}%`)
  if (s.p10 !== null) parts.push(`最差一成 ${pct1(s.p10)} 以下`)
  return parts.join('、')
}

/** 鄰居的年份範圍：「2018～2026 年」；同一年只寫一年；沒有就是空字串。 */
export function spanText(s: Similar): string {
  if (!s.from || !s.to) return ''
  const a = s.from.slice(0, 4)
  const b = s.to.slice(0, 4)
  return a === b ? `${a} 年` : `${a}～${b} 年`
}

/** 一個例子：「2021-03-05 台積電（2330）+12.3%」；沒有名稱只寫代號。 */
export function exampleText(e: SimilarExample): string {
  const who = e.name ? `${e.name}（${e.sid}）` : e.sid
  return `${e.d} ${who} ${pct1(e.r)}`
}

/** 分布條：p10、中位數、p90 放在 −limit～+limit 的橫軸（百分比位置 0～100），超出夾在邊上；沒筆數回 null。 */
export function similarBar(s: Similar, limit = 0.3): { p10: number; median: number; p90: number; zero: number } | null {
  if (s.p10 === null || s.p90 === null || s.median === null) return null
  const pos = (v: number) => Math.min(100, Math.max(0, ((v + limit) / (2 * limit)) * 100))
  return { p10: pos(s.p10), median: pos(s.median), p90: pos(s.p90), zero: pos(0) }
}
