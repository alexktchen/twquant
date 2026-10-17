// 首頁「行事曆」（T-379／D-189；使用者 2026-10-17：「行事曆也可以縮」）：預設收起。
// 收起時留月營收截止、每天幾檔除權息／幾場法說會，以及名單股、追蹤股的那幾筆；展開看全部。展開／收起記在瀏覽器。
// 同日：「也要可以用股號、股名搜尋，跟推薦股或儲存的股票有關的擺前面」——搜尋框不論收起展開都能用；名單股、追蹤股一律排前面。
import { useState } from 'react'
import { CAL_KEY, loadPref, openPref, savePref } from './prefs'
import { DIV_KIND, calendarDays, calendarItems, calendarMatch, dayLabel, dividendText, mineFirst, type CalendarItem, type WarRoomData } from './war'

export function CalendarCard({
  calendar,
  listedSids,
  watchSids,
  onStock,
}: {
  calendar: WarRoomData['calendar'] | null
  /** 今天名單上的股票 */
  listedSids: Set<string>
  watchSids: Set<string>
  onStock: (sid: string, name: string | null) => void
}) {
  const [open, setOpenState] = useState(() => openPref(loadPref(CAL_KEY), false))
  const setOpen = (on: boolean) => {
    setOpenState(on)
    savePref(CAL_KEY, on ? '1' : '0')
  }

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

  return (
    <section className="cq-war-card">
      <header>
        <button className="cq-news-head" aria-expanded={open} onClick={() => setOpen(!open)}>
          <h2>行事曆</h2>
          {calendar && <span className="cq-war-when">今天起 3 天</span>}
          <span className="cq-mood-caret">{open ? '收起 ▲' : '展開 ▼'}</span>
        </button>
      </header>
      {calendar && items.length > 0 && (
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
      )}
      {calendar ? (
        <ul className="cq-war-cal">
          {!searching && (
            <li className="cq-cal-static">
              <b>月營收截止</b>
              <span>
                {dayLabel(calendar.revenue.date)}
                {calendar.revenue.days === 0 ? '（今天）' : `（還有 ${calendar.revenue.days} 天）`}
              </span>
            </li>
          )}
          {searching ? (
            found.length > 0 ? (
              found.map(row)
            ) : (
              <li className="cq-war-mute cq-cal-static">這三天沒有「{q.trim()}」的除權息或法說會。</li>
            )
          ) : open ? (
            items.map(row)
          ) : (
            <>
              {calendarDays(items).map((d) => (
                <li key={d.date} className="cq-cal-static" onClick={() => setOpen(true)}>
                  <b className="cq-cal-day">{dayLabel(d.date)}</b>
                  <span>
                    {[d.dividends > 0 && `除權息 ${d.dividends} 檔`, d.conferences > 0 && `法說會 ${d.conferences} 場`]
                      .filter(Boolean)
                      .join('・')}
                  </span>
                </li>
              ))}
              {mine.map(row)}
            </>
          )}
          {items.length === 0 && <li className="cq-war-mute cq-cal-static">這三天沒有除權息或法說會。</li>}
        </ul>
      ) : (
        <p className="cq-war-mute">讀取中…</p>
      )}
      {!open && !searching && items.length > 0 && (
        <p className="cq-war-note">
          收起時只列名單股與追蹤股的那幾筆
          <button className="cq-war-link" onClick={() => setOpen(true)}>
            看全部 {items.length} 筆 ›
          </button>
        </p>
      )}
    </section>
  )
}
