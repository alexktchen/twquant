// 個股頁「籌碼與風險」：停損基礎率（D-191 C）、千張大戶（T-386）、權證（T-385）併成一張卡，每項一行，點了才展開細節。
// 使用者 2026-10-18：「詳細頁太高了、字太多太雜亂」——原本三塊各自一大段加說明，現在摘要一行、說明只在展開後留一句。
import { useEffect, useState } from 'react'
import { fetchStockHolders, fetchStockStopHit, fetchStockWarrants } from './api'
import { ratioText as pctText, weekChange, type HoldersWeek } from './holders'
import { stopHitTitle, type StopHit } from './stophit'
import { calDate } from './stockcal'
import { factLines } from './facts'
import { ivText, moneyText, putShareText, ratioText, type WarrantDay } from './warrants'

type Data = { sid: string; today: string; hit: StopHit | null; weeks: HoldersWeek[]; days: WarrantDay[] }

export function StockFacts({ sid, refresh }: { sid: string; refresh: number }) {
  const [data, setData] = useState<Data | null>(null)
  const [open, setOpen] = useState<string | null>(null)

  useEffect(() => {
    let stale = false
    setOpen(null)
    // 三個來源各自容錯：哪個讀不到就當沒有，不擋個股頁
    Promise.all([
      fetchStockStopHit(sid).catch(() => null),
      fetchStockHolders(sid).catch(() => null),
      fetchStockWarrants(sid).catch(() => null),
    ]).then(([h, b, w]) => {
      if (stale) return
      setData({ sid, today: h?.today ?? b?.today ?? w?.today ?? '', hit: h?.hit ?? null, weeks: b?.weeks ?? [], days: w?.days ?? [] })
    })
    return () => {
      stale = true
    }
  }, [sid, refresh])

  const mine = data?.sid === sid ? data : null
  if (!mine) return null
  const lines = factLines(mine)
  if (lines.length === 0) return null

  const detail = (key: string) => {
    if (key === 'hit' && mine.hit)
      return <p className="cq-fact-more">{stopHitTitle(mine.hit)}</p>
    if (key === 'big')
      return (
        <div className="cq-fact-more">
          {[...mine.weeks].reverse().map((w, k) => (
            <div key={w.date} className="cq-fact-row">
              <span>{calDate(w.date, mine.today)}</span>
              <b>{pctText(w.pct)}</b>
              <span>{weekChange(mine.weeks, mine.weeks.length - 1 - k)}</span>
              <span>{w.big_holders === null ? '' : `${w.big_holders.toLocaleString('zh-TW')} 人`}</span>
            </div>
          ))}
          <p>持股 1,000 張以上的人占總股數的比例，集保每週六公布。</p>
        </div>
      )
    if (key === 'wr')
      return (
        <div className="cq-fact-more">
          {mine.days.map((d) => (
            <div key={d.date} className="cq-fact-row">
              <span>{calDate(d.date, mine.today)}</span>
              <b>
                {moneyText(d.call_value)}／{moneyText(d.put_value)}
              </b>
              <span>{[putShareText(d), ratioText(d.value_ratio)].filter(Boolean).join('，')}</span>
              <span>IV {ivText(d.iv_call)}</span>
            </div>
          ))}
          <p>金額是認購／認售成交金額；隱含波動用最價平的一般型權證反推，只能跟自己前幾天比。</p>
        </div>
      )
    return null
  }

  return (
    <div className="cq-ref cq-facts">
      <h3>籌碼與風險</h3>
      <ul>
        {lines.map((l) => (
          <li key={l.key} className={open === l.key ? 'on' : undefined} onClick={() => setOpen((k) => (k === l.key ? null : l.key))}>
            <div className="cq-fact-line">
              <span className="cq-fact-label">{l.label}</span>
              <span className="cq-fact-text">{l.text}</span>
              <span className="cq-fact-caret">{open === l.key ? '▴' : '▾'}</span>
            </div>
            {open === l.key && detail(l.key)}
          </li>
        ))}
      </ul>
    </div>
  )
}
