// 首頁「行事曆」（T-379／D-189；使用者 2026-10-17：預設收起、可搜尋，名單股與追蹤股排前面）。
// 2026-10-18 改版（地鐵告示）：月營收截止一張數字卡；每筆＝圖示（除權息／法說會）＋日期＋代號名稱＋名單／追蹤小標。
import { Coins, Mic, Search, X } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { CardFrame } from './CardFrame'
import type { Slide } from './Reel'
import { Kpi } from './Section'
import { DIV_KIND, calendarDays, calendarItems, calendarMatch, dayLabel, dividendText, mineFirst, type CalendarItem, type WarRoomData } from './war'

export function CalendarCard({
  calendar,
  listedSids,
  watchSids,
  onStock,
  collapsed,
  onToggle,
}: {
  calendar: WarRoomData['calendar'] | null
  /** 今天名單上的股票 */
  listedSids: Set<string>
  watchSids: Set<string>
  onStock: (sid: string, name: string | null) => void
  collapsed: boolean
  onToggle: () => void
}) {
  const [q, setQ] = useState('')
  const isMine = (sid: string) => listedSids.has(sid) || watchSids.has(sid)
  const items = calendar ? mineFirst(calendarItems(calendar), isMine) : []
  const mine = items.filter((it) => isMine(it.stock_id))
  const searching = q.trim() !== ''
  const found = searching ? items.filter((it) => calendarMatch(it, q)) : []

  const row = (it: CalendarItem) => {
    const conf = it.kind === 'conf'
    const money = it.dividend ? dividendText(it.dividend) : ''
    return (
      <li key={it.key} onClick={() => onStock(it.stock_id, it.name)} title={it.summary ?? ''}>
        <span className={`cq-ev-icon cq-b-${conf ? 'event' : 'event'}`}>{conf ? <Mic size={15} aria-hidden="true" /> : <Coins size={15} aria-hidden="true" />}</span>
        <span className="cq-ev-date">{dayLabel(it.date)}{conf && it.conf_time ? <em>{it.conf_time}</em> : null}</span>
        <span className="cq-ev-main">
          <b>
            <em>{it.stock_id}</em> {it.name ?? ''}
            {listedSids.has(it.stock_id) && <i className="cq-b cq-b-compact cq-b-good">名單</i>}
            {watchSids.has(it.stock_id) && <i className="cq-b cq-b-compact cq-b-chip">追蹤</i>}
          </b>
          <span>
            {conf ? '法說會' : (DIV_KIND[it.kind] ?? it.kind)}
            {money ? `・${money}` : ''}
          </span>
        </span>
      </li>
    )
  }
  const one = (key: string, li: ReactNode): Slide => ({ key, node: <ul className="cq-ev cq-news-one">{li}</ul> })
  const revenueSlide = calendar && (
    <li className="cq-cal-static">
      <span className="cq-ev-icon cq-b-warn">
        <Coins size={15} aria-hidden="true" />
      </span>
      <span className="cq-ev-date">{dayLabel(calendar.revenue.date)}</span>
      <span className="cq-ev-main">
        <b>月營收截止</b>
        <span>{calendar.revenue.days === 0 ? '今天' : `還有 ${calendar.revenue.days} 天`}</span>
      </span>
    </li>
  )
  // 收起時輪播：名單股、追蹤股的那幾筆先，再來月營收截止與每天的檔數
  const slides: Slide[] = calendar
    ? [
        ...mine.map((it) => one(it.key, row(it))),
        one('revenue', revenueSlide),
        ...calendarDays(items).map((d) =>
          one(
            d.date,
            <li className="cq-cal-static">
              <span className="cq-ev-icon cq-b-event">
                <Coins size={15} aria-hidden="true" />
              </span>
              <span className="cq-ev-date">{dayLabel(d.date)}</span>
              <span className="cq-ev-main">
                <b>{[d.dividends > 0 && `除權息 ${d.dividends} 檔`, d.conferences > 0 && `法說會 ${d.conferences} 場`].filter(Boolean).join('・')}</b>
              </span>
            </li>,
          ),
        ),
      ]
    : []

  const search = calendar && items.length > 0 && (
    <div className="cq-cal-search">
      <Search size={16} aria-hidden="true" />
      <input type="search" inputMode="search" placeholder="股號或股名" aria-label="搜尋行事曆" value={q} onChange={(e) => setQ(e.target.value)} />
      {searching && (
        <button aria-label="清除搜尋" onClick={() => setQ('')}>
          <X size={16} aria-hidden="true" />
        </button>
      )}
    </div>
  )

  return (
    <CardFrame
      id="cal"
      when={calendar ? '今天起 3 天' : undefined}
      collapsed={collapsed && !searching}
      onToggle={() => {
        setQ('')
        onToggle()
      }}
      slides={slides}
      empty={calendar ? '這三天沒有除權息或法說會。' : '讀取中…'}
      top={search}
    >
      {calendar ? (
        <>
          {!searching && (
            <div className="cq-tiles cq-tiles-2">
              <Kpi label="月營收截止" value={dayLabel(calendar.revenue.date)} sub={calendar.revenue.days === 0 ? '今天' : `還有 ${calendar.revenue.days} 天`} tone={calendar.revenue.days <= 3 ? 'warn' : 'plain'} />
              <Kpi label="這 3 天" value={`${items.length}`} sub={`${items.filter((i) => i.kind !== 'conf').length} 除權息・${items.filter((i) => i.kind === 'conf').length} 法說會`} />
            </div>
          )}
          <ul className="cq-ev">
            {searching ? (
              found.length > 0 ? (
                found.map(row)
              ) : (
                <li className="cq-war-mute cq-cal-static">這三天沒有「{q.trim()}」的除權息或法說會。</li>
              )
            ) : (
              <>
                {items.map(row)}
                {items.length === 0 && <li className="cq-war-mute cq-cal-static">這三天沒有除權息或法說會。</li>}
              </>
            )}
          </ul>
        </>
      ) : (
        <p className="cq-war-mute">讀取中…</p>
      )}
    </CardFrame>
  )
}
