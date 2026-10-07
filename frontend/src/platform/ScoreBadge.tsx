// 每日選股每檔股票後面的總分徽章（T-325）：畫線結構＋籌碼＋基本面，滑過看三項明細。
import { useEffect, useState, type JSX } from 'react'
import { fetchFactorTop, fetchStockFactorHistory, fetchStockOhlcRecent, type FactorOption } from '../api'
import { drawSummary } from '../stock/drawLayer'
import { categoryScores, factorCardFrom } from '../stock/readings'
import { DEFAULT_SCREEN } from '../stock/store'
import { STOCK_SCORE, combineStockScore, pctTo100, type StockScore } from '../stock/stockScore'

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

function useStockScore(sid: string): StockScore | null | 'error' {
  const [s, setS] = useState<StockScore | null | 'error'>(null)
  useEffect(() => {
    let alive = true
    let p = cache.get(sid)
    if (!p) {
      p = computeScore(sid)
      cache.set(sid, p)
      p.catch(() => cache.delete(sid))
    }
    p.then((v) => alive && setS(v)).catch(() => alive && setS('error'))
    return () => {
      alive = false
    }
  }, [sid])
  return s
}

export function ScoreBadge({ sid }: { sid: string }): JSX.Element {
  const s = useStockScore(sid)
  if (s === null) return <span className="pb-score is-loading" title="計算中">…</span>
  if (s === 'error') return <span className="pb-score is-na" title="分數讀取失敗">—</span>
  const detail = s.parts.map((p) => `${p.label} ${p.value ?? '無資料'}`).join('・')
  const title = `總分 ${s.total ?? '—'}（${s.grade}）\n${detail}\n最新資料計算，不隨上方日期切換；只是要不要點進去看的篩選，未回測、不是買賣訊號`
  const tone = s.total === null ? 'is-na' : s.total >= STOCK_SCORE.bull ? 'is-bull' : s.total < STOCK_SCORE.bear ? 'is-bear' : 'is-mid'
  return (
    <span className={`pb-score ${tone}`} title={title} aria-label={title}>
      {s.total ?? '—'}
      <small>{s.parts.map((p) => p.value ?? '–').join('/')}</small>
    </span>
  )
}
