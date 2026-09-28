import { useEffect, useRef, useState, type JSX } from 'react'
import { AreaSeries, ColorType, LineStyle, createChart } from 'lightweight-charts'
import { apiUrl } from '../api'
import { DOWN, LINE, MUT, UP, num } from '../theme'
import { DayNav } from './DayNav'
import {
  boardDates,
  changeLog,
  changeText,
  equitySummary,
  exitReasonText,
  remainingSessions,
  resolveDay,
  resolveTab,
  stockLabel,
  tradeStats,
  type Board,
  type BoardDay,
  type BoardStrategy,
  type EquityPoint,
  type StockInfo,
} from './pickBoard'
import { routeHref, type PlatformRoute } from './routes'
import './pickboard.css'

type Load = { state: 'loading' } | { state: 'error'; message: string } | { state: 'ready'; board: Board | null }
type Stocks = Record<string, StockInfo>

async function fetchBoard(): Promise<Board | null> {
  const res = await fetch(apiUrl('/api/platform/pick-board'), { headers: { accept: 'application/json' } })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return ((await res.json()) as { board: Board | null }).board
}

const WEEKDAY = ['日', '一', '二', '三', '四', '五', '六']
function withWeekday(date: string): string {
  return `${date}（${WEEKDAY[new Date(`${date}T00:00:00`).getDay()]}）`
}

function boardHref(tab: string | null, day: string | null): string {
  const q = new URLSearchParams()
  if (tab) q.set('m', tab)
  if (day) q.set('date', day)
  return routeHref('picks', undefined, q)
}

/** 漲跌數字：紅漲綠跌（台股慣例）。 */
function Change({ value, suffix }: { value: number | null | undefined; suffix?: string }) {
  const c = changeText(value)
  if (!c) return null
  return <span className={`pb-chg pb-chg-${c.tone}`}>{c.text}{suffix ? <small>{suffix}</small> : null}</span>
}

function Stock({ sid, stocks, tone, change, changeSuffix }: { sid: string; stocks: Stocks; tone?: 'buy' | 'sell'; change?: number | null; changeSuffix?: string }) {
  return <a className={`pb-chip${tone ? ` pb-chip-${tone}` : ''}`} href={routeHref('stocks', sid)}>
    {tone && <span className={`pb-side pb-side-${tone}`}>{tone === 'buy' ? '買' : '賣'}</span>}
    {stockLabel(stocks, sid)}
    <Change value={change} suffix={changeSuffix} />
  </a>
}

function money(x: number): string {
  return `${x > 0 ? '+' : x < 0 ? '−' : ''}${num(Math.abs(x))}`
}
function toneOf(x: number | null): string {
  return x === null || x === 0 ? 'pb-chg-flat' : x > 0 ? 'pb-chg-up' : 'pb-chg-down'
}

/** 最上面一眼看完：累計損益、當天、最大回撤、持股數。 */
function Summary({ strategy, day, initial }: { strategy: BoardStrategy; day: BoardDay; initial: number }) {
  const s = equitySummary(strategy.equity ?? [], initial, day.date)
  const t = tradeStats(strategy, day.date)
  const slots = strategy.slots
  return <div className="pb-kpis">
    <div className="pb-kpi pb-kpi-main">
      <span>累計損益</span>
      {s ? <><strong className={toneOf(s.pnl)}>{changeText(s.pnlPct)?.text ?? '—'}</strong><small className={toneOf(s.pnl)}>{money(s.pnl)} 元</small></> : <strong>—</strong>}
    </div>
    <div className="pb-kpi">
      <span>當天</span>
      {s && s.dayPct !== null && s.dayPnl !== null ? <><strong className={toneOf(s.dayPnl)}>{changeText(s.dayPct)?.text}</strong><small className={toneOf(s.dayPnl)}>{money(s.dayPnl)} 元</small></> : <strong>—</strong>}
    </div>
    <div className="pb-kpi">
      <span>持股平均漲跌</span>
      {t.avgOpen !== null ? <><strong className={toneOf(t.avgOpen)}>{changeText(t.avgOpen)?.text}</strong><small className="pb-muted">{t.open} 檔裡 {t.openUp} 檔上漲</small></> : <strong>—</strong>}
    </div>
    <div className="pb-kpi">
      <span>已賣出平均每筆</span>
      {t.avgClosed !== null ? <><strong className={toneOf(t.avgClosed)}>{changeText(t.avgClosed)?.text}</strong><small className="pb-muted">近三個月 {t.closed} 筆</small></> : <><strong>—</strong><small className="pb-muted">近三個月沒有賣出</small></>}
    </div>
    <div className="pb-kpi">
      <span>勝率（賺錢筆數）</span>
      {t.winRate !== null ? <><strong>{(t.winRate * 100).toFixed(0)}%</strong><small className="pb-muted">輔助看，平均每筆才是重點</small></> : <strong>—</strong>}
    </div>
    <div className="pb-kpi">
      <span>最大回撤</span>
      {s ? <strong className={s.maxDrawdown < 0 ? 'pb-chg-down' : 'pb-chg-flat'}>{(s.maxDrawdown * 100).toFixed(1)}%</strong> : <strong>—</strong>}
    </div>
    <div className="pb-kpi">
      <span>持股</span>
      <strong>{day.holdings.length}{slots ? <small className="pb-muted"> / {slots}</small> : null}</strong>
      {s && <small className="pb-muted">淨值 {num(s.value)}</small>}
    </div>
  </div>
}

