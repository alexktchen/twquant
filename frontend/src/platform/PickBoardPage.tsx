import { useEffect, useState, type JSX } from 'react'
import { apiUrl } from '../api'
import { DayNav } from './DayNav'
import {
  boardDates,
  changeLog,
  exitReasonText,
  remainingSessions,
  resolveDay,
  stockLabel,
  type Board,
  type BoardDay,
  type BoardStrategy,
  type StockInfo,
} from './pickBoard'
import { routeHref, type PlatformRoute } from './routes'
import './pickboard.css'

type Load = { state: 'loading' } | { state: 'error'; message: string } | { state: 'ready'; board: Board | null }

async function fetchBoard(): Promise<Board | null> {
  const res = await fetch(apiUrl('/api/platform/pick-board'), { headers: { accept: 'application/json' } })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return ((await res.json()) as { board: Board | null }).board
}

const WEEKDAY = ['日', '一', '二', '三', '四', '五', '六']
function withWeekday(date: string): string {
  return `${date}（${WEEKDAY[new Date(`${date}T00:00:00`).getDay()]}）`
}

function dayHref(day: string | null): string {
  const q = new URLSearchParams()
  if (day) q.set('date', day)
  return routeHref('picks', undefined, q)
}

function Stock({ sid, stocks, tone }: { sid: string; stocks: Record<string, StockInfo>; tone?: 'buy' | 'sell' }) {
  return <a className={`pb-chip${tone ? ` pb-chip-${tone}` : ''}`} href={routeHref('stocks', sid)}>{stockLabel(stocks, sid)}</a>
}

/** 下一個交易日開盤要做的事——只在看最新一天時出現。 */
function NextBox({ strategy, stocks }: { strategy: BoardStrategy; stocks: Record<string, StockInfo> }) {
  const { buy, sell, rebalance } = strategy.next
  return <section className="pb-next" aria-label={`${strategy.name}下一個交易日`}>
    <h3>下一個交易日開盤{rebalance ? '・月換股' : ''}</h3>
    {!buy.length && !sell.length ? <p className="pb-quiet">不動作，續抱。</p> : <div className="pb-next-grid">
      <div><span className="pb-label pb-label-buy">買進 {buy.length}</span><div className="pb-chips">{buy.length ? buy.map(sid => <Stock key={sid} sid={sid} stocks={stocks} tone="buy" />) : <span className="pb-quiet">無</span>}</div></div>
      <div><span className="pb-label pb-label-sell">賣出 {sell.length}</span><div className="pb-chips">{sell.length ? sell.map(s => <span key={s.stock_id} className="pb-with-reason"><Stock sid={s.stock_id} stocks={stocks} tone="sell" /><small>{exitReasonText(s.reason)}</small></span>) : <span className="pb-quiet">無</span>}</div></div>
    </div>}
    {strategy.kind === 'slots' && buy.length > 0 && <p className="pb-footnote">空位不夠或開盤買不到（漲停、量不足）時，排在後面的會略過。</p>}
  </section>
}

function DayChanges({ day, stocks }: { day: BoardDay; stocks: Record<string, StockInfo> }) {
  if (!day.entered.length && !day.exited.length) return <p className="pb-quiet">這天沒有新增或剔除。</p>
  return <div className="pb-changes">
    <div><span className="pb-label pb-label-buy">新增 {day.entered.length}</span><div className="pb-chips">{day.entered.length ? day.entered.map(e => <Stock key={e.stock_id} sid={e.stock_id} stocks={stocks} tone="buy" />) : <span className="pb-quiet">無</span>}</div></div>
    <div><span className="pb-label pb-label-sell">剔除 {day.exited.length}</span><div className="pb-chips">{day.exited.length ? day.exited.map(e => <span key={e.stock_id} className="pb-with-reason"><Stock sid={e.stock_id} stocks={stocks} tone="sell" /><small>{exitReasonText(e.reason)}・抱 {e.held_sessions} 天</small></span>) : <span className="pb-quiet">無</span>}</div></div>
  </div>
}

/** 補位型的觀察名單：模型這天新看好的股票。位子滿了就不會買，只是讓你知道模型在看誰。 */
function Watch({ day, stocks }: { day: BoardDay; stocks: Record<string, StockInfo> }) {
  if (day.watch_new === undefined) return null
  const held = new Set(day.holdings.map(h => h.stock_id))
  return <section className="pb-watch" aria-label="模型新看好的股票">
    <h3>模型這天新看好 {day.watch_new.length} 檔<span className="pb-muted">（前 15 名裡、過去 20 個交易日沒出現過）</span></h3>
    {!day.watch_new.length ? <p className="pb-quiet">這天前 15 名都是老面孔。</p> : <div className="pb-chips">{day.watch_new.map(sid => <span key={sid} className="pb-with-reason"><Stock sid={sid} stocks={stocks} /><small>{held.has(sid) ? '已持有' : '位子滿了就不買'}</small></span>)}</div>}
  </section>
}

