// 「同波動股票 20 日內碰 −15%／+20%」的文字（D-191 §三 選項 C，使用者 2026-10-18：「C」）。純函式，供 node 單元測試。
// 這是歷史基礎率的查表，不是預測；只供看盤，名單不因此刪減。

export type StopHit = { vol: number; group: number; down15: number; up20: number; n: number; vol_lo: number | null; vol_hi: number | null }

const pc = (x: number, digits = 0) => `${(x * 100).toFixed(digits)}%`

/** 名單列下面的一行小字；沒資料、舊後端沒有這欄 → null。 */
export function stopHitLine(h: StopHit | null | undefined): string | null {
  if (!h) return null
  return `同波動股票 20 日內碰 −15%：${pc(h.down15)}、碰 +20%：${pc(h.up20)}`
}

/** 滑過的說明：這檔的日波動度、落在十組的第幾組與界線、樣本數、怎麼讀。 */
export function stopHitTitle(h: StopHit | null | undefined): string {
  if (!h) return ''
  const lo = h.vol_lo === null ? '' : `${pc(h.vol_lo, 2)} 以上`
  const hi = h.vol_hi === null ? '' : `${pc(h.vol_hi, 2)} 以下`
  const range = [lo, hi].filter(Boolean).join('、') || '全部'
  return (
    `這檔近 20 日的日波動度 ${pc(h.vol, 2)}，在全市場十組裡是第 ${h.group + 1} 組（${range}）。` +
    `2017-09 到 2026-09 同一組的 ${h.n.toLocaleString('zh-TW')} 筆裡，20 個交易日內最低收盤跌破 −15% 的有 ${pc(h.down15, 1)}、最高收盤漲過 +20% 的有 ${pc(h.up20, 1)}。` +
    '這是波動度的歷史基礎率，不是對這檔的預測；高波動是雙向的。只供參考，名單不因此刪減。'
  )
}
