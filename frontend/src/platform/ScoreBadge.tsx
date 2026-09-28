// 每日選股每檔股票後面的總分徽章（T-325）：畫線結構＋籌碼＋基本面，滑過看三項明細。
import { type JSX } from 'react'
import { STOCK_SCORE } from '../stock/stockScore'
import { useStockScore } from '../stock/useStockScore'

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
