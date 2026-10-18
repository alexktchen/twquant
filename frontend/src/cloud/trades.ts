// 我的下單（T-388／D-193 §一）的前端純函式：鍵、文字。後端資料形狀見 functions/_lib/trades.ts。

export type Side = 'buy' | 'sell'
export type Trade = { sid: string; date: string; side: Side; price: number | null; qty: number | null; source: 'pick' | 'manual'; line: string | null; created_at?: string }
export type TradeList = { today: string; items: Trade[]; max: number }

/** 名單上「我買了」的鍵：名單日＋代號（來源＝pick 的買進）。 */
export function boughtKeys(trades: Trade[]): Set<string> {
  return new Set(trades.filter((t) => t.side === 'buy' && t.source === 'pick').map((t) => `${t.date}|${t.sid}`))
}

export const SIDE_TEXT: Record<Side, string> = { buy: '買', sell: '賣' }
export const SOURCE_TEXT: Record<Trade['source'], string> = { pick: '名單勾選', manual: '手動' }

/** 一筆的說明：「買 2330 10/16 @1,180 × 2 張（名單勾選・大盤精選）」 */
export function tradeText(t: Trade, lineName: (code: string | null) => string | null): string {
  const md = `${Number(t.date.slice(5, 7))}/${Number(t.date.slice(8, 10))}`
  const px = t.price === null ? '' : ` @${t.price.toLocaleString('zh-TW')}`
  const q = t.qty === null ? '' : ` × ${t.qty} 張`
  const src = t.source === 'pick' ? `名單勾選${t.line ? `・${lineName(t.line) ?? t.line}` : ''}` : '手動'
  return `${SIDE_TEXT[t.side]} ${t.sid} ${md}${px}${q}（${src}）`
}

/** 表單檢查（與後端同規則的子集，先在瀏覽器擋掉明顯錯的）：回錯誤訊息或 null。 */
export function tradeFormError(f: { sid: string; date: string; price: string; qty: string }, today: string): string | null {
  if (!/^[0-9A-Z]{4,6}$/.test(f.sid.trim().toUpperCase())) return '代號要 4～6 碼'
  if (!/^\d{4}-\d{2}-\d{2}$/.test(f.date) || f.date > today) return '日期不對或在未來'
  if (f.price.trim() !== '' && !(Number(f.price) >= 0)) return '價格要是 0 以上的數字'
  if (f.qty.trim() !== '' && !(Number.isInteger(Number(f.qty)) && Number(f.qty) > 0)) return '張數要是正整數'
  return null
}
