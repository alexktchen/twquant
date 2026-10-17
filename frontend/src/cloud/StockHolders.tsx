// 個股頁「千張大戶」（T-386）：最近八週持股 1,000 張以上的人占總股數的比例、每週變化、人數。資料來自集保，每週六公布。
import { useEffect, useState } from 'react'
import { fetchStockHolders } from './api'
import { ratioText, weekChange, type HoldersWeek } from './holders'
import { calDate } from './stockcal'

export function StockHolders({ sid, refresh }: { sid: string; refresh: number }) {
  const [data, setData] = useState<{ sid: string; today: string; weeks: HoldersWeek[] } | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let stale = false
    setErr('')
    fetchStockHolders(sid)
      .then((r) => !stale && setData(r))
      .catch((e: Error) => !stale && setErr(e.message))
    return () => {
      stale = true
    }
  }, [sid, refresh])

  const mine = data?.sid === sid ? data : null
  if (err || !mine || mine.weeks.length === 0) return null // 讀不到或還沒累積到資料，不擋個股頁其他部分
  const weeks = [...mine.weeks].reverse() // 新到舊
  const n = mine.weeks.length
  return (
    <div className="cq-ref cq-stockcal cq-holders">
      <h3>
        千張大戶<small>持股 1,000 張以上的人占總股數的比例；集保每週六公布，最近 {n} 週</small>
      </h3>
      <ul>
        {weeks.map((w, k) => {
          const i = n - 1 - k // 在舊到新序列裡的位置
          const chg = weekChange(mine.weeks, i)
          return (
            <li key={w.date} className={k === 0 ? 'soon' : 'past'}>
              <span className="cq-stockcal-date">{calDate(w.date, mine.today)}</span>
              <b>{ratioText(w.pct)}</b>
              <span className="cq-stockcal-detail">
                {chg}
                {w.big_holders !== null && <small>{w.big_holders.toLocaleString('zh-TW')} 人</small>}
              </span>
            </li>
          )
        })}
      </ul>
      <p className="cq-ref-note">比例＝第 15 級（1,000 張以上）股數 ÷ 全部分級股數合計。大戶增加不代表會漲；只是多一個看籌碼集中或鬆動的角度。</p>
    </div>
  )
}
