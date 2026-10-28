// 個股當沖分頁（T-458／D-221）：盤中強勢股榜＋網友原版模擬單；復盤可以拉回任何一刻看當時榜上有誰、抱什麼、做了什麼。
// 使用者 2026-10-28：「那可以開始規劃做一個個股當沖的功能嗎」→「都做」「先用他預設，但是可以調整」。
import { Activity, BookOpen, History, Layers, ListOrdered, Pause, Play, Radio, ReceiptText, Settings2, Wallet } from 'lucide-react'
import { createChart, createSeriesMarkers, LineSeries, LineStyle, type SeriesMarker, type Time, type UTCTimestamp } from 'lightweight-charts'
import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchDayTrade, fetchDayTradeDays, fetchDayTradeHistory } from './api'
import {
  CONFIG_TEXT,
  EXIT_TEXT,
  MARKET_STATE_TEXT,
  boardAt,
  boardTag,
  pct,
  replayAt,
  roughOpenPnl,
  secOf,
  shouldPoll,
  tone,
  yuan,
  type DayEvent,
  type DayTrade,
  type DayTradeDay,
  type DayTradeHistory,
  type DayTradeRow,
} from './daytrade'
import { Kpi, Section } from './Section'
import './futures.css'
import './daytrade.css'

type Mode = 'live' | 'replay'
const POLL_MS = 15_000
const PLAY_MS = 400
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))
const num = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? '—' : v.toFixed(digits))
const price = (v: number | null | undefined) => (v === null || v === undefined ? '—' : String(Math.round(v * 100) / 100))

const KIND: Record<DayEvent['kind'], { text: string; cls: string }> = {
  signal: { text: '訊號', cls: 'cq-fu-k-signal' },
  fill: { text: '買進', cls: 'cq-fu-k-fill' },
  exit: { text: '賣出', cls: 'cq-fu-k-exit' },
  info: { text: '說明', cls: 'cq-fu-k-info' },
}

function useLiveDay(enabled: boolean, refresh: number) {
  const [data, setData] = useState<DayTradeDay | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    if (!enabled) return
    let stop = false
    const run = () =>
      fetchDayTrade()
        .then((d) => !stop && (setData(d), setErr(null)))
        .catch((e) => !stop && setErr(errText(e)))
    run()
    const id = setInterval(() => shouldPoll(new Date(), null) && run(), POLL_MS)
    return () => {
      stop = true
      clearInterval(id)
    }
  }, [enabled, refresh])
  return { data, err }
}

function MarketBadges({ d }: { d: DayTradeDay }) {
  const m = d.market
  return (
    <div className="cq-dt-badges">
      <b className="cq-dt-day">{d.day}</b>
      <span className="cq-cat cq-cat-view">{d.source === 'fubon_live' ? '富邦即時' : '1 分 K 重播'}</span>
      <span className={`cq-cat ${d.status === 'live' ? 'cq-cat-event' : 'cq-cat-quote'}`}>{d.status === 'live' ? `盤中・${d.board_time ?? '—'}` : '已收盤'}</span>
      <span className={`cq-cat ${m.state === 'PANIC' || m.state === 'WEAK' ? 'cq-cat-list' : 'cq-cat-group'}`}>大盤 {MARKET_STATE_TEXT[m.state ?? 'UNKNOWN'] ?? m.state}</span>
      <span className={`cq-dt-pct ${tone(m.taiex_pct)}`}>加權 {pct(m.taiex_pct)}</span>
      <span className={`cq-dt-pct ${tone(m.tpex_pct)}`}>櫃買 {pct(m.tpex_pct)}</span>
      {m.momentum_allowed === false && <span className="cq-cat cq-cat-list">動能不准進場</span>}
      {m.trend_allowed === false && <span className="cq-cat cq-cat-list">趨勢不准進場</span>}
    </div>
  )
}

