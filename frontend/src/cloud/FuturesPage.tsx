// 台指期分頁（T-450／T-451／D-220）：小台模擬單＋大台、微台行情；每個商品都有盤中與復盤。
// 使用者 2026-10-26：「這個可以在我網站即時模擬出來嗎？」「畫面可以是復盤模式跟盤中模式」「我想要秒級的復盤」
// 「買進賣出的點位要在復盤過程標出來」；10-27：「我也要微台跟大台的資料」「放到網站」。
// 小台：固定組合「ORB 只做多 + 噪音區只放空」的模擬單（只記帳、不送委託）。大台、微台：只有行情，不做模擬單。
// 資料來自使用者電腦上的富邦即時行情（fubon_live.py），富邦行情不得轉傳 → 只給 admin。
import { Activity, History, ListOrdered, Pause, Play, Radio, ReceiptText, SkipBack, SkipForward } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  LineType,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { fetchFuturesDay, fetchFuturesDays, fetchFuturesSeconds, fetchQuoteDay, fetchQuoteDays, fetchQuoteSeconds } from './api'
import { price } from './format'
import {
  PRODUCT_NAME,
  REASON_TEXT,
  SLEEVES,
  SLEEVE_NAME,
  STALE_AFTER_S,
  addSeconds,
  barsAt,
  change,
  chartTime,
  decodeSeconds,
  fillTimes,
  lastAtOrBefore,
  replayAt,
  shouldPoll,
  staleSeconds,
  tone,
  yuan,
  type FuturesDay,
  type FuturesDayRow,
  type FuturesEvent,
  type FuturesTrade,
  type Product,
  type QuoteDay,
  type QuoteDayRow,
  type QuoteProduct,
  type ReplayView,
  type Seconds,
  type SleeveState,
} from './futures'
import { Kpi, Section } from './Section'
import './futures.css'

type Mode = 'live' | 'replay'
/** 盤中多久重抓一次（電腦上的程式也是每 15 秒寫一次） */
const POLL_MS = 15_000
/** 播放的節拍：每 0.1 秒動一次 */
const TICK_MS = 100
/** 秒級播放倍速（盤中幾秒／真實 1 秒）；預設 60 倍＝一整天約 5 分鐘 */
const SPEEDS = [10, 60, 300] as const
type Speed = (typeof SPEEDS)[number]
/** 1 分 K 播放：每一拍走一分鐘（一整天約 30 秒） */
const MINUTE_STEP = 1
/** 剛發生的買賣點：盤中 60 秒內在提示列上亮起來 */
const FRESH_S = 60

const UP = '#F6465D'
const DOWN = '#0ECB81'
const PRICE = '#E8EAED'
const VWAP = '#F0B90B'
const MUTE = '#9AA0AC'

const KIND: Record<FuturesEvent['kind'], { text: string; cls: string }> = {
  signal: { text: '訊號', cls: 'cq-fu-k-signal' },
  fill: { text: '成交', cls: 'cq-fu-k-fill' },
  exit: { text: '平倉', cls: 'cq-fu-k-exit' },
  check: { text: '檢查', cls: 'cq-fu-k-check' },
  info: { text: '說明', cls: 'cq-fu-k-info' },
}

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))
const pad = (n: number) => String(n).padStart(2, '0')

/** 一整個交易時段 08:45～13:45，每分鐘一格：1 分 K 時圖的寬度固定，往後走時不會一直縮放。 */
function sessionMinutes(): string[] {
  const out: string[] = []
  for (let m = 8 * 60 + 45; m <= 13 * 60 + 45; m++) out.push(`${pad(Math.floor(m / 60))}:${pad(m % 60)}`)
  return out
}
const SESSION = sessionMinutes()

const axisTime = (t: Time) => {
  const d = new Date(Number(t) * 1000)
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`
}
const crossTime = (t: Time) => {
  const d = new Date(Number(t) * 1000)
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`
}

