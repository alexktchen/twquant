// 個股頁「行事曆」（使用者 2026-10-17：「這邊裡面的個股資訊要顯示行事曆」）：這一檔接下來的除權息、法說會，
// 以及過去一年的除權息、分割減資、法說會。點法說會那一列看完整說明。
import { useEffect, useState } from 'react'
import { fetchStockCalendar } from './api'
import { STOCK_CAL_KIND, STOCK_CAL_PAST_SHOWN, calDate, calDetail, daysAway, splitByToday, type StockCalItem } from './stockcal'

export function StockCalendar({ sid, refresh }: { sid: string; refresh: number }) {
  const [data, setData] = useState<{ sid: string; today: string; items: StockCalItem[] } | null>(null)
  const [err, setErr] = useState('')
  const [morePast, setMorePast] = useState(false)
  const [open, setOpen] = useState<string | null>(null)

  useEffect(() => {
    let stale = false
    setErr('')
    setMorePast(false)
    setOpen(null)
    fetchStockCalendar(sid)
      .then((r) => !stale && setData(r))
      .catch((e: Error) => !stale && setErr(e.message))
    return () => {
      stale = true
    }
  }, [sid, refresh])

  const mine = data?.sid === sid ? data : null
  if (err) return null // 行事曆讀不到不擋個股頁其他部分
  if (!mine) return null
  const { upcoming, past } = splitByToday(mine.items, mine.today)
  const shownPast = morePast ? past : past.slice(0, STOCK_CAL_PAST_SHOWN)

  const row = (x: StockCalItem, soon: boolean) => {
    const key = `${x.date}${x.kind}${x.time ?? ''}`
    const detail = calDetail(x)
    const expandable = x.kind === 'conf' && !!x.summary
    return (
      <li
        key={key}
        className={[soon ? 'soon' : 'past', expandable ? 'tap' : ''].filter(Boolean).join(' ')}
        onClick={expandable ? () => setOpen((k) => (k === key ? null : key)) : undefined}
      >
        <span className="cq-stockcal-date">{calDate(x.date, mine.today)}</span>
        <b>{STOCK_CAL_KIND[x.kind]}</b>
        <span className="cq-stockcal-detail">
          {detail}
          {soon && <small>{daysAway(x.date, mine.today)}</small>}
        </span>
        {expandable && <p className={open === key ? 'cq-stockcal-sum on' : 'cq-stockcal-sum'}>{x.summary}</p>}
      </li>
    )
  }

  return (
    <div className="cq-ref cq-stockcal">
      <h3>
        行事曆<small>除權息、法說會、分割減資、內部人申讓；過去一年到往後四個月</small>
      </h3>
      {upcoming.length === 0 && past.length === 0 && <p>這段期間沒有除權息、法說會或分割減資的紀錄。</p>}
      {upcoming.length > 0 && <ul>{upcoming.map((x) => row(x, true))}</ul>}
      {upcoming.length === 0 && past.length > 0 && <p>接下來沒有已公告的除權息或法說會。</p>}
      {past.length > 0 && (
        <>
          <ul className="cq-stockcal-past">{shownPast.map((x) => row(x, false))}</ul>
          {past.length > STOCK_CAL_PAST_SHOWN && (
            <button className="cq-war-link" onClick={() => setMorePast(!morePast)}>
              {morePast ? '收起' : `看過去全部 ${past.length} 筆`}
            </button>
          )}
        </>
      )}
      <p className="cq-ref-note">
        除權息預告來自證交所與櫃買中心（有金額）；已經發生的只記日期與種類。法說會來自公開資訊觀測站，點一下看說明。
        內部人申讓是董監事、經理人或大股東依法在轉讓前申報的紀錄（證交所、櫃買中心每日公告），記在申報公開那天；申報不一定會真的賣完。
      </p>
    </div>
  )
}