/** 整體損益曲線：以起始資金為 0%，紅在上、綠在下；虛線標出正在看的那天。 */
function EquityChart({ equity, initial, day }: { equity: readonly EquityPoint[]; initial: number; day: string }) {
  const target = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = target.current
    if (!el || !equity.length) return
    const chart = createChart(el, {
      width: el.clientWidth, height: 220,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: MUT, fontSize: 11 },
      grid: { vertLines: { visible: false }, horzLines: { color: LINE } },
      rightPriceScale: { borderColor: LINE }, timeScale: { borderColor: LINE, fixLeftEdge: true, fixRightEdge: true },
      handleScroll: false, handleScale: false,
    })
    const last = equity[equity.length - 1].value
    const up = last >= initial
    const color = up ? UP : DOWN
    const series = chart.addSeries(AreaSeries, {
      lineColor: color, lineWidth: 2,
      topColor: up ? 'rgba(240,72,78,0.28)' : 'rgba(34,181,115,0.05)',
      bottomColor: up ? 'rgba(240,72,78,0.02)' : 'rgba(34,181,115,0.28)',
      priceLineVisible: false,
      priceFormat: { type: 'custom', formatter: (v: number) => `${v > 0 ? '+' : ''}${v.toFixed(1)}%` },
    })
    series.setData(equity.map(p => ({ time: p.date, value: (p.value / initial - 1) * 100 })))
    series.createPriceLine({ price: 0, color: MUT, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: '' })
    chart.timeScale().fitContent()
    const ro = new ResizeObserver(entries => chart.applyOptions({ width: entries[0].contentRect.width }))
    ro.observe(el)
    return () => { ro.disconnect(); chart.remove() }
  }, [equity, initial])
  const first = equity[0]?.date
  return <section className="pb-chart" aria-label="整體損益曲線">
    <div className="pb-section-head"><h3>整體損益</h3><span className="pb-muted">{first ? `${first} 起 ${num(initial / 10000)} 萬模擬，含手續費、證交稅與股利` : ''}{day !== equity[equity.length - 1]?.date ? `・看的是 ${day}` : ''}</span></div>
    {equity.length ? <div ref={target} /> : <p className="pb-quiet">這份看板沒有淨值資料，等下一次排程重算。</p>}
  </section>
}

/** 下一個交易日開盤要做的事——只在看最新一天時出現。 */
function NextBox({ strategy, stocks }: { strategy: BoardStrategy; stocks: Stocks }) {
  const { buy, sell, rebalance } = strategy.next
  return <section className="pb-next" aria-label={`${strategy.name}下一個交易日`}>
    <h3>下一個交易日開盤{rebalance ? '・月換股' : ''}</h3>
    {!buy.length && !sell.length ? <p className="pb-quiet">不動作，續抱。</p> : <div className="pb-chips">
      {buy.map(sid => <Stock key={`+${sid}`} sid={sid} stocks={stocks} tone="buy" />)}
      {sell.map(s => <span key={`-${s.stock_id}`} className="pb-with-reason"><Stock sid={s.stock_id} stocks={stocks} tone="sell" /><small>{exitReasonText(s.reason)}</small></span>)}
    </div>}
    {strategy.kind === 'slots' && buy.length > 0 && <p className="pb-footnote">空位不夠或開盤買不到（漲停、量不足）時，排在後面的會略過。</p>}
  </section>
}