/** 盤中時間差幾秒（同一天）。 */
const secOf = (t: string) => Number(t.slice(0, 2)) * 3600 + Number(t.slice(3, 5)) * 60 + Number(t.slice(6, 8) || 0)

type ChartProps = {
  day: string
  /** 拉到的那一刻以前的 1 分 K（1 分 K 模式畫這個） */
  bars: FuturesDay['bars']
  secs: Seconds | null
  /** 秒級：拉到第幾點 */
  pos: number
  /** 噪音區下緣（小台）；大台、微台沒有 */
  noiseLb: [string, number][]
  /** ORB 停損線（小台、進場後才畫） */
  stopLine: number | null
  /** 買賣點（小台）；大台、微台沒有 */
  fills: ReplayView['fills']
}

function FuturesChart({ day, bars, secs, pos, noiseLb, stopLine, fills }: ChartProps) {
  const box = useRef<HTMLDivElement>(null)
  const parts = useRef<{
    chart: IChartApi
    px: ISeriesApi<'Line'>
    vw: ISeriesApi<'Line'>
    lb: ISeriesApi<'Line'>
    frame: ISeriesApi<'Line'>
    marks: ISeriesMarkersPluginApi<Time>
    lines: IPriceLine[]
  } | null>(null)
  // 秒級：每一點的圖表時間先算好（一天約 1～2 萬點），播放時只切片
  const secTimes = useMemo(() => (secs ? secs.label.map((l) => chartTime(day, l) as UTCTimestamp) : null), [secs, day])

  useEffect(() => {
    if (!box.current) return
    const chart = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: MUTE, fontSize: 12 },
      grid: { vertLines: { color: '#1C1E25' }, horzLines: { color: '#1C1E25' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#23252D' },
      timeScale: { borderColor: '#23252D', timeVisible: true, secondsVisible: false, fixLeftEdge: true, fixRightEdge: true, tickMarkFormatter: axisTime },
      localization: { timeFormatter: crossTime, priceFormatter: (v: number) => v.toFixed(0) },
    })
    const lb = chart.addSeries(LineSeries, {
      color: DOWN,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      lineType: LineType.WithSteps,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    })
    const vw = chart.addSeries(LineSeries, {
      color: VWAP,
      lineWidth: 1,
      lineStyle: LineStyle.Dotted,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    })
    // 看不見的框：08:45 與 13:45 各一點，把時間軸撐滿整個交易時段（大台、微台沒有噪音區那條線撐）；不影響價格刻度
    const frame = chart.addSeries(LineSeries, {
      color: 'rgba(0,0,0,0)',
      lineVisible: false,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      autoscaleInfoProvider: () => null,
    })
    const px = chart.addSeries(LineSeries, { color: PRICE, lineWidth: secs ? 1 : 2, priceLineVisible: false })
    parts.current = { chart, px, vw, lb, frame, marks: createSeriesMarkers(px, []), lines: [] }
    return () => {
      chart.remove()
      parts.current = null
    }
  }, [day, secs])

  useEffect(() => {
    const p = parts.current
    if (!p) return
    const t = (time: string) => chartTime(day, time) as UTCTimestamp
    let open = bars.length > 0
    if (secs && secTimes) {
      // 秒級：拉到的那一秒以前畫線，後面留空白（圖的寬度固定）
      const n = pos + 1
      p.px.setData(secTimes.map((time, i) => (i < n ? { time, value: secs.price[i] } : { time })))
      p.vw.setData(secTimes.slice(0, n).map((time, i) => ({ time, value: secs.vwap[i] })))
      open = n > 0 && secs.label.length > 0
    } else {
      const shown = new Map(bars.map((b) => [b[0], b]))
      p.px.setData(SESSION.map((m) => (shown.has(m) ? { time: t(m), value: shown.get(m)![4] } : { time: t(m) })))
      p.vw.setData(bars.map((b) => ({ time: t(b[0]), value: b[5] })))
    }
    // 噪音區下緣：開盤就算得出今天九個檢查點的值（昨收、開盤、前 14 天波動都已知），不是偷看
    p.lb.setData(open ? noiseLb.map(([m, v]) => ({ time: t(m), value: v })) : [])
    const anchor = secs && secs.price.length ? secs.price[0] : bars.length ? bars[0][1] : null
    p.frame.setData(anchor === null ? [] : [{ time: t(SESSION[0]), value: anchor }, { time: t(SESSION[SESSION.length - 1]), value: anchor }])
    for (const l of p.lines) p.px.removePriceLine(l)
    p.lines = []
    if (stopLine !== null)
      p.lines.push(p.px.createPriceLine({ price: stopLine, color: MUTE, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'ORB 停損' }))
    // 買賣點：成交的那一秒（1 分 K 時是那一分鐘）、成交價的位置
    const marks: SeriesMarker<Time>[] = fills.map((f) => {
      const long = f.side > 0
      const time = t(secs ? f.time.slice(0, 8) : f.time.slice(0, 5))
      if (f.kind === 'entry')
        return { time, position: 'atPriceBottom', price: f.price, shape: long ? 'arrowUp' : 'arrowDown', color: long ? UP : DOWN, text: `${long ? '買' : '空'} ${f.price.toFixed(0)}` }
      return { time, position: 'atPriceTop', price: f.price, shape: 'circle', color: MUTE, text: `${long ? '賣' : '補'} ${f.price.toFixed(0)}` }
    })
    p.marks.setMarkers(marks)
  }, [day, bars, secs, secTimes, pos, noiseLb, stopLine, fills])

  useEffect(() => {
    parts.current?.chart.timeScale().fitContent()
  }, [day, secs])

  return <div className="cq-fu-chart" ref={box} />
}