function Board({ d }: { d: DayTradeDay }) {
  if (d.board.length === 0) return <p className="cq-empty">{d.board_time ? '這一輪沒有股票過 70 分。' : '09:00 開盤後第一輪掃描才有。'}</p>
  return (
    <ol className="cq-dt-board">
      {d.board.map((r) => {
        const tag = boardTag(r.symbol, d)
        return (
          <li key={r.symbol} className={tag ?? ''}>
            <span className="cq-dt-rank">{r.rank ?? '—'}</span>
            <div className="cq-dt-name">
              <b>
                {r.name} <small>{r.symbol}</small>
              </b>
              <span>
                分數 {num(r.score, 0)}・比大盤 {pct(r.market_relative_strength, 1)}・離高點 {num(r.distance_to_intraday_high_pct)}%
              </span>
            </div>
            <div className="cq-dt-right">
              <b className={tone(r.change_pct)}>{pct(r.change_pct)}</b>
              {tag && <em className={`cq-dt-tag ${tag}`}>{tag === 'holding' ? '持有中' : '今天做過'}</em>}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

function Positions({ rows }: { rows: { name: string; symbol: string; quantity: number; entry_price: number; entry_time: string | null; stop?: number; now: number | null; pnl: number | null }[] }) {
  if (rows.length === 0) return <p className="cq-empty">現在沒有持股。</p>
  return (
    <ul className="cq-fu-board cq-dt-pos">
      {rows.map((p) => (
        <li key={p.symbol}>
          <div>
            <b className="cq-fu-sleeve long">
              {p.name} {p.symbol}
            </b>
            <span>
              {p.quantity} 張 @ {price(p.entry_price)}（{p.entry_time ?? '—'}）{p.stop ? `・停損 ${price(p.stop)}` : ''}・現價 {price(p.now)}
            </span>
          </div>
          <b className={`cq-fu-pnl ${tone(p.pnl)}`}>{p.pnl === null ? '—' : yuan(p.pnl)}</b>
        </li>
      ))}
    </ul>
  )
}

function Timeline({ events, newestFirst }: { events: DayEvent[]; newestFirst: boolean }) {
  const rows = newestFirst ? [...events].reverse() : events
  if (rows.length === 0) return <p className="cq-empty">還沒有事件：有股票進榜、出現訊號才有。</p>
  return (
    <ol className="cq-fu-events">
      {rows.map((e, i) => (
        <li key={`${e.time}-${i}`}>
          <time>{e.time}</time>
          <span className={`cq-cat ${KIND[e.kind].cls}`}>{KIND[e.kind].text}</span>
          <div className="cq-fu-what">
            {e.symbol && (
              <span className="cq-fu-sleeve long">
                {e.name} {e.symbol}
              </span>
            )}
            <span className="cq-fu-text">{e.text}</span>
          </div>
        </li>
      ))}
    </ol>
  )
}

function Trades({ trades }: { trades: DayTrade[] }) {
  if (trades.length === 0) return <p className="cq-empty">還沒有賣掉的交易。</p>
  return (
    <table className="cq-fu-trades">
      <thead>
        <tr>
          <th>股票</th>
          <th>買進</th>
          <th>賣出</th>
          <th>原因</th>
          <th>淨損益</th>
        </tr>
      </thead>
      <tbody>
        {trades.map((t, i) => (
          <tr key={i}>
            <td>
              {t.name} {t.symbol}
              <br />
              <small>
                {t.quantity} 張・{t.setup}
              </small>
            </td>
            <td>
              {t.entry_time}
              <br />
              {price(t.entry_price)}
            </td>
            <td>
              {t.exit_time}
              <br />
              {price(t.exit_price)}
            </td>
            <td>{EXIT_TEXT[t.exit_reason] ?? t.exit_reason}</td>
            <td className={tone(t.net_pnl)}>
              {yuan(t.net_pnl)}
              <br />
              <small>{pct(t.net_ret_pct)}</small>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Rules({ d }: { d: DayTradeDay }) {
  const c = d.config
  return (
    <Section icon={Settings2} title="帳戶與規則" fold summary={`資金 ${Math.round(c.capital / 10_000)} 萬・最多 ${c.trading.max_position_count ?? '—'} 檔`}>
      <ul className="cq-dt-config">
        <li>
          <span>起始資金</span>
          <b>{c.capital.toLocaleString('en-US')} 元</b>
        </li>
        <li>
          <span>盯盤檔數（榜單前幾名）</span>
          <b>{c.watch_count}</b>
        </li>
        <li>
          <span>手續費折數／最低</span>
          <b>
            {c.fee_discount * 10} 折／{c.min_fee} 元
          </b>
        </li>
        {Object.entries(c.trading).map(([k, v]) => (
          <li key={k}>
            <span>{CONFIG_TEXT[k] ?? k}</span>
            <b>{typeof v === 'number' ? v.toLocaleString('en-US') : String(v)}</b>
          </li>
        ))}
      </ul>
      <p className="cq-fu-foot">
        選股和進出場是網友原版 Momentum_Scalping，程式與參數不改：每 30 秒掃一次全市場普通股，今天在漲、量放大、比大盤和同類股強、貼著當日高點、買賣價差小的打分數，70 分以上照分數取前 {c.watch_count} 檔，
        只有這些股票看逐筆；動能或趨勢突破才買，跌破前低、EMA 死叉、動能衰退就賣，12:00 後不再進場、13:00 全部賣掉。只做多、只記帳，沒有送任何委託。
      </p>
      <p className="cq-fu-foot">
        上面的數字是網友預設。要改的話，改你電腦上的 <code>data/research_ms/stock_live/account.json</code>，下次啟動錄製程式時生效。成本：手續費 0.1425% 打 {c.fee_discount * 10} 折（最低 {c.min_fee} 元）、當沖證交稅 0.15%、買在賣價賣在買價。
        富邦行情只供本人使用，不對外顯示。
      </p>
    </Section>
  )
}

/** 復盤的小圖：那一檔的 1 分 K 收盤線（或秒級），買賣點標在成交那一刻。 */
function MiniChart({ h, d, symbol, cut }: { h: DayTradeHistory; d: DayTradeDay; symbol: string; cut: string }) {
  const box = useRef<HTMLDivElement>(null)
  const chart = h.charts[symbol]
  useEffect(() => {
    if (!box.current || !chart) return
    const c = createChart(box.current, {
      height: 220,
      layout: { background: { color: 'transparent' }, textColor: '#9aa0ac' },
      grid: { vertLines: { color: 'rgba(255,255,255,.04)' }, horzLines: { color: 'rgba(255,255,255,.04)' } },
      timeScale: { timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderVisible: false },
    })
    const [y, mo, dd] = d.day.split('-').map(Number)
    const t = (hhmmss: string) => (Date.UTC(y, mo - 1, dd, 0, 0, 0) / 1000 + secOf(hhmmss)) as UTCTimestamp
    const line = c.addSeries(LineSeries, { color: '#e8eaed', lineWidth: 2, priceLineVisible: false })
    const vw = c.addSeries(LineSeries, { color: '#f0b90b', lineWidth: 1, lineStyle: LineStyle.Dotted, priceLineVisible: false })
    const bars = chart.bars.filter((b) => b[0] < cut.slice(0, 5))
    line.setData(bars.map((b) => ({ time: t(`${b[0]}:00`), value: b[4] })))
    vw.setData(bars.map((b) => ({ time: t(`${b[0]}:00`), value: b[5] })))
    const marks: SeriesMarker<Time>[] = []
    for (const tr of d.trades.filter((x) => x.symbol === symbol)) {
      if (tr.entry_time <= cut)
        marks.push({ time: t(`${tr.entry_time.slice(0, 5)}:00`), position: 'belowBar', shape: 'arrowUp', color: '#ef4444', text: `買 ${price(tr.entry_price)}` })
      if (tr.exit_time <= cut) marks.push({ time: t(`${tr.exit_time.slice(0, 5)}:00`), position: 'aboveBar', shape: 'circle', color: '#9aa0ac', text: `賣 ${price(tr.exit_price)}` })
    }
    marks.sort((a, b) => Number(a.time) - Number(b.time))
    createSeriesMarkers(line, marks)
    c.timeScale().fitContent()
    return () => c.remove()
  }, [chart, d, symbol, cut])
  if (!chart) return <p className="cq-empty">這檔沒有圖。</p>
  return <div className="cq-dt-chart" ref={box} />
}

function LiveView({ refresh }: { refresh: number }) {
  const { data, err } = useLiveDay(true, refresh)
  if (err) return <p className="cq-empty">{err}</p>
  if (!data) return <p className="cq-empty">讀取中…</p>
  const a = data.account
  return (
    <>
      <MarketBadges d={data} />
      <div className="cq-tiles cq-fu-tiles">
        <Kpi label="帳戶合計" value={yuan(a.total)} tone={tone(a.total)} sub={`已賣 ${a.closed} 筆・今天進場 ${a.entries} 次`} />
        <Kpi label="已實現" value={yuan(a.realized)} tone={tone(a.realized)} sub="賣掉的（扣手續費、證交稅）" />
        <Kpi label="持股未實現" value={yuan(a.open_pnl)} tone={tone(a.open_pnl)} sub={`${data.positions.length} 檔・現在賣的話`} />
        <Kpi label="榜上" value={`${data.board.length} 檔`} sub={`進榜 ${data.watch.in} 次・出榜 ${data.watch.out} 次`} />
      </div>
      <Section icon={Activity} title="強勢股榜" meta={data.board_time ? `${data.board_time} 掃描` : '開盤後才有'}>
        <Board d={data} />
      </Section>
      <Section icon={Wallet} title="持股" meta={`${data.positions.length} 檔`}>
        <Positions
          rows={data.positions.map((p) => ({ name: p.name, symbol: p.symbol, quantity: p.quantity, entry_price: p.entry_price, entry_time: p.entry_time, stop: p.stop, now: p.last, pnl: p.open_pnl }))}
        />
      </Section>
      <Section icon={ListOrdered} title="時間軸" meta={`${data.events_total} 件`}>
        <Timeline events={data.events} newestFirst />
      </Section>
      <Section icon={ReceiptText} title="成交明細" fold summary={`${data.trades.length} 筆・${yuan(data.trades.reduce((s, t) => s + t.net_pnl, 0))} 元`}>
        <Trades trades={data.trades} />
      </Section>
      <Rules d={data} />
    </>
  )
}

function ReplayView({ refresh }: { refresh: number }) {
  const [days, setDays] = useState<DayTradeRow[] | null>(null)
  const [pick, setPick] = useState<string | null>(null)
  const [d, setD] = useState<DayTradeDay | null>(null)
  const [h, setH] = useState<DayTradeHistory | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [pos, setPos] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [sym, setSym] = useState<string | null>(null)

  useEffect(() => {
    fetchDayTradeDays()
      .then(setDays)
      .catch((e) => setErr(errText(e)))
  }, [refresh])
  const day = pick ?? days?.[0]?.day ?? null
  useEffect(() => {
    if (day === null) return
    let stop = false
    setPlaying(false)
    Promise.all([fetchDayTrade(day), fetchDayTradeHistory(day).catch(() => null)])
      .then(([dd, hh]) => {
        if (stop) return
        setD(dd)
        setH(hh)
        setPos(Math.max(0, (hh?.board_history.length ?? 1) - 1))
        setSym(dd.trades[0]?.symbol ?? null)
        setErr(null)
      })
      .catch((e) => !stop && setErr(errText(e)))
    return () => {
      stop = true
    }
  }, [day])
  const times = useMemo(() => (h ? h.board_history.map((b) => b[0]) : []), [h])
  const last = Math.max(0, times.length - 1)
  useEffect(() => {
    if (!playing) return
    const id = setInterval(() => setPos((i) => (i >= last ? (setPlaying(false), i) : i + 1)), PLAY_MS)
    return () => clearInterval(id)
  }, [playing, last])

  if (err) return <p className="cq-empty">{err}</p>
  if (days !== null && days.length === 0) return <p className="cq-empty">還沒有當沖紀錄。</p>
  if (!d) return <p className="cq-empty">讀取中…</p>
  const cut = times.length ? times[Math.min(pos, last)] : '23:59:59'
  const view = replayAt(d, h, cut)
  const board = h ? boardAt(h, cut) : null
  const holdingPnl = view.holding.reduce((s, t) => s + (roughOpenPnl(t, t.price) ?? 0), 0)
  const traded = [...new Set(d.trades.map((t) => t.symbol))]
  return (
    <>
      <div className="cq-dt-days">
        {(days ?? []).slice(0, 20).map((r) => (
          <button key={r.day} className={r.day === day ? 'on' : ''} onClick={() => setPick(r.day)}>
            {r.day.slice(5)}
            <b className={tone(r.total)}>{r.total === null ? '—' : yuan(r.total)}</b>
          </button>
        ))}
      </div>
      <MarketBadges d={d} />
      <div className="cq-tiles cq-fu-tiles">
        <Kpi label="到這一刻" value={yuan(view.realized + holdingPnl)} tone={tone(view.realized + holdingPnl)} sub={`已賣 ${view.closed.length} 筆・${cut}`} />
        <Kpi label="已實現" value={yuan(view.realized)} tone={tone(view.realized)} sub="賣掉的（扣成本）" />
        <Kpi label="抱著" value={`${view.holding.length} 檔`} sub={view.holding.length ? `粗估 ${yuan(holdingPnl)}（未扣賣出成本）` : '空手'} />
        <Kpi label="全天" value={yuan(d.account.total)} tone={tone(d.account.total)} sub={`成交 ${d.account.closed} 筆`} />
      </div>
      {times.length > 0 ? (
        <div className="cq-dt-scrub">
          <button className="cq-fu-play" onClick={() => setPlaying((p) => !p)} aria-label={playing ? '暫停' : '播放'}>
            {playing ? <Pause size={18} /> : <Play size={18} />}
          </button>
          <input type="range" min={0} max={last} value={Math.min(pos, last)} onChange={(e) => (setPlaying(false), setPos(Number(e.target.value)))} aria-label="拉到哪一刻" />
          <b>{cut}</b>
        </div>
      ) : (
        <p className="cq-empty">這天還沒有復盤資料（收盤後才會有），下面是收盤的結果。</p>
      )}
      {h && traded.length > 0 && (
        <Section icon={Activity} title="有交易的股票" meta="買賣點標在成交那一分鐘">
          <div className="cq-seg cq-seg-small cq-dt-syms">
            {traded.map((s) => (
              <button key={s} className={s === sym ? 'on' : ''} onClick={() => setSym(s)}>
                {h.names[s] ?? s} {s}
              </button>
            ))}
          </div>
          {sym && <MiniChart h={h} d={d} symbol={sym} cut={cut} />}
        </Section>
      )}
      {board && (
        <Section icon={Layers} title="那一刻的強勢股榜" meta={board.time ? `${board.time} 掃描・${board.rows.length} 檔` : '開盤前'}>
          {board.rows.length === 0 ? (
            <p className="cq-empty">這一輪沒有股票過 70 分。</p>
          ) : (
            <ol className="cq-dt-board">
              {board.rows.map((r) => {
                const tag = view.holding.some((t) => t.symbol === r.symbol) ? 'holding' : view.closed.some((t) => t.symbol === r.symbol) ? 'traded' : null
                return (
                  <li key={r.symbol} className={tag ?? ''}>
                    <span className="cq-dt-rank">{r.rank}</span>
                    <div className="cq-dt-name">
                      <b>
                        {r.name} <small>{r.symbol}</small>
                      </b>
                      <span>分數 {num(r.score, 0)}</span>
                    </div>
                    <div className="cq-dt-right">
                      <b className={tone(r.change_pct)}>{pct(r.change_pct)}</b>
                      {tag && <em className={`cq-dt-tag ${tag}`}>{tag === 'holding' ? '持有中' : '做過'}</em>}
                    </div>
                  </li>
                )
              })}
            </ol>
          )}
        </Section>
      )}
      <Section icon={Wallet} title="那一刻的持股" meta={`${view.holding.length} 檔`}>
        <Positions
          rows={view.holding.map((t) => ({ name: t.name, symbol: t.symbol, quantity: t.quantity, entry_price: t.entry_price, entry_time: t.entry_time, stop: t.stop_price, now: t.price, pnl: roughOpenPnl(t, t.price) }))}
        />
      </Section>
      <Section icon={ListOrdered} title="時間軸" meta={`${view.events.length} 件`}>
        <Timeline events={view.events} newestFirst={false} />
      </Section>
      <Section icon={ReceiptText} title="成交明細" fold summary={`${view.closed.length} 筆・${yuan(view.realized)} 元`}>
        <Trades trades={view.closed} />
      </Section>
      <Rules d={d} />
    </>
  )
}

export function DayTradePage({ refresh }: { refresh: number }) {
  const [mode, setMode] = useState<Mode>('live')
  return (
    <main className="cq-body cq-fu cq-dt">
      <div className="cq-seg cq-fu-mode">
        <button className={mode === 'live' ? 'on' : ''} onClick={() => setMode('live')}>
          <Radio size={15} aria-hidden="true" /> 盤中
        </button>
        <button className={mode === 'replay' ? 'on' : ''} onClick={() => setMode('replay')}>
          <History size={15} aria-hidden="true" /> 復盤
        </button>
      </div>
      {mode === 'live' ? <LiveView refresh={refresh} /> : <ReplayView refresh={refresh} />}
      <p className="cq-fu-foot cq-dt-note">
        <BookOpen size={13} aria-hidden="true" /> 網友原版在台指期上的回測扣掉成本後沒有優勢；這頁的榜單是給你盤中看哪些股票正在強，模擬單是記帳對照，不是下單建議。
      </p>
    </main>
  )
}
