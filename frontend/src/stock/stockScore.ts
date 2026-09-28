// 個股總分（T-325）的純邏輯：畫線結構、籌碼、基本面三項各 0–100，平均成總分，給每日選股判斷「要不要點進去看」。
// 只是看盤篩選，沒有回測、不是買賣訊號；權重一律相等，事後不得依報酬調整。不 import 執行期模組，Node 測試可直接載入。

/**
 * 總分參數：分級門檻（與判讀摘要相同）、至少幾項有值才給總分；
 * 畫線結構用最近 window 根 K 棒（與 K 線卡預設 120 日視窗相同），抓 ohlcDays 天就夠。
 */
export const STOCK_SCORE = { bull: 70, bear: 40, minParts: 2, window: 120, ohlcDays: 250 } as const

export type ScorePart = { key: 'draw' | 'chip' | 'fund'; label: string; value: number | null }
export type StockScore = { parts: ScorePart[]; total: number | null; grade: '偏多' | '中性' | '偏空' | '資料不足' }

/**
 * 三項（0–100，null＝沒資料）等權平均成總分。有值的項目少於 minParts 就不給總分——
 * 只剩一項時「總分」其實就是那一項，會誤導。
 */
export function combineStockScore(draw: number | null, chip: number | null, fund: number | null, p: typeof STOCK_SCORE = STOCK_SCORE): StockScore {
  const parts: ScorePart[] = [
    { key: 'draw', label: '畫線', value: draw },
    { key: 'chip', label: '籌碼', value: chip },
    { key: 'fund', label: '基本面', value: fund },
  ]
  const vals = parts.map((x) => x.value).filter((v): v is number => v !== null)
  if (vals.length < p.minParts) return { parts, total: null, grade: '資料不足' }
  const total = Math.round(vals.reduce((a, b) => a + b, 0) / vals.length)
  return { parts, total, grade: total >= p.bull ? '偏多' : total < p.bear ? '偏空' : '中性' }
}

/** 類別分數（0–1 的方向化百分位平均）→ 0–100 整數；沒有因子值為 null。 */
export const pctTo100 = (v: number | null): number | null => (v === null ? null : Math.round(v * 100))