/** 當天的推薦：模型前 n 名，位子滿了也照列；逐檔標出持有中／新進榜。 */
function Picks({ strategy, day, stocks }: { strategy: BoardStrategy; day: BoardDay; stocks: Stocks }) {
  const change = new Map(day.holdings.map(h => [h.stock_id, h.change]))
  const picks = day.picks
  const ordered = strategy.picks_ordered !== false
  const heldN = picks?.filter(p => p.held).length ?? 0
  const title = strategy.kind === 'slots' ? '當天推薦' : '若今天換股會挑'
  return <section className="pb-panel" aria-label={title}>
    <div className="pb-section-head"><h3>{title}{picks ? ` ${picks.length} 檔` : ''}</h3>{picks && <span className="pb-muted">持有中 {heldN}・未持有 {picks.length - heldN}</span>}</div>
    {picks === null ? <p className="pb-quiet pb-error">這天模型沒有打分（排序模型分數缺漏），列不出名單。</p>
      : picks === undefined ? <p className="pb-quiet">這份看板還沒有推薦名單，等下一次排程重算。</p>
      : !picks.length ? <p className="pb-quiet">這天沒有股票過門檻。</p>
        : <ol className="pb-pick-list">{picks.map(p => <li key={p.stock_id} className={p.held ? 'is-held' : undefined}>
          {ordered && <span className="pb-rank">{p.rank}</span>}
          <a href={routeHref('stocks', p.stock_id)}>{stockLabel(stocks, p.stock_id)}</a>
          <span className="pb-pick-tags">
            {p.new && <span className="pb-badge pb-badge-new">新</span>}
            {p.held ? <span className="pb-badge pb-badge-held">持有中</span> : <span className="pb-badge pb-badge-rec">推薦</span>}
            {p.held && <Change value={change.get(p.stock_id)} />}
          </span>
        </li>)}</ol>}
    {picks && heldN < picks.length && <p className="pb-footnote">{strategy.kind === 'slots' ? '「推薦」＝模型看好但模擬帳本沒持有（位子滿了不會買），照樣列出。「新」＝過去 20 個交易日沒進過前 15 名。' : '月換股型只在換股日照這份名單調整；平常日列出供參考。「新」＝過去 20 個交易日沒進過名單。'}</p>}
  </section>
}

function Holdings({ strategy, day, stocks }: { strategy: BoardStrategy; day: BoardDay; stocks: Stocks }) {
  const slots = strategy.kind === 'slots'
  return <section className="pb-panel" aria-label={`${strategy.name}持股`}>
    <div className="pb-section-head"><h3>目前持股 {day.holdings.length} 檔</h3>{slots && strategy.slots ? <span className="pb-muted">共 {strategy.slots} 個位子</span> : null}</div>
    {!slots && strategy.slots !== null && day.holdings.length > 0 && day.holdings.length < strategy.slots && <p className="pb-footnote">每次挑 {strategy.slots} 檔；少的是換股那天沒買到的（漲停或成交量不足），下次換股再補。</p>}
    {!day.holdings.length ? <p className="pb-quiet">空手。</p> : <div className="pb-table-wrap"><table className="pb-table">
      <thead><tr><th>股票</th><th className="pb-col-ind">產業</th><th>買進日</th><th className="pb-num">漲跌</th><th className="pb-num">持有</th>{slots && <th className="pb-num">還剩</th>}</tr></thead>
      <tbody>{day.holdings.map(h => {
        const left = remainingSessions(strategy, h.held_sessions)
        const info = stocks[h.stock_id]
        return <tr key={h.stock_id}>
          <td><a href={routeHref('stocks', h.stock_id)}><strong>{h.stock_id}</strong> {info?.name ?? ''}</a></td>
          <td className="pb-muted pb-col-ind">{info?.industry ?? ''}</td>
          <td>{h.entry_date.slice(5)}</td>
          <td className="pb-num"><Change value={h.change} /></td>
          <td className="pb-num">{h.held_sessions} 天</td>
          {slots && <td className={`pb-num${left !== null && left <= 1 ? ' pb-soon' : ''}`}>{left === null ? '' : left === 0 ? '今天到期' : `${left} 天`}</td>}
        </tr>
      })}</tbody>
    </table></div>}
  </section>
}

function DayChanges({ day, stocks }: { day: BoardDay; stocks: Stocks }) {
  return <section aria-label="當天異動">
    <div className="pb-section-head"><h3>{withWeekday(day.date)} 開盤異動</h3></div>
    {!day.entered.length && !day.exited.length ? <p className="pb-quiet">這天沒有買進或賣出。</p> : <div className="pb-chips">
      {day.entered.map(e => <Stock key={`+${e.stock_id}`} sid={e.stock_id} stocks={stocks} tone="buy" change={e.change} changeSuffix={e.change_to === 'now' ? '至今' : undefined} />)}
      {day.exited.map(e => <span key={`-${e.stock_id}`} className="pb-with-reason"><Stock sid={e.stock_id} stocks={stocks} tone="sell" change={e.change} /><small>{exitReasonText(e.reason)}・抱 {e.held_sessions} 天</small></span>)}
    </div>}
  </section>
}

