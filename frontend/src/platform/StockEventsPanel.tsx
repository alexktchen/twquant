import { useEffect, useState, type JSX } from 'react'
import { TPanel } from '../stock/ui'
import { eventDay } from './eventMarkers'
import { fetchStockEvents, type StockEvent } from './stockRecommendations'

type State = { events: StockEvent[] | null; error: string | null }

/** List saved recommendation days for one stock; absence of events is never inferred as a signal. */
export function StockEventsPanel({ sid, selectedDay, onSelectDay }: {
  sid: string
  selectedDay?: string | null
  onSelectDay?: (day: string) => void
}): JSX.Element {
  const [state, setState] = useState<State>({ events: null, error: null })
  useEffect(() => {
    let alive = true
    setState({ events: null, error: null })
    fetchStockEvents(sid).then(
      events => alive && setState({ events, error: null }),
      error => alive && setState({ events: null, error: error instanceof Error ? error.message : String(error) }),
    )
    return () => { alive = false }
  }, [sid])
  const body = state.error ? <div className="text-[13px] text-[var(--crit)]">{state.error}（讀取失敗，不代表這檔沒有被推薦過）</div>
    : state.events === null ? <div className="text-[13px] text-[var(--muted)]">讀取推薦事件…</div>
    : state.events.length === 0 ? <div className="text-[13px] text-[var(--muted)]">這檔股票沒有任何保存的推薦事件。</div>
    : <div className="overflow-x-auto"><table className="platform-data-table">
        <thead><tr><th>推薦日</th><th>名次</th><th>原始分數</th><th>分組</th><th>模型</th><th>模式</th></tr></thead>
        <tbody>{state.events.map(event => {
          const day = eventDay(event)
          return <tr key={`${event.run_id}:${day}`} className={selectedDay === day ? 'is-selected' : undefined}>
            <td>{onSelectDay ? <button type="button" className="text-[var(--accent)]" onClick={() => onSelectDay(day)}>{day}</button> : day}</td>
            <td>{event.rank}</td><td>{event.score.toFixed(4)}</td><td>{event.bucket}</td><td>{event.model_id}</td>
            <td>{event.mode === 'research' ? '研究' : '重建'}{event.published_at ? '' : '· 未發布'}</td>
          </tr>
        })}</tbody></table></div>
  return <TPanel title="推薦事件" sub={state.events ? `${state.events.length} 個保存 run` : undefined}>{body}</TPanel>
}
