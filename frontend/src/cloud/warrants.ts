// 個股頁權證摘要的文字（T-385）。純函式，供 node 單元測試；後端資料形狀見 functions/_lib/warrants.ts。
// 只供看盤：權證市場的方向與熱度是多一個角度，不是買賣訊號。

export type WarrantDay = {
  date: string
  n_call: number
  n_put: number
  call_value: number
  put_value: number
  underlying_amount: number | null
  value_ratio: number | null
  iv_call: number | null
  iv_put: number | null
  iv_call_days: number | null
  iv_put_days: number | null
}

/** 金額 → 「1.2 億」「3,500 萬」「85 萬」；0 → 「0」。 */
export function moneyText(v: number): string {
  if (!Number.isFinite(v) || v <= 0) return '0'
  if (v >= 1e8) return `${(v / 1e8).toFixed(v >= 1e9 ? 0 : 1)} 億`
  return `${Math.round(v / 1e4).toLocaleString('zh-TW')} 萬`
}

/** 年化隱含波動 → 「38%」；null → '—'。 */
export function ivText(iv: number | null): string {
  return iv === null || !Number.isFinite(iv) ? '—' : `${Math.round(iv * 100)}%`
}

/** 認售占比 → 「認售 23%」；都是 0 → ''。 */
export function putShareText(d: { call_value: number; put_value: number }): string {
  const total = d.call_value + d.put_value
  if (total <= 0) return ''
  return `認售 ${Math.round((d.put_value / total) * 100)}%`
}

/** 權證成交金額對標的成交金額 → 「權證成交＝標的的 12%」；null → ''。 */
export function ratioText(r: number | null): string {
  if (r === null || !Number.isFinite(r)) return ''
  return `權證成交＝標的的 ${r >= 0.1 ? Math.round(r * 100) : (r * 100).toFixed(1)}%`
}

/** 最新一天的一句摘要：「認購 1.2 億、認售 3,500 萬（認售 23%）；權證成交＝標的的 12%；價平隱含波動 認購 38%／認售 41%」 */
export function warrantLine(d: WarrantDay): string {
  const parts = [
    `認購 ${moneyText(d.call_value)}、認售 ${moneyText(d.put_value)}${putShareText(d) ? `（${putShareText(d)}）` : ''}`,
  ]
  const r = ratioText(d.value_ratio)
  if (r) parts.push(r)
  if (d.iv_call !== null || d.iv_put !== null) parts.push(`價平隱含波動 認購 ${ivText(d.iv_call)}／認售 ${ivText(d.iv_put)}`)
  return parts.join('；')
}