/** 復盤播放：位置、播放、倍速；秒級一秒一秒走（跳過沒成交的秒數），1 分 K 一分鐘一分鐘走。 */
function usePlayer(secs: Seconds | null, minutes: string[]) {
  const [pos, setPos] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState<Speed>(60)
  const labels = secs ? secs.label : minutes
  const lastPos = Math.max(0, labels.length - 1)
  useEffect(() => {
    if (!playing) return
    const id = setInterval(() => {
      setPos((i) => {
        if (i >= lastPos) {
          setPlaying(false)
          return i
        }
        if (!secs) return Math.min(lastPos, i + MINUTE_STEP)
        // 秒級：盤中時間往前推「倍速 × 0.1 秒」，跳到那之前最後一筆
        const j = lastAtOrBefore(secs.label, addSeconds(secs.label[i], (speed * TICK_MS) / 1000))
        return Math.max(i + 1, Math.min(lastPos, j))
      })
    }, TICK_MS)
    return () => clearInterval(id)
  }, [playing, secs, speed, lastPos])
  const cut = labels.length ? labels[Math.min(pos, lastPos)] : '23:59'
  /** 跳到某個時間（HH:MM 或 HH:MM:SS）以前最後一點 */
  const seek = (time: string) => {
    setPlaying(false)
    setPos(Math.max(0, lastAtOrBefore(labels, secs ? time : time.slice(0, 5))))
  }
  return { pos, setPos, playing, setPlaying, speed, setSpeed, lastPos, cut, seek, labels }
}

