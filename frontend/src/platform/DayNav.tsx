import type { JSX } from 'react'
import { neighbourDays } from './tracking'

/**
 * 前一天／日期選擇／後一天（T-300／T-301）。前後只在有快照的日子之間跳；
 * 日期選擇可以選任何一天，沒有快照時由頁面明示「尚未產生」。
 */
export function DayNav({ dates, day, label, hrefFor, latestHref }: {
  dates: readonly string[]
  day: string | null
  label: string
  hrefFor: (day: string) => string
  /** 目前釘在某一天時才給：回到「最新可得」。 */
  latestHref: string | null
}): JSX.Element {
  const { prev, next } = neighbourDays(dates, day)
  return <div className="platform-day-nav" role="group" aria-label={`${label}切換`}>
    {prev ? <a href={hrefFor(prev)} rel="prev">← 前一天 <span>{prev}</span></a> : <span className="platform-day-nav-off">← 沒有更早的快照</span>}
    <input type="date" aria-label={label} value={day ?? ''} onChange={event => { if (event.target.value) window.location.hash = hrefFor(event.target.value) }} />
    {next ? <a href={hrefFor(next)} rel="next">後一天 <span>{next}</span> →</a> : <span className="platform-day-nav-off">已是最新一天</span>}
    {latestHref && <a href={latestHref}>回到最新</a>}
  </div>
}
