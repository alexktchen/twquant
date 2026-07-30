// 全站調色盤與數字格式（單一真相源；圖表與各頁共用）。
import type { StockMetaMap } from './api'

export const UP = '#e5484d' // 台股：紅漲
export const DOWN = '#2ea36b' // 綠跌
export const ACC = '#e0a83b'
export const MUT = '#8b98a5'
export const LINE = '#232a33'

export const pct = (x: number, d = 1) => `${x >= 0 ? '+' : ''}${(x * 100).toFixed(d)}%`
export const sign = (x: number) => (x >= 0 ? 'var(--up)' : 'var(--down)')
export const num = (x: number, d = 0) =>
  x.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })
export const oku = (x: number) => `${(x / 1e8).toFixed(1)} 億`
// 關鍵價位標籤精度：依台股價位量級調整小數位（>=1000→整數、>=100→1位、其餘2位）
export const priceFmt = (p: number) => (p >= 1000 ? p.toFixed(0) : p >= 100 ? p.toFixed(1) : p.toFixed(2))
// null 安全的帶號數字（IC/t/IR/多空價差可能為 null＝資料不足）
export const fnum = (x: number | null, d = 2, plus = true) =>
  x == null ? '—' : `${plus && x >= 0 ? '+' : ''}${x.toFixed(d)}`
export const zhang = (shares: number) => Math.round(shares / 1000)

export const labelOf = (meta: StockMetaMap, sid: string) => {
  const n = meta[sid]?.name
  return n ? `${sid} ${n}` : sid
}
