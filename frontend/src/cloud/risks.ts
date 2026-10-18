// 名單風險日曆的小標（T-387）。純函式，供 node 單元測試；後端資料形狀見 functions/_lib/risks.ts。
// 只供看盤：這些是「下單前最好知道」的事，名單不因此刪減。

export type RiskKind = 'disposition' | 'disposition_soon' | 'attention' | 'dividend' | 'conference' | 'revenue'
export type Risk = { kind: RiskKind; date: string | null; text: string; detail: string | null }

const WHY: Record<RiskKind, string> = {
  disposition: '處置股每幾分鐘才撮合一次、大量下單要先全額圈存，進出都不方便。',
  disposition_soon: '處置期間每幾分鐘才撮合一次、大量下單要先全額圈存，進出都不方便。',
  attention: '交易所公布的注意交易資訊：漲跌、週轉或集中度異常。連續達標會進處置。',
  dividend: '除權息當天參考價會往下調，看漲跌幅時要還原；配股會稀釀每股數字。',
  conference: '法說會前後常有大波動，消息面也會在這幾天集中出現。',
  revenue: '上月營收還沒公布，月營收因子用的還是舊數字；公布當天常有缺口。',
}

/** 小標：text 照後端；title＝細節＋為什麼要知道。 */
export function riskTag(r: Risk): { text: string; title: string; kind: RiskKind } {
  const title = [r.detail, WHY[r.kind]].filter(Boolean).join('。')
  return { text: r.text, title, kind: r.kind }
}

/** 一檔的小標清單；沒有、舊後端沒有這欄 → []。 */
export function riskTags(rs: Risk[] | null | undefined): { text: string; title: string; kind: RiskKind }[] {
  return (rs ?? []).map(riskTag)
}

/** 這條線有幾檔帶風險：「15 檔裡 2 檔處置中、3 檔近期除權息」；都沒有 → null。 */
export function riskSummary(picks: { risks?: Risk[] | null }[]): string | null {
  const count = (kinds: RiskKind[]) => picks.filter((p) => (p.risks ?? []).some((r) => kinds.includes(r.kind))).length
  const parts = [
    [count(['disposition', 'disposition_soon']), '處置'],
    [count(['attention']), '注意股'],
    [count(['dividend']), '近期除權息'],
    [count(['conference']), '近期法說會'],
    [count(['revenue']), '營收待公布'],
  ] as [number, string][]
  const shown = parts.filter(([n]) => n > 0).map(([n, label]) => `${n} 檔${label}`)
  return shown.length ? `${picks.length} 檔裡 ${shown.join('、')}` : null
}
