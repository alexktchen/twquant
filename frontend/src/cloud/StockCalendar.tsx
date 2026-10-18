// 個股頁「事件」（原「行事曆」，使用者 2026-10-17 要的）：這一檔接下來的除權息、法說會，以及過去一年的除權息、分割減資、法說會、內部人申讓。
// 2026-10-18 改版（「一目瞭然、多用符號」）：每列＝圖示＋日期＋幾天後＋種類＋一句；過去的預設收起；法說會與申讓點了看全文。
import { CalendarDays, ChevronDown, Coins, Mic, Scissors, UserMinus, type LucideIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import { fetchStockCalendar } from './api'
import { Section } from './Section'
import { STOCK_CAL_KIND, calDate, calDetail, daysAway, splitByToday, type StockCalItem } from './stockcal'

const KIND_ICON: Record<StockCalItem['kind'], LucideIcon> = {
  cash: Coins,
  stock: Coins,
  both: Coins,
  split: Scissors,
  reduce: Scissors,
  conf: Mic,
  insider: UserMinus,
}
const KIND_TONE: Record<StockCalItem['kind'], string> = {
  cash: 'event',
  stock: 'event',
  both: 'event',
  split: 'chip',
  reduce: 'chip',
  conf: 'event',
  insider: 'warn',
}

export function StockCalendar({ sid, refresh }: { sid: string; refresh: number }) {
  const [data, setData] = useState<{ sid: string; today: string; items: StockCalItem[] } | null>(null)
  const [err, setErr] = useState('')
  const [showPast, setShowPast] = useState(false)
  const [open, setOpen] = useState<string | null>(null)

  useEffect(() => {
    let stale = false
    setErr('')
    setShowPast(false)
    setOpen(null)
    fetchStockCalendar(sid)
      .then((r) => !stale && setData(r))
      .catch((e: Error) => !stale && setErr(e.message))
    return () => {
      stale = true
    }
  }, [sid, refresh])

  const mine = data?.sid === sid ? data : null
  if (err) return null // 讀不到不擋個股頁其他部分
  if (!mine) return null
  const { upcoming, past } = splitByToday(mine.items, mine.today)
  if (upcoming.length === 0 && past.length === 0) return null

  const row = (x: StockCalItem, soon: boolean) => {
    const key = `${x.date}${x.kind}${x.time ?? ''}${x.summary ?? ''}`
    const I = KIND_ICON[x.kind]
    const detail = calDetail(x)
    const long = (x.kind === 'conf' || x.kind === 'insider') && !!x.summary
    const away = soon ? daysAway(x.date, mine.today) : ''
    return (
      <li
        key={key}
        className={[soon ? 'soon' : 'past', long ? 'tap' : '', open === key ? 'on' : ''].filter(Boolean).join(' ')}
        onClick={long ? () => setOpen((k) => (k === key ? null : key)) : undefined}
      >
        <span className={`cq-ev-icon cq-b-${KIND_TONE[x.kind]}`}>
          <I size={15} strokeWidth={2.2} aria-hidden="true" />
        </span>
        <span className="cq-ev-date">
          {calDate(x.date, mine.today)}
          {away && <em>{away}</em>}
        </span>
        <span className="cq-ev-main">
          <b>{STOCK_CAL_KIND[x.kind]}</b>
          {x.kind === 'conf' ? <span>{x.time ?? ''}</span> : x.kind !== 'insider' && detail && <span>{detail}</span>}
        </span>
        {long && <p className={open === key ? 'cq-ev-sum on' : 'cq-ev-sum'}>{x.kind === 'insider' ? detail : x.summary}</p>}
      </li>
    )
  }

  return (
    <Section icon={CalendarDays} title="事件" meta={upcoming.length ? `接下來 ${upcoming.length} 件` : '接下來沒有已公告的'}>
      {upcoming.length > 0 && <ul className="cq-ev">{upcoming.map((x) => row(x, true))}</ul>}
      {past.length > 0 && (
        <>
          <button className="cq-more" onClick={() => setShowPast(!showPast)} aria-expanded={showPast}>
            {showPast ? '收起過去' : `過去一年 ${past.length} 件`}
            <ChevronDown size={14} className={showPast ? 'flip' : undefined} aria-hidden="true" />
          </button>
          {showPast && <ul className="cq-ev cq-ev-past">{past.map((x) => row(x, false))}</ul>}
        </>
      )}
    </Section>
  )
}
