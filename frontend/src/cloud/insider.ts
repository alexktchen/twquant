// 名單上的內部人申讓小標（T-384）。純函式，供 node 單元測試；後端資料形狀見 functions/_lib/insider.ts。
// 只供看盤：名單照模型排名產生，不因這個標記刪減。

/** 近 30 天的內部人持股轉讓申報摘要：幾筆、合計股數（算得出來的加總）、最近一筆日期、申報人身分。 */
export type InsiderFlag = { n: number; shares: number | null; latest: string; roles: string[] }

/** 後端往回看的天數（同 functions/_lib/insider.ts 的 INSIDER_LOOKBACK_DAYS），只用在文字。 */
export const INSIDER_DAYS = 30

/** 股數 → 「200 萬股」；null → ''。 */
export function insiderShares(shares: number | null): string {
  if (shares === null || !Number.isFinite(shares)) return ''
  if (shares >= 10_000) return `${Number((shares / 10_000).toFixed(1)).toLocaleString('zh-TW')} 萬股`
  return `${Math.round(shares).toLocaleString('zh-TW')} 股`
}

/** 名單一列的小標；沒有申報、舊後端沒有這欄 → null。 */
export function insiderTag(f: InsiderFlag | null | undefined): { text: string; title: string } | null {
  if (!f || f.n === 0) return null
  const who = f.roles.slice(0, 3).join('、')
  const shares = insiderShares(f.shares)
  const title =
    `近 ${INSIDER_DAYS} 天有 ${f.n} 筆內部人持股轉讓申報（${who}）` +
    `${shares ? `，合計約 ${shares}` : ''}，最近一筆 ${f.latest.slice(5).replace('-', '/')}。` +
    '內部人要賣股票依法要先申報；申報不等於一定會賣完。只是提醒，名單不因此刪減。點進個股看行事曆有每一筆。'
  return { text: f.n > 1 ? `內部人申讓×${f.n}` : '內部人申讓', title }
}
