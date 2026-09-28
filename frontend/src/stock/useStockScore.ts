// 個股總分的取數與快取（T-325／T-326）：每日選股徽章與個股頁頂端共用，同一檔整個 App 只算一次。
import { useEffect, useState } from 'react'
import { fetchFactorTop, fetchStockFactorHistory, fetchStockOhlcRecent, type FactorOption } from '../api'
import { drawSummary } from './drawLayer'
import { categoryScores, factorCardFrom } from './readings'
import { DEFAULT_SCREEN } from './store'
import { STOCK_SCORE, combineStockScore, pctTo100, type StockScore } from './stockScore'

// 同一檔在推薦名單與持股表都會出現：整頁共用一份請求，不重複打 API。
const cache = new Map<string, Promise<StockScore>>()
const SCORE_CATEGORIES = ['chip', 'fundamental']
// 因子清單（名稱、方向）整頁只抓一次；/api/stocks/{sid}/factors 每檔要重算全市場百分位太慢，
// 改走個股頁因子雷達同一條路：清單＋ factors/history?days=1。
let optionsP: Promise<FactorOption[]> | null = null
const factorOptions = () => {
  if (!optionsP) {
    // 總分只用籌碼、基本面兩類；價格／量能／ML 因子不抓（72 個因子一檔 0.6 秒，只抓兩類快很多）
    optionsP = fetchFactorTop(DEFAULT_SCREEN.factor, 1).then((r) => r.available.filter((o) => SCORE_CATEGORIES.includes(o.factor.split('.')[0])))
    optionsP.catch(() => { optionsP = null })
  }
  return optionsP
}

async function computeScore(sid: string): Promise<StockScore> {
  // 兩邊各自失敗不拖累另一邊：缺的那項記 null，總分照規則只用有值的項目
  const [bars, card] = await Promise.all([
    fetchStockOhlcRecent(sid, STOCK_SCORE.ohlcDays).catch(() => null),
    factorOptions()
      .then((opts) => fetchStockFactorHistory(sid, opts.map((o) => o.factor), 1).then((h) => factorCardFrom(h.rows, opts)))
      .catch(() => null),
  ])
  const draw = bars ? drawSummary(bars.slice(-STOCK_SCORE.window)) : null
  const cats = card ? categoryScores(card.factors) : []
  const cat = (c: string) => pctTo100(cats.find((x) => x.category === c)?.value ?? null)
  return combineStockScore(draw ? Math.round(draw.pct * 100) : null, cat('chip'), cat('fundamental'))
}

export function useStockScore(sid: string): StockScore | null | 'error' {
  // 結果連同股號一起存：換股後、新分數回來前，舊股票的分數不能冒充新股票的（顯示「計算中」）
  const [s, setS] = useState<{ sid: string; v: StockScore | 'error' } | null>(null)
  useEffect(() => {
    let alive = true
    let p = cache.get(sid)
    if (!p) {
      p = computeScore(sid)
      cache.set(sid, p)
      p.catch(() => cache.delete(sid))
    }
    p.then((v) => alive && setS({ sid, v })).catch(() => alive && setS({ sid, v: 'error' }))
    return () => {
      alive = false
    }
  }, [sid])
  return s !== null && s.sid === sid ? s.v : null
}
