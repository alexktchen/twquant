// 首頁「行事曆」（T-379／D-189；使用者 2026-10-17：「行事曆也可以縮」）：預設收起，收起與否存在首頁版面（帳號）。
// 收起時輪播月營收截止、每天幾檔除權息／幾場法說會，以及名單股、追蹤股的那幾筆；展開看全部。
// 同日：「也要可以用股號、股名搜尋，跟推薦股或儲存的股票有關的擺前面」——搜尋框不論收起展開都能用；名單股、追蹤股一律排前面。
import { useState, type ReactNode } from 'react'
import { CardFrame } from './CardFrame'
import type { Slide } from './Reel'
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

  const row = (it: CalendarItem) => (
    <li key={it.key} onClick={() => onStock(it.stock_id, it.name)} title={it.summary ?? ''}>
      <b>{it.kind === 'conf' ? '法說會' : (DIV_KIND[it.kind] ?? it.kind)}</b>
      <span>
        {dayLabel(it.date)} {it.conf_time ?? ''} <em>{it.stock_id}</em> {it.name ?? ''}
        {listedSids.has(it.stock_id) && <i className="cq-tag prob">名單</i>}
        {watchSids.has(it.stock_id) && <i className="cq-tag">追蹤</i>}
        {it.dividend && dividendText(it.dividend) && <small className="cq-war-mute">　{dividendText(it.dividend)}</small>}
      </span>
    </li>
  )
  const revenue = calendar && (
    <li className="cq-cal-static">
      <b>月營收截止</b>
      <span>
        {dayLabel(calendar.revenue.date)}
        {calendar.revenue.days === 0 ? '（今天）' : `（還有 ${calendar.revenue.days} 天）`}
      </span>
    </li>
  )
  const one = (key: string, li: ReactNode): Slide => ({ key, node: <ul className="cq-war-cal cq-news-one">{li}</ul> })
  // 收起時輪播：名單股、追蹤股的那幾筆先，再來月營收截止與每天的檔數
  const slides: Slide[] = calendar
    ? [
        ...mine.map((it) => one(it.key, row(it))),
        one('revenue', revenue),
        ...calendarDays(items).map((d) =>
          one(
            d.date,
            <li className="cq-cal-static">
              <b className="cq-cal-day">{dayLabel(d.date)}</b>
              <span>{[d.dividends > 0 && `除權息 ${d.dividends} 檔`, d.conferences > 0 && `法說會 ${d.conferences} 場`].filter(Boolean).join('・')}</span>
            </li>,
          ),
        ),
      ]
    : []

  const search = calendar && items.length > 0 && (
    <div className="cq-cal-search">
      <input
        type="search"
        inputMode="search"
        placeholder="搜尋股號或股名（例：00694、富邦）"
        aria-label="搜尋行事曆"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {searching && (
        <button aria-label="清除搜尋" onClick={() => setQ('')}>
          ✕
        </button>
      )}
    </div>
  )

  return (
    <CardFrame
      title="行事曆"
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
        <ul className="cq-war-cal">
          {searching ? (
            found.length > 0 ? (
              found.map(row)
            ) : (
              <li className="cq-war-mute cq-cal-static">這三天沒有「{q.trim()}」的除權息或法說會。</li>
            )
          ) : (
            <>
              {revenue}
              {items.map(row)}
              {items.length === 0 && <li className="cq-war-mute cq-cal-static">這三天沒有除權息或法說會。</li>}
            </>
          )}
        </ul>
      ) : (
        <p className="cq-war-mute">讀取中…</p>
      )}
    </CardFrame>
  )
}