function Scrubber({
  player,
  secs,
  jumpTimes,
}: {
  player: ReturnType<typeof usePlayer>
  secs: Seconds | null
  /** 有給就顯示「上一個／下一個買賣點」（小台） */
  jumpTimes: string[] | null
}) {
  const { pos, setPos, playing, setPlaying, speed, setSpeed, lastPos, cut, seek } = player
  const jump = (dir: 1 | -1) => {
    if (!jumpTimes) return
    const times = jumpTimes.map((t) => (secs ? t : t.slice(0, 5)))
    const target = dir > 0 ? times.find((t) => t > cut) : [...times].reverse().find((t) => t < cut)
    if (target !== undefined) seek(target)
  }
  return (
    <div className="cq-fu-scrub">
      {jumpTimes && (
        <button className="cq-fu-btn" onClick={() => jump(-1)} aria-label="上一個買賣點" title="上一個買賣點">
          <SkipBack size={16} />
        </button>
      )}
      <button
        className="cq-fu-play"
        onClick={() => {
          if (!playing && pos >= lastPos) setPos(0)
          setPlaying(!playing)
        }}
        aria-label={playing ? '暫停' : '播放'}
      >
        {playing ? <Pause size={18} /> : <Play size={18} />}
      </button>
      {jumpTimes && (
        <button className="cq-fu-btn" onClick={() => jump(1)} aria-label="下一個買賣點" title="下一個買賣點">
          <SkipForward size={16} />
        </button>
      )}
      <input
        type="range"
        min={0}
        max={lastPos}
        value={Math.min(pos, lastPos)}
        onChange={(e) => {
          setPlaying(false)
          setPos(Number(e.target.value))
        }}
        aria-label="復盤時間"
      />
      <b>{cut}</b>
      {secs && (
        <div className="cq-seg cq-seg-small cq-fu-speed">
          {SPEEDS.map((s) => (
            <button key={s} className={speed === s ? 'on' : ''} onClick={() => setSpeed(s)}>
              {s}×
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

type Badged = {
  day: string
  source: 'fubon_live' | 'taifex_replay'
  symbol: string
  status: 'live' | 'closed'
  updated_at: string
  last_tick: string | null
  official_match?: boolean | null
}

function StatusBadges({ data, mode, now, secs }: { data: Badged; mode: Mode; now: Date; secs: Seconds | null }) {
  const stale = staleSeconds(data.updated_at, now)
  return (
    <div className="cq-fu-badges">
      <b className="cq-fu-day">{data.day}</b>
      <span className={`cq-cat ${data.source === 'fubon_live' ? 'cq-cat-quote' : 'cq-cat-mute'}`}>
        {data.source === 'fubon_live' ? '富邦即時' : '期交所重算'}
      </span>
      <span className="cq-cat cq-cat-mute">{data.symbol}</span>
      {mode === 'replay' && <span className="cq-cat cq-cat-view">{secs ? '秒級' : '1 分 K'}</span>}
      {data.status === 'live' ? (
        mode === 'live' && stale !== null && stale > STALE_AFTER_S ? (
          <span className="cq-cat cq-fu-warn">⚠ {Math.round(stale / 60)} 分鐘沒更新：電腦上的錄製程式可能斷了</span>
        ) : (
          <span className="cq-cat cq-cat-event">盤中・最新成交 {data.last_tick ?? '—'}</span>
        )
      ) : (
        <span className="cq-cat cq-cat-list">已收盤</span>
      )}
      {data.official_match != null && (
        <span className={`cq-cat ${data.official_match ? 'cq-cat-mute' : 'cq-fu-warn'}`}>
          {data.official_match ? '收盤重算一致' : '收盤重算不一致，以重算為準'}
        </span>
      )}
    </div>
  )
}

/** 提示列：拉到的那一刻最近發生的事；買賣點剛發生（盤中 60 秒內）就亮起來。 */
function NowBar({ view }: { view: ReplayView }) {
  const last = view.events[view.events.length - 1]
  if (!last) return <div className="cq-fu-now">08:50 開盤 5 分鐘收完才有第一個訊號</div>
  const trade = last.kind === 'fill' || last.kind === 'exit'
  const fresh = trade && view.cut.length > 5 && secOf(view.cut) - secOf(last.time) <= FRESH_S
  const side = last.sleeve === 'ORB_LONG' ? 'long' : last.sleeve === 'NOISE_SHORT' ? 'short' : ''
  return (
    <div className={`cq-fu-now${fresh ? ` fresh ${side}` : ''}`}>
      <time>{last.time}</time>
      <span className={`cq-cat ${KIND[last.kind].cls}`}>{KIND[last.kind].text}</span>
      <span>{last.text}</span>
    </div>
  )
}

function Timeline({ events, newestFirst }: { events: FuturesEvent[]; newestFirst: boolean }) {
  const rows = newestFirst ? [...events].reverse() : events
  if (rows.length === 0) return <p className="cq-empty">還沒有事件：08:50 開盤 5 分鐘收完才有第一個訊號。</p>
  return (
    <ol className="cq-fu-events">
      {rows.map((e, i) => (
        <li key={`${e.time}-${i}`}>
          <time>{e.time}</time>
          <span className={`cq-cat ${KIND[e.kind].cls}`}>{KIND[e.kind].text}</span>
          {e.sleeve && <span className={`cq-fu-sleeve ${e.sleeve === 'ORB_LONG' ? 'long' : 'short'}`}>{SLEEVE_NAME[e.sleeve]}</span>}
          <span className="cq-fu-text">{e.text}</span>
        </li>
      ))}
    </ol>
  )
}

function Trades({ trades }: { trades: FuturesTrade[] }) {
  if (trades.length === 0) return <p className="cq-empty">還沒有平倉的交易。</p>
  return (
    <table className="cq-fu-trades">
      <thead>
        <tr>
          <th>子帳戶</th>
          <th>進場</th>
          <th>出場</th>
          <th>原因</th>
          <th>淨損益</th>
        </tr>
      </thead>
      <tbody>
        {trades.map((t, i) => (
          <tr key={i}>
            <td className={t.side > 0 ? 'long' : 'short'}>{SLEEVE_NAME[t.sleeve]}</td>
            <td>
              {t.entry_time.slice(0, 8)} {price(t.entry_price)}
            </td>
            <td>
              {t.exit_time.slice(0, 8)} {price(t.exit_price)}
            </td>
            <td>{REASON_TEXT[t.exit_reason] ?? t.exit_reason}</td>
            <td className={tone(t.net_pnl)}>{yuan(t.net_pnl)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function sleeveSub(s: SleeveState, side: 1 | -1): string {
  if (s.position === 0) return s.realized ? `已平倉，這邊 ${yuan(s.realized)}` : '空手'
  const word = side > 0 ? '持多' : '持空'
  return `${word} 1 口 @ ${price(s.entry_price)}（現在平倉 ${yuan(s.open_pnl ?? 0)}）`
}

function PriceKpi({ p, prevClose }: { p: number | null; prevClose: number | null }) {
  const c = change(p, prevClose)
  return (
    <Kpi
      label="最新"
      value={price(p)}
      tone={tone(c ? c.pts : null)}
      sub={p === null ? '還沒開盤' : c ? `比昨收 ${yuan(c.pts)} 點（${(c.pct * 100).toFixed(2)}%）` : '沒有昨收'}
    />
  )
}

/** 交易日下拉：小台列合計損益，大台、微台只列日期。 */
function DayPick({ days, value, onPick }: { days: { day: string; source: string; total?: number | null }[]; value: string | null; onPick: (d: string) => void }) {
  if (days.length === 0) return null
  return (
    <label className="cq-fu-pick">
      <span>交易日</span>
      <select value={value ?? ''} onChange={(e) => onPick(e.target.value)}>
        {days.map((d) => (
          <option key={d.day} value={d.day}>
            {d.day}
            {d.total == null ? '' : `（${yuan(d.total)}）`}
            {d.source === 'fubon_live' ? '' : '・期交所重算'}
          </option>
        ))}
      </select>
    </label>
  )
}

/** 盤中重抓：最新一天；台北 08:25～13:50 每 15 秒。load 換了（換商品）要重抓，由呼叫端用 key 重建元件。 */
function useLive<T>(enabled: boolean, load: () => Promise<T>, refresh: number) {
  const [data, setData] = useState<T | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [now, setNow] = useState(() => new Date())
  const loader = useRef(load)
  loader.current = load
  useEffect(() => {
    if (!enabled) return
    let stop = false
    const run = () =>
      loader
        .current()
        .then((d) => {
          if (stop) return
          setData(d)
          setErr(null)
        })
        .catch((e) => !stop && setErr(errText(e)))
    run()
    const id = setInterval(() => {
      const t = new Date()
      setNow(t)
      if (shouldPoll(t, null)) run()
    }, POLL_MS)
    return () => {
      stop = true
      clearInterval(id)
    }
  }, [enabled, refresh])
  return { data, err, now }
}

/** 小台：模擬單（盤中＋復盤）。 */
function PaperView({ mode, refresh }: { mode: Mode; refresh: number }) {
  const [days, setDays] = useState<FuturesDayRow[] | null>(null)
  const [pick, setPick] = useState<string | null>(null)
  const [replay, setReplay] = useState<{ d: FuturesDay; s: Seconds | null } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const live = useLive(mode === 'live', () => fetchFuturesDay(), refresh)

  useEffect(() => {
    fetchFuturesDays()
      .then(setDays)
      .catch((e) => setErr(errText(e)))
  }, [refresh])

  const replayDay = pick ?? days?.[0]?.day ?? null
  const data = mode === 'live' ? live.data : (replay?.d ?? null)
  const secs = mode === 'replay' ? (replay?.s ?? null) : null
  const minutes = useMemo(() => (data ? data.bars.map((b) => b[0]) : []), [data])
  const player = usePlayer(secs, minutes)
  const { setPlaying, setPos } = player

  useEffect(() => {
    if (mode !== 'replay' || replayDay === null) return
    let stop = false
    setPlaying(false)
    Promise.all([fetchFuturesDay(replayDay), fetchFuturesSeconds(replayDay).then(decodeSeconds, () => null)])
      .then(([d, s]) => {
        if (stop) return
        setReplay({ d, s })
        setPos(Math.max(0, (s ? s.label.length : d.bars.length) - 1))
        setErr(null)
      })
      .catch((e) => !stop && setErr(errText(e)))
    return () => {
      stop = true
    }
  }, [mode, replayDay, setPlaying, setPos])

  const cut = mode === 'replay' && data ? player.cut : '23:59'
  const view = useMemo(() => (data ? replayAt(data, cut, secs) : null), [data, cut, secs])
  // 復盤拉到 cut：只列那之前已經平倉的交易（不劇透後面）
  const done = data ? data.trades.filter((t) => t.exit_time.slice(0, cut.length) <= cut) : []
  const error = err ?? (mode === 'live' ? live.err : null)

  return (
    <>
      {mode === 'replay' && days !== null && <DayPick days={days} value={replayDay} onPick={setPick} />}
      {error && <p className="cq-empty">{error}</p>}
      {data && view && (
        <>
          <StatusBadges data={data} mode={mode} now={live.now} secs={secs} />
          <div className="cq-tiles cq-fu-tiles">
            <Kpi label="今天合計" value={yuan(view.total)} tone={tone(view.total)} sub={mode === 'replay' ? `到 ${cut}` : '已實現＋現在平倉的話'} />
            {SLEEVES.map((s) => {
              const st = view.sleeves[s]
              const v = st.realized + (st.open_pnl ?? 0)
              return <Kpi key={s} label={SLEEVE_NAME[s]} value={yuan(v)} tone={tone(v)} sub={sleeveSub(st, s === 'ORB_LONG' ? 1 : -1)} />
            })}
            <PriceKpi p={view.price} prevClose={data.prev_close} />
          </div>
          <Section
            icon={Activity}
            title="江波圖"
            meta={
              <span className="cq-fu-legend">
                <i style={{ background: PRICE }} />價 <i style={{ background: VWAP }} />VWAP <i style={{ background: DOWN }} />噪音區下緣
              </span>
            }
          >
            {mode === 'replay' && <NowBar view={view} />}
            <FuturesChart
              day={data.day}
              bars={view.bars}
              secs={secs}
              pos={player.pos}
              noiseLb={data.noise_lb}
              stopLine={data.orb.stop !== null && data.orb.entry_time !== null && data.orb.entry_time.slice(0, cut.length) <= cut ? data.orb.stop : null}
              fills={view.fills}
            />
            {mode === 'replay' && player.lastPos > 0 && <Scrubber player={player} secs={secs} jumpTimes={fillTimes(data)} />}
          </Section>
          <Section icon={ListOrdered} title="時間軸" meta={`${view.events.length} 件`}>
            <Timeline events={view.events} newestFirst={mode === 'live'} />
          </Section>
          <Section icon={ReceiptText} title="成交明細" fold summary={`${done.length} 筆・${yuan(done.reduce((a, t) => a + t.net_pnl, 0))} 元`}>
            <Trades trades={done} />
          </Section>
          <p className="cq-fu-foot">
            只記帳、沒有送任何委託。規則 2026-10-26 凍結：開盤 5 分鐘收紅才做多（停損在那 5 分鐘低點、停利 10 倍）；
            噪音區每 30 分鐘檢查一次，跌破下緣又在 VWAP 之下才放空；各 1 口小台，13:44 全部平倉；每邊滑價 1 點、手續費 30 元、期交稅十萬分之二。
            秒級復盤只有逐筆的日子才有（富邦即時錄到的、期交所近 30 個交易日）；更早只有 1 分 K。富邦行情只供本人使用，不對外顯示。
          </p>
        </>
      )}
      {!error && !data && <p className="cq-empty">讀取中…</p>}
    </>
  )
}

/** 大台、微台：只有行情（盤中＋復盤），不做模擬單。換商品時由呼叫端用 key 整個重建。 */
function QuoteView({ product, mode, refresh }: { product: QuoteProduct; mode: Mode; refresh: number }) {
  const [days, setDays] = useState<QuoteDayRow[] | null>(null)
  const [pick, setPick] = useState<string | null>(null)
  const [replay, setReplay] = useState<{ q: QuoteDay; s: Seconds | null } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const live = useLive(mode === 'live', () => fetchQuoteDay(product), refresh)

  useEffect(() => {
    fetchQuoteDays(product)
      .then(setDays)
      .catch((e) => setErr(errText(e)))
  }, [product, refresh])

  const replayDay = pick ?? days?.[0]?.day ?? null
  const data = mode === 'live' ? live.data : (replay?.q ?? null)
  const secs = mode === 'replay' ? (replay?.s ?? null) : null
  const minutes = useMemo(() => (data ? data.bars.map((b) => b[0]) : []), [data])
  const player = usePlayer(secs, minutes)
  const { setPlaying, setPos } = player

  useEffect(() => {
    if (mode !== 'replay' || replayDay === null) return
    let stop = false
    setPlaying(false)
    Promise.all([fetchQuoteDay(product, replayDay), fetchQuoteSeconds(product, replayDay).then(decodeSeconds, () => null)])
      .then(([q, s]) => {
        if (stop) return
        setReplay({ q, s })
        setPos(Math.max(0, (s ? s.label.length : q.bars.length) - 1))
        setErr(null)
      })
      .catch((e) => !stop && setErr(errText(e)))
    return () => {
      stop = true
    }
  }, [mode, replayDay, product, setPlaying, setPos])

  const cut = mode === 'replay' && data ? player.cut : '23:59'
  const at = useMemo(() => (data ? barsAt(data.bars, cut, secs) : null), [data, cut, secs])
  const error = err ?? (mode === 'live' ? live.err : null)
  const high = at && at.bars.length ? Math.max(...at.bars.map((b) => b[2])) : null
  const low = at && at.bars.length ? Math.min(...at.bars.map((b) => b[3])) : null
  const vsPrev = (v: number | null) => tone(v === null || data === null || data.prev_close === null ? null : v - data.prev_close)

  return (
    <>
      {mode === 'replay' && days !== null && <DayPick days={days} value={replayDay} onPick={setPick} />}
      {error && <p className="cq-empty">{error}</p>}
      {data && at && (
        <>
          <StatusBadges data={data} mode={mode} now={live.now} secs={secs} />
          <div className="cq-tiles cq-fu-tiles">
            <PriceKpi p={at.price} prevClose={data.prev_close} />
            <Kpi label="開盤" value={price(data.open)} tone={vsPrev(data.open)} sub={data.prev_close === null ? '沒有昨收' : `昨收 ${price(data.prev_close)}`} />
            <Kpi label="最高" value={price(high)} tone={vsPrev(high)} sub={mode === 'replay' ? `到 ${cut}` : '今天到目前'} />
            <Kpi label="最低" value={price(low)} tone={vsPrev(low)} sub={mode === 'replay' ? `到 ${cut}` : '今天到目前'} />
          </div>
          <Section
            icon={Activity}
            title={`${PRODUCT_NAME[product]}江波圖`}
            meta={
              <span className="cq-fu-legend">
                <i style={{ background: PRICE }} />價 <i style={{ background: VWAP }} />VWAP
              </span>
            }
          >
            <FuturesChart day={data.day} bars={at.bars} secs={secs} pos={player.pos} noiseLb={NO_LB} stopLine={null} fills={NO_FILLS} />
            {mode === 'replay' && player.lastPos > 0 && <Scrubber player={player} secs={secs} jumpTimes={null} />}
          </Section>
          <p className="cq-fu-foot">
            {PRODUCT_NAME[product]}只有行情、不做模擬單（模擬單只跑小台）。秒級復盤只有逐筆的日子才有。富邦行情只供本人使用，不對外顯示。
          </p>
        </>
      )}
      {!error && !data && <p className="cq-empty">讀取中…</p>}
    </>
  )
}

/** 大台、微台的圖沒有噪音區與買賣點：用固定的空陣列，免得每次重畫都當成新資料。 */
const NO_LB: [string, number][] = []
const NO_FILLS: ReplayView['fills'] = []

const PRODUCTS: { key: Product; label: string }[] = [
  { key: 'MTX', label: '小台・模擬單' },
  { key: 'TX', label: '大台' },
  { key: 'TMF', label: '微台' },
]

export function FuturesPage({ refresh }: { refresh: number }) {
  const [product, setProduct] = useState<Product>('MTX')
  const [mode, setMode] = useState<Mode>('live')
  return (
    <main className="cq-body cq-fu">
      <div className="cq-seg cq-fu-product">
        {PRODUCTS.map((p) => (
          <button key={p.key} className={product === p.key ? 'on' : ''} onClick={() => setProduct(p.key)}>
            {p.label}
          </button>
        ))}
      </div>
      <div className="cq-seg cq-fu-mode">
        <button className={mode === 'live' ? 'on' : ''} onClick={() => setMode('live')}>
          <Radio size={15} aria-hidden="true" /> 盤中
        </button>
        <button className={mode === 'replay' ? 'on' : ''} onClick={() => setMode('replay')}>
          <History size={15} aria-hidden="true" /> 復盤
        </button>
      </div>
      {product === 'MTX' ? (
        <PaperView mode={mode} refresh={refresh} />
      ) : (
        <QuoteView key={product} product={product} mode={mode} refresh={refresh} />
      )}
    </main>
  )
}
