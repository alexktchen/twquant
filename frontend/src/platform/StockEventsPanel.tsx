import { useState, type JSX } from 'react'
import { TPanel } from '../stock/ui'
import { eventId, type MarkerGroup } from './eventMarkers'
import type { StockEvent } from './stockRecommendations'

function EventRows({ events }: { events: StockEvent[] }) {
  return <>{events.map(event => (
    <div key={eventId(event)} className="text-[11px] mono text-[var(--muted)] py-0.5">
      run {event.run_id} · 名次 {event.rank} · 分數 {event.score.toFixed(4)} · {event.bucket} · 模型 {event.model_id}
      {' · '}{event.published_at ? `發布 ${event.published_at.slice(0, 16).replace('T', ' ')}` : '尚未發布'}
      {' · '}{event.mode === 'research' ? '研究模式' : '重建歷史'}
    </div>
  ))}</>
}

/**
 * The keyboard-operable table alternative to the chart markers (PRD-003 §14).
 *
 * Absence of events is shown as absence; it is never rendered as a neutral or negative signal.
 */
export function StockEventsPanel({ groups, pending, loading, error, selectedDay, onSelectDay }: {
  groups: MarkerGroup[]
  pending: StockEvent[]
  loading: boolean
  error: string | null
  selectedDay: string | null
  onSelectDay: (day: string) => void
}): JSX.Element {
  const [open, setOpen] = useState<string | null>(null)
  const total = groups.reduce((sum, group) => sum + group.events.length, 0) + pending.length
  if (error) return <TPanel title="推薦事件"><div className="text-[13px] text-[var(--crit)]">{error}（讀取失敗，不代表這檔沒有被推薦過）</div></TPanel>
  if (loading) return <TPanel title="推薦事件"><div className="text-[13px] text-[var(--muted)]">讀取推薦事件…</div></TPanel>
  if (total === 0) return <TPanel title="推薦事件"><div className="text-[13px] text-[var(--muted)]">這檔股票沒有任何保存的推薦事件；圖上因此沒有標記。</div></TPanel>
  return (
    <TPanel title="推薦事件" sub={`${total} 筆 · 標記依實際發布日`}>
      <div className="overflow-x-auto">
        <table className="platform-data-table">
          <thead><tr><th>發布日</th><th>對應 K 棒</th><th>筆數</th><th>最佳名次</th><th>類型</th><th /></tr></thead>
          <tbody>{groups.map(group => {
            const best = Math.min(...group.events.map(event => event.rank))
            return [
              <tr key={group.day} className={selectedDay === group.day ? 'is-selected' : undefined}>
                <td><button type="button" className="text-[var(--accent)]" onClick={() => onSelectDay(group.day)}>{group.day}</button></td>
                <td>{group.mapped ? `${group.session}（前次非交易日發布）` : group.session}</td>
                <td>{group.events.length}</td>
                <td>{best}</td>
                <td>{group.kind === 'insight_pick' ? '洞悉精選' : group.kind === 'watch' ? '觀察' : group.kind === 'excluded' ? '排除' : 'ML 入選'}</td>
                <td>{group.events.length > 1 || open === group.day
                  ? <button type="button" className="text-[var(--accent)]" aria-expanded={open === group.day} onClick={() => setOpen(open === group.day ? null : group.day)}>{open === group.day ? '收合' : '展開'}</button>
                  : null}</td>
              </tr>,
              open === group.day ? <tr key={`${group.day}:detail`}><td colSpan={6}><EventRows events={group.events} /></td></tr> : null,
            ]
          })}</tbody>
        </table>
      </div>
      {pending.length > 0 && (
        <div className="mt-2 text-[11px] text-[var(--muted)]">
          有 {pending.length} 筆事件的發布日之後還沒有交易日行情，僅列於清單、圖上不定位。
          <EventRows events={pending} />
        </div>
      )}
    </TPanel>
  )
}