function Holdings({ strategy, day, stocks }: { strategy: BoardStrategy; day: BoardDay; stocks: Record<string, StockInfo> }) {
  const slots = strategy.kind === 'slots'
  return <section aria-label={`${strategy.name}持股`}>
    <h3>持股 {day.holdings.length} 檔{slots && strategy.slots ? <span className="pb-muted">（共 {strategy.slots} 個位子）</span> : null}</h3>
    {!day.holdings.length ? <p className="pb-quiet">空手。</p> : <div className="pb-table-wrap"><table className="pb-table">
      <thead><tr><th>股票</th><th>產業</th><th>買進日</th><th className="pb-num">已持有</th>{slots && <th className="pb-num">還剩</th>}</tr></thead>
      <tbody>{day.holdings.map(h => {
        const left = remainingSessions(strategy, h.held_sessions)
        const info = stocks[h.stock_id]
        return <tr key={h.stock_id}>
          <td><a href={routeHref('stocks', h.stock_id)}><strong>{h.stock_id}</strong> {info?.name ?? ''}</a></td>
          <td className="pb-muted">{info?.industry ?? ''}</td>
          <td>{h.entry_date}</td>
          <td className="pb-num">{h.held_sessions} 天</td>
          {slots && <td className={`pb-num${left !== null && left <= 1 ? ' pb-soon' : ''}`}>{left === null ? '' : left === 0 ? '今天到期' : `${left} 天`}</td>}
        </tr>
      })}</tbody>
    </table></div>}
  </section>
}

function History({ strategy, stocks, current }: { strategy: BoardStrategy; stocks: Record<string, StockInfo>; current: string }) {
  const log = changeLog(strategy)
  return <details className="pb-history">
    <summary>近三個月異動紀錄（{log.length} 天有進出）</summary>
    {!log.length ? <p className="pb-quiet">這段期間沒有任何進出。</p> : <ol>{log.map(d => <li key={d.date} className={d.date === current ? 'is-current' : undefined}>
      <a className="pb-history-date" href={dayHref(d.date)}>{withWeekday(d.date)}</a>
      <span className="pb-chips">
        {d.entered.map(e => <Stock key={`+${e.stock_id}`} sid={e.stock_id} stocks={stocks} tone="buy" />)}
        {d.exited.map(e => <Stock key={`-${e.stock_id}`} sid={e.stock_id} stocks={stocks} tone="sell" />)}
      </span>
    </li>)}</ol>}
  </details>
}

function StrategyCard({ strategy, day, isLatest, stocks }: { strategy: BoardStrategy; day: BoardDay; isLatest: boolean; stocks: Record<string, StockInfo> }) {
  const cadence = strategy.kind === 'slots' ? `${strategy.slots ?? ''} 個位子・每檔抱 ${strategy.hold_sessions ?? ''} 個交易日` : '每月換股・不設停損停利'
  return <article className={`pb-card pb-card-${strategy.code.toLowerCase()}`}>
    <header>
      <div className="pb-card-title"><h2>{strategy.name}</h2><span className="pb-tag">{cadence}</span></div>
      <p>{strategy.summary}</p>
    </header>
    {isLatest && <NextBox strategy={strategy} stocks={stocks} />}
    <section aria-label={`${strategy.name}當日異動`}>
      <h3>{withWeekday(day.date)} 開盤的異動</h3>
      <DayChanges day={day} stocks={stocks} />
    </section>
    <Watch day={day} stocks={stocks} />
    <Holdings strategy={strategy} day={day} stocks={stocks} />
    <History strategy={strategy} stocks={stocks} current={day.date} />
  </article>
}

/** 每日選股（D-164）：大盤精選與潛伏飆股並排，只看名單與持有天數，不顯示報酬。 */
export function PickBoardPage({ route }: { route: PlatformRoute }): JSX.Element {
  const [load, setLoad] = useState<Load>({ state: 'loading' })
  useEffect(() => {
    let alive = true
    fetchBoard()
      .then(board => alive && setLoad({ state: 'ready', board }))
      .catch((err: unknown) => alive && setLoad({ state: 'error', message: err instanceof Error ? err.message : String(err) }))
    return () => { alive = false }
  }, [])

  if (load.state === 'loading') return <p className="pb-state">讀取每日選股…</p>
  if (load.state === 'error') return <p className="pb-state pb-error">每日選股暫時讀不到（{load.message}）。請到「資料與排程」確認今天的排程有沒有跑完。</p>
  const board = load.board
  if (!board || !board.strategies.length) return <p className="pb-state">還沒有產生過每日選股。每天收盤後的排程跑完就會出現。</p>

  const dates = boardDates(board)
  const wanted = route.query.get('date')
  const shown = resolveDay(dates, wanted)
  const latest = dates[dates.length - 1]
  return <div className="pb-page">
    <div className="pb-head">
      <div>
        <h1>每日選股</h1>
        <p>資料到 <strong>{withWeekday(board.as_of)}</strong>。兩條線每天的新增、剔除和目前持股；點股票可看個股。<span className="pb-muted">頁面不顯示報酬（2026-09-09 起是保留期，績效等 2027-03 一次判讀）。</span></p>
      </div>
      <DayNav dates={dates} day={shown} label="看哪一天" hrefFor={dayHref} latestHref={shown && shown !== latest ? dayHref(null) : null} />
    </div>
    {!shown ? <p className="pb-state">{wanted} 早於紀錄起點（最早 {dates[0]}）。</p> : <div className="pb-grid">
      {board.strategies.map(s => {
        const day = s.days.find(d => d.date === shown)
        return day ? <StrategyCard key={s.code} strategy={s} day={day} isLatest={shown === latest} stocks={board.stocks} /> : null
      })}
    </div>}
    <p className="pb-footnote">持股是用回測引擎從 {board.replay_start} 起每天照規則模擬出來的（1,000 萬、零股、隔日開盤成交），不是真實帳戶。最新更新 {board.generated_at.slice(0, 16).replace('T', ' ')}。</p>
  </div>
}