function History({ strategy, stocks, current }: { strategy: BoardStrategy; stocks: Stocks; current: string }) {
  const log = changeLog(strategy)
  return <details className="pb-history">
    <summary>近三個月買賣紀錄（{log.length} 天有進出）</summary>
    <p className="pb-footnote">買進的漲跌：已賣出算到賣出價，仍持有算到最新收盤（標「至今」）；賣出的漲跌是這筆從買到賣的結果。</p>
    {!log.length ? <p className="pb-quiet">這段期間沒有任何進出。</p> : <ol>{log.map(d => <li key={d.date} className={d.date === current ? 'is-current' : undefined}>
      <a className="pb-history-date" href={boardHref(strategy.code, d.date)}>{withWeekday(d.date)}</a>
      <span className="pb-chips">
        {d.entered.map(e => <Stock key={`+${e.stock_id}`} sid={e.stock_id} stocks={stocks} tone="buy" change={e.change} changeSuffix={e.change_to === 'now' ? '至今' : undefined} />)}
        {d.exited.map(e => <Stock key={`-${e.stock_id}`} sid={e.stock_id} stocks={stocks} tone="sell" change={e.change} />)}
      </span>
    </li>)}</ol>}
  </details>
}

function Tabs({ board, active, day }: { board: Board; active: string; day: string }) {
  const initial = board.initial_capital ?? 0
  return <nav className="pb-tabs" role="tablist" aria-label="選股模型">
    {board.strategies.map(s => {
      const sum = equitySummary(s.equity ?? [], initial, day)
      return <a key={s.code} role="tab" aria-selected={s.code === active} className={`pb-tab pb-tab-${s.code.toLowerCase()}${s.code === active ? ' is-active' : ''}`} href={boardHref(s.code, day)}>
        <span className="pb-tab-name">{s.name}</span>
        {sum && <span className={`pb-tab-pnl ${toneOf(sum.pnl)}`}>{changeText(sum.pnlPct)?.text}</span>}
      </a>
    })}
  </nav>
}

/** 每日選股（D-164／D-165）：一個模型一個分頁；上面損益、中間推薦與持股、下面買賣紀錄。 */
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
  const tab = resolveTab(board.strategies.map(s => s.code), route.query.get('m')) ?? board.strategies[0].code
  const strategy = board.strategies.find(s => s.code === tab) ?? board.strategies[0]
  const day = shown ? strategy.days.find(d => d.date === shown) : undefined
  const initial = board.initial_capital ?? 0
  return <div className="pb-page">
    <div className="pb-head">
      <div>
        <h1>每日選股</h1>
        <p>資料到 <strong>{withWeekday(board.as_of)}</strong>。每個模型一頁：整體損益、每天推薦、目前持股。</p>
      </div>
      <DayNav dates={dates} day={shown} label="看哪一天" hrefFor={d => boardHref(tab, d)} latestHref={shown && shown !== latest ? boardHref(tab, null) : null} />
    </div>
    {shown && <Tabs board={board} active={tab} day={shown} />}
    {!shown || !day ? <p className="pb-state">{wanted} 早於紀錄起點（最早 {dates[0]}）。</p> : <article className={`pb-card pb-card-${strategy.code.toLowerCase()}`}>
      <header>
        <div className="pb-card-title"><h2>{strategy.name}</h2><span className="pb-tag">{strategy.kind === 'slots' ? `${strategy.slots ?? ''} 個位子・每檔抱 ${strategy.hold_sessions ?? ''} 個交易日` : '每月換股・不設停損停利'}</span></div>
        <p>{strategy.summary}</p>
      </header>
      {initial > 0 && <Summary strategy={strategy} day={day} initial={initial} />}
      {initial > 0 && <EquityChart equity={strategy.equity ?? []} initial={initial} day={day.date} />}
      {shown === latest && <NextBox strategy={strategy} stocks={board.stocks} />}
      <div className="pb-split">
        <Picks strategy={strategy} day={day} stocks={board.stocks} />
        <Holdings strategy={strategy} day={day} stocks={board.stocks} />
      </div>
      <DayChanges day={day} stocks={board.stocks} />
      <History strategy={strategy} stocks={board.stocks} current={day.date} />
    </article>}
    <p className="pb-footnote">持股與損益是回測引擎從 {board.replay_start} 起每天照規則模擬的（{initial ? `${num(initial / 10000)} 萬、` : ''}零股、隔日開盤成交），不是真實帳戶。個股漲跌＝成交價到收盤，不含股利與費用；整體損益含費用與股利。2026-09-09 起是保留期：這些數字只看、不拿來調參數，策略好壞等 2027-03 一次判讀。最新更新 {board.generated_at.slice(0, 16).replace('T', ' ')}。</p>
  </div>
}
