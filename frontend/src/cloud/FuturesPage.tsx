// 台指期分頁（T-450／T-451／D-220）：小台模擬單＋大台、微台行情；每個商品都有盤中與復盤。
// 使用者 2026-10-26：「這個可以在我網站即時模擬出來嗎？」「畫面可以是復盤模式跟盤中模式」「我想要秒級的復盤」
// 「買進賣出的點位要在復盤過程標出來」；10-27：「我也要微台跟大台的資料」「放到網站」。
// 三個商品都跑同一套固定組合「ORB 只做多 + 噪音區只放空」的模擬單（只記帳、不送委託；10-27「也要有模擬單」），成本照各自的合約。
// T-453（10-27「都整合在一起…再呈現各策略賺賠…用成日曆一次可以看完當天賺多少」）：不切換，各策略同一個畫面；復盤上面是每日損益日曆。
// T-460（10-29「把台指期的策略都以網友為主…其他策略都是輔助顯示」）：網友原版是主角；T-461（10-29「開高走低策略直接拿掉」）：
// 開高走低（T-452）與它和固定組合合成的帳戶（T-454）拿掉，只剩網友原版（主）與固定組合（輔助）。
// T-455（10-28「可以讓我在前端介面看到錄製的資料嗎」「做成可切換的」）：「盤口與大單」區塊——主動買賣、每分鐘淨主動、五檔、大單（門檻 5／10／20／30 口可切）；
// 展開才抓、盤中每 15 秒更新、盤中不抓每分鐘的五檔（2026-10-28 Supabase 流出超量，省流量）。
// 資料來自使用者電腦上的富邦即時行情（fubon_live.py），富邦行情不得轉傳 → 只給 admin。
import {
  Activity,
  ArrowUpDown,
  BookOpen,
  ChevronLeft,
  ChevronRight,
  History,
  Layers,
  ListOrdered,
  Pause,
  Play,
  Radio,
  ReceiptText,
  SkipBack,
  SkipForward,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
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
import { fetchFlow, fetchMsEstimate, fetchFuturesDay, fetchFuturesDays, fetchFuturesSeconds, fetchQuoteDay, fetchQuoteDays, fetchQuoteSeconds } from './api'
import { price } from './format'
import {
  BIG_THRESHOLDS,
  CAL_VIEWS,
  CAL_VIEW_NAME,
  DEFAULT_BIG_MIN,
  PRODUCT_NAME,
  REASON_TEXT,
  SLEEVES,
  SLEEVE_NAME,
  STRATEGY_NAME,
  STALE_AFTER_S,
  addSeconds,
  allFillTimes,
  comboBook,
  sideSummary,
  barsAt,
  bookTotals,
  change,
  chartMarks,
  chartTime,
  compactYuan,
  dayAt,
  dayPnl,
  decodeSeconds,
  flowAt,
  lastAtOrBefore,
  lots,
  monthSummary,
  monthWeeks,
  monthsOf,
  msKpis,
  msStopAt,
  msSub,
  costOf,
  isEstimate,
  isPaper,
  shouldPoll,
  staleSeconds,
  tone,
  yuan,
  type BigMin,
  type BookLevel,
  type CalView,
  type Flow,
  type OrderBook,
  type DayView,
  type FuturesDay,
  type FuturesDayRow,
  type FuturesEvent,
  type Fill,
  type FuturesTrade,
  type MsEstimate,
  type PnlRow,
  type Product,
  type QuoteDayRow,
  type QuotePayload,
  type QuoteProduct,
  type Seconds,
  type Sleeve,
  type SleeveState,
  type Strategy,
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
/** T-460：輔助策略（不是網友原版）的買賣點 */
const AUX = '#5C6270'

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
  /** 水平線：固定組合的 ORB 停損（進場後才畫）、網友原版持倉的停損 */
  levels: { price: number; title: string }[]
  /** 買賣點（有模擬單才有） */
  fills: readonly Fill[]
  /** T-460：主策略（網友原版）；有給時其他策略的買賣點畫灰色 */
  primary?: Sleeve | null
}

function FuturesChart({ day, bars, secs, pos, levels, fills, primary = null }: ChartProps) {
  const box = useRef<HTMLDivElement>(null)
  const parts = useRef<{
    chart: IChartApi
    px: ISeriesApi<'Line'>
    vw: ISeriesApi<'Line'>
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
      // 圖幾乎佔滿畫面寬：滾輪與上下滑要留給頁面捲動，否則游標停在圖上就捲不下去
      handleScroll: { mouseWheel: false, vertTouchDrag: false },
      handleScale: { mouseWheel: false },
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
    parts.current = { chart, px, vw, frame, marks: createSeriesMarkers(px, []), lines: [] }
    return () => {
      chart.remove()
      parts.current = null
    }
  }, [day, secs])

  useEffect(() => {
    const p = parts.current
    if (!p) return
    const t = (time: string) => chartTime(day, time) as UTCTimestamp
    if (secs && secTimes) {
      // 秒級：拉到的那一秒以前畫線，後面留空白（圖的寬度固定）
      const n = pos + 1
      p.px.setData(secTimes.map((time, i) => (i < n ? { time, value: secs.price[i] } : { time })))
      p.vw.setData(secTimes.slice(0, n).map((time, i) => ({ time, value: secs.vwap[i] })))
    } else {
      const shown = new Map(bars.map((b) => [b[0], b]))
      p.px.setData(SESSION.map((m) => (shown.has(m) ? { time: t(m), value: shown.get(m)![4] } : { time: t(m) })))
      p.vw.setData(bars.map((b) => ({ time: t(b[0]), value: b[5] })))
    }
    const anchor = secs && secs.price.length ? secs.price[0] : bars.length ? bars[0][1] : null
    p.frame.setData(anchor === null ? [] : [{ time: t(SESSION[0]), value: anchor }, { time: t(SESSION[SESSION.length - 1]), value: anchor }])
    for (const l of p.lines) p.px.removePriceLine(l)
    p.lines = []
    for (const l of levels)
      p.lines.push(p.px.createPriceLine({ price: l.price, color: MUTE, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: l.title }))
    // 買賣點：成交的那一秒（1 分 K 時是那一分鐘）、成交價的位置；同一刻同價的合成一個
    const marks: SeriesMarker<Time>[] = chartMarks(fills, secs !== null, primary).map((m) => {
      const long = m.side > 0
      const time = t(m.time)
      if (m.kind === 'entry')
        return { time, position: 'atPriceBottom', price: m.price, shape: long ? 'arrowUp' : 'arrowDown', color: m.aux ? AUX : long ? UP : DOWN, text: m.text }
      return { time, position: 'atPriceTop', price: m.price, shape: 'circle', color: m.aux ? AUX : MUTE, text: m.text }
    })
    p.marks.setMarkers(marks)
  }, [day, bars, secs, secTimes, pos, levels, fills, primary])

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
  // T-460：以網友原版為主 → 收盤重算一致與否看它的；沒有網友原版的日子照舊看固定組合
  const ms = 'ms' in data ? (data as FuturesDay).ms : null
  const match = ms ? ms.official_match : data.official_match
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
      {match != null && (
        <span className={`cq-cat ${match ? 'cq-cat-mute' : 'cq-fu-warn'}`}>{match ? '收盤重算一致' : '收盤重算不一致，以重算為準'}</span>
      )}
    </div>
  )
}

/** 提示列：拉到的那一刻最近發生的事；買賣點剛發生（盤中 60 秒內）就亮起來。 */
function NowBar({ view }: { view: { events: (FuturesEvent & { strategy?: Strategy })[]; cut: string } }) {
  const last = view.events[view.events.length - 1]
  if (!last) return <div className="cq-fu-now">08:50 開盤 5 分鐘收完才有第一個訊號</div>
  const trade = last.kind === 'fill' || last.kind === 'exit'
  const fresh = trade && view.cut.length > 5 && secOf(view.cut) - secOf(last.time) <= FRESH_S
  const side = last.sleeve === 'ORB_LONG' || last.sleeve === 'MS_LONG' ? 'long' : last.sleeve ? 'short' : ''
  return (
    <div className={`cq-fu-now${fresh ? ` fresh ${side}` : ''}`}>
      <time>{last.time}</time>
      <span className={`cq-cat ${KIND[last.kind].cls}`}>{KIND[last.kind].text}</span>
      <span>
        {last.strategy === 'ms' && <span className="cq-cat cq-fu-tag-ms">{STRATEGY_NAME.ms}</span>}
        {last.text}
      </span>
    </div>
  )
}

/** 時間軸上的標籤（T-464 做成 badge）：動能順勢標策略名；晨光突破標子帳戶名。 */
function eventChip(e: FuturesEvent & { strategy?: Strategy }): { text: string; cls: string } | null {
  if (e.strategy === 'ms') return { text: STRATEGY_NAME.ms, cls: 'ms' }
  if (e.sleeve) return { text: SLEEVE_NAME[e.sleeve], cls: e.sleeve === 'ORB_LONG' ? 'long' : 'short' }
  return null
}

function Timeline({ events, newestFirst }: { events: (FuturesEvent & { strategy?: Strategy })[]; newestFirst: boolean }) {
  const rows = newestFirst ? [...events].reverse() : events
  if (rows.length === 0) return <p className="cq-empty">還沒有事件：開盤後才有。</p>
  return (
    <ol className="cq-fu-events">
      {rows.map((e, i) => {
        const chip = eventChip(e)
        return (
          <li key={`${e.time}-${i}`}>
            <time>{e.time}</time>
            <span className={`cq-cat ${KIND[e.kind].cls}`}>{KIND[e.kind].text}</span>
            <div className="cq-fu-what">
              {chip && <span className={`cq-cat cq-fu-tag-${chip.cls}`}>{chip.text}</span>}
              <span className="cq-fu-text">{e.text}</span>
            </div>
          </li>
        )
      })}
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

/** 每日損益日曆（T-453）：一個月一張、週一到週五；格子是那天的損益（網友原版／固定組合可切，T-460 起預設看網友原版），點一下打開那天的復盤。 */
function PnlCalendar({ days, value, onPick }: { days: PnlRow[]; value: string | null; onPick: (d: string) => void }) {
  const months = useMemo(() => monthsOf(days), [days])
  const byDay = useMemo(() => new Map(days.map((d) => [d.day, d])), [days])
  const [month, setMonth] = useState<string | null>(null)
  const [view, setView] = useState<CalView>('ms') // T-460：台指期以網友原版為主
  if (days.length === 0) return null
  const shown = month ?? (value ?? days[0].day).slice(0, 7)
  const i = months.indexOf(shown)
  const sum = monthSummary(days, shown, view)
  return (
    <div className="cq-fu-cal">
      <div className="cq-fu-cal-head">
        <button className="cq-fu-btn" disabled={i <= 0} onClick={() => setMonth(months[i - 1])} aria-label="上個月">
          <ChevronLeft size={16} />
        </button>
        <b>
          {Number(shown.slice(0, 4))} 年 {Number(shown.slice(5, 7))} 月
        </b>
        <button className="cq-fu-btn" disabled={i < 0 || i >= months.length - 1} onClick={() => setMonth(months[i + 1])} aria-label="下個月">
          <ChevronRight size={16} />
        </button>
        <b className={`cq-fu-cal-sum ${tone(sum.total)}`}>{yuan(sum.total)}</b>
      </div>
      <div className="cq-seg cq-seg-small cq-fu-cal-view">
        {CAL_VIEWS.map((k) => (
          <button key={k} className={view === k ? 'on' : ''} onClick={() => setView(k)}>
            {CAL_VIEW_NAME[k]}
          </button>
        ))}
      </div>
      <p className="cq-fu-cal-note">
        賺 {sum.win} 天・賠 {sum.loss} 天・不賺不賠 {sum.days - sum.win - sum.loss} 天
      </p>
      <div className="cq-fu-cal-grid">
        {['一', '二', '三', '四', '五'].map((w) => (
          <span key={w} className="cq-fu-cal-wd">
            {w}
          </span>
        ))}
        {monthWeeks(shown)
          .flat()
          .map((d, k) => {
            if (d === null) return <span key={`out-${k}`} className="cq-fu-cal-cell out" />
            const row = byDay.get(d)
            const v = row ? dayPnl(row, view) : null
            const t = v === null ? 'plain' : tone(Math.round(v))
            return (
              <button
                key={d}
                className={`cq-fu-cal-cell ${row ? t : 'none'}${d === value ? ' on' : ''}${isEstimate(row) ? ' est' : ''}`}
                disabled={!row}
                onClick={() => onPick(d)}
                aria-label={d}
              >
                <i>
                  {Number(d.slice(8))}
                  {isEstimate(row) && <em className="cq-fu-est">估</em>}
                </i>
                <b>{row ? (v === null ? '—' : compactYuan(v)) : ''}</b>
              </button>
            )
          })}
      </div>
    </div>
  )
}

/** 分頁在不在前景。省 Supabase 流量（2026-10-28 超量）：切到別的分頁、縮小視窗就停抓，切回來立刻補抓一次。 */
function usePageVisible(): boolean {
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden')
  useEffect(() => {
    const on = () => setVisible(document.visibilityState !== 'hidden')
    document.addEventListener('visibilitychange', on)
    return () => document.removeEventListener('visibilitychange', on)
  }, [])
  return visible
}

/** 盤中重抓：最新一天；台北 08:25～13:50、分頁在前景時每 15 秒。load 換了（換商品）要重抓，由呼叫端用 key 重建元件。 */
function useLive<T>(enabled: boolean, load: () => Promise<T>, refresh: number) {
  const visible = usePageVisible()
  const [data, setData] = useState<T | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [now, setNow] = useState(() => new Date())
  const loader = useRef(load)
  loader.current = load
  useEffect(() => {
    if (!enabled || !visible) return
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
  }, [enabled, visible, refresh])
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
  const estDay = mode === 'replay' && isEstimate(days?.find((r) => r.day === replayDay))
  const data = mode === 'live' ? live.data : estDay ? null : (replay?.d ?? null)
  const secs = mode === 'replay' ? (replay?.s ?? null) : null
  const minutes = useMemo(() => (data ? data.bars.map((b) => b[0]) : []), [data])
  const player = usePlayer(secs, minutes)
  const { setPlaying, setPos } = player

  useEffect(() => {
    if (mode !== 'replay' || replayDay === null || estDay) return
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
  }, [mode, replayDay, estDay, setPlaying, setPos])

  const error = err ?? (mode === 'live' ? live.err : null)

  return (
    <>
      {mode === 'replay' && days !== null && <PnlCalendar days={days} value={replayDay} onPick={setPick} />}
      {estDay && replayDay !== null && <EstimateDay product="MTX" day={replayDay} />}
      {!estDay && error && <p className="cq-empty">{error}</p>}
      {!estDay && data && <PaperBody data={data} secs={secs} player={player} mode={mode} now={live.now} refresh={refresh} />}
      {!estDay && !error && !data && <p className="cq-empty">讀取中…</p>}
    </>
  )
}

/** 只有網友原版 1 分 K 估算的日子（T-462）：合計、筆數、每筆成交；沒有秒級復盤。 */
function EstimateDay({ product, day }: { product: Product; day: string }) {
  const [est, setEst] = useState<MsEstimate | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let stop = false
    setEst(null)
    fetchMsEstimate(product, day)
      .then((e) => !stop && (setEst(e), setErr(null)))
      .catch((e) => !stop && setErr(errText(e)))
    return () => {
      stop = true
    }
  }, [product, day])
  if (err) return <p className="cq-empty">{err}</p>
  if (!est) return <p className="cq-empty">讀取中…</p>
  const wins = est.trades.filter((t) => t.net_pnl > 0).length
  return (
    <>
      <div className="cq-fu-badges">
        <b className="cq-fu-day">{est.day}</b>
        <span className="cq-cat cq-fu-warn">1 分 K 估算</span>
        <span className="cq-cat cq-cat-mute">{PRODUCT_NAME[product]}</span>
      </div>
      <div className="cq-tiles cq-fu-tiles">
        <Kpi label="動能順勢合計" value={yuan(est.total)} tone={tone(est.total)} sub="只做多 1 口・1 分 K 估算" />
        <Kpi label="筆數" value={`${est.n} 筆`} sub={est.n ? `賺 ${wins} 筆・賠 ${est.n - wins} 筆` : '這天沒有進場'} />
      </div>
      <Section icon={ReceiptText} title="成交明細（動能順勢・估算）" meta={`${est.n} 筆`}>
        {est.trades.length === 0 ? (
          <p className="cq-empty">這天動能順勢沒有進場。</p>
        ) : (
          <table className="cq-fu-trades">
            <thead>
              <tr>
                <th>買進</th>
                <th>賣出</th>
                <th>原因</th>
                <th>淨損益</th>
              </tr>
            </thead>
            <tbody>
              {est.trades.map((t, i) => (
                <tr key={i}>
                  <td>
                    {t.entry_time} {price(t.entry_price)}
                  </td>
                  <td>
                    {t.exit_time} {price(t.exit_price)}
                  </td>
                  <td>{REASON_TEXT[t.exit_reason] ?? t.exit_reason}</td>
                  <td className={tone(t.net_pnl)}>{yuan(t.net_pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
      <p className="cq-fu-foot">
        這天沒有逐筆資料，是用富邦的 1 分 K 還原逐筆、照動能順勢的規則跑出來的估算。1 分 K 看不到一分鐘裡面的走勢，動能順勢又常在幾秒內出場，
        所以估算偏悲觀：用 30 天真實逐筆對照，每筆大約少算 10 點。沒有秒級復盤與江波圖。
      </p>
    </>
  )
}

/** 口數的正負寫法：+1,234 口／-56 口／0 口。 */
const signedLots = (v: number): string => `${v > 0 ? '+' : v < 0 ? '-' : ''}${Math.abs(Math.round(v)).toLocaleString('en-US')} 口`

/** 每分鐘淨主動（買 − 賣）的柱狀圖，加一條累計線；時間軸同江波圖固定 08:45～13:45。 */
function DeltaChart({ day, minutes }: { day: string; minutes: [string, number][] }) {
  const box = useRef<HTMLDivElement>(null)
  const parts = useRef<{ chart: IChartApi; hist: ISeriesApi<'Histogram'>; cum: ISeriesApi<'Line'>; frame: ISeriesApi<'Line'> } | null>(null)
  useEffect(() => {
    if (!box.current) return
    const chart = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: MUTE, fontSize: 12 },
      grid: { vertLines: { color: '#1C1E25' }, horzLines: { color: '#1C1E25' } },
      crosshair: { mode: CrosshairMode.Normal },
      leftPriceScale: { visible: true, borderColor: '#23252D' },
      rightPriceScale: { borderColor: '#23252D' },
      timeScale: { borderColor: '#23252D', timeVisible: true, secondsVisible: false, fixLeftEdge: true, fixRightEdge: true, tickMarkFormatter: axisTime },
      localization: { timeFormatter: crossTime, priceFormatter: (v: number) => v.toFixed(0) },
      handleScroll: { mouseWheel: false, vertTouchDrag: false },
      handleScale: { mouseWheel: false },
    })
    const hist =chart.addSeries(HistogramSeries, { priceScaleId: 'right', priceLineVisible: false, lastValueVisible: false, priceFormat: { type: 'volume' } })
    const cum = chart.addSeries(LineSeries, { priceScaleId: 'left', color: VWAP, lineWidth: 2, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false })
    const frame = chart.addSeries(LineSeries, {
      color: 'rgba(0,0,0,0)',
      lineVisible: false,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      autoscaleInfoProvider: () => null,
    })
    parts.current = { chart, hist, cum, frame }
    return () => {
      chart.remove()
      parts.current = null
    }
  }, [day])
  useEffect(() => {
    const p = parts.current
    if (!p) return
    const t = (m: string) => chartTime(day, m) as UTCTimestamp
    let acc = 0
    p.hist.setData(minutes.map(([m, v]) => ({ time: t(m), value: v, color: v >= 0 ? UP : DOWN })))
    p.cum.setData(minutes.map(([m, v]) => ({ time: t(m), value: (acc += v) })))
    p.frame.setData([{ time: t(SESSION[0]), value: 0 }, { time: t(SESSION[SESSION.length - 1]), value: 0 }])
    p.chart.timeScale().fitContent()
  }, [day, minutes])
  return <div className="cq-fu-flow-chart" ref={box} />
}

/** 五檔：賣盤在上（價高→低）、買盤在下（價高→低），口數畫成橫條；中間一行買賣盤合計。 */
function BookTable({ book }: { book: OrderBook | null }) {
  if (!book) return <p className="cq-empty">還沒有五檔。</p>
  const maxLots = Math.max(1, ...book.bids.map((l) => l[1]), ...book.asks.map((l) => l[1]))
  const t = bookTotals(book)
  const row = (l: BookLevel, cls: 'ask' | 'bid') => (
    <li key={`${cls}${l[0]}`} className={cls}>
      <i style={{ width: `${(l[1] / maxLots) * 100}%` }} />
      <b>{price(l[0])}</b>
      <span>{l[1]}</span>
    </li>
  )
  return (
    <div className="cq-fu-book">
      <ul>{[...book.asks].reverse().map((l) => row(l, 'ask'))}</ul>
      <ul>
        <li className="mid">{t ? `買盤 ${t.bid} 口・賣盤 ${t.ask} 口・買盤佔 ${Math.round(t.bidShare * 100)}%` : '—'}</li>
      </ul>
      <ul>{book.bids.map((l) => row(l, 'bid'))}</ul>
    </div>
  )
}

const BIG_PAGE = 30

/** 盤口與大單（T-455）：展開才抓（省流量）；盤中每 15 秒更新、復盤照拉到的那一刻；大單門檻可切。 */
function FlowSection({ product, day, mode, cut, refresh }: { product: Product; day: string; mode: Mode; cut: string; refresh: number }) {
  const [open, setOpen] = useState(false)
  const [min, setMin] = useState<BigMin>(DEFAULT_BIG_MIN[product])
  const [flow, setFlow] = useState<Flow | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [more, setMore] = useState(false)
  const live = mode === 'live'
  const visible = usePageVisible()
  useEffect(() => {
    if (!open) return
    if (live && !visible) return
    let stop = false
    // 盤中：大單照門檻在伺服器先篩、不抓每分鐘的五檔；復盤：抓整份一次，之後換門檻在這裡篩
    const run = () =>
      fetchFlow(product, live ? null : day, live ? min : BIG_THRESHOLDS[0], live)
        .then((f) => {
          if (stop) return
          setFlow(f)
          setErr(null)
        })
        .catch((e) => !stop && setErr(errText(e)))
    run()
    if (!live)
      return () => {
        stop = true
      }
    const id = setInterval(() => shouldPoll(new Date(), null) && run(), POLL_MS)
    return () => {
      stop = true
      clearInterval(id)
    }
  }, [open, live, visible, product, day, min, refresh])
  const v = useMemo(() => (flow && flow.day === day ? flowAt(flow, cut, min) : null), [flow, day, cut, min])
  const bigs = v ? [...v.big].reverse() : []
  const shown = more ? bigs : bigs.slice(0, BIG_PAGE)
  const summary = v ? `主動買 ${lots(v.buy)}・賣 ${lots(v.sell)}・大單 ${v.big.length} 筆` : live ? '展開才載入・盤中每 15 秒更新' : '展開才載入'
  return (
    <Section icon={ArrowUpDown} title="盤口與大單" fold summary={summary} onToggle={setOpen}>
      <div className="cq-fu-flow">
        <div className="cq-fu-flow-head">
          <span className="cq-seg-label">大單門檻</span>
          <div className="cq-seg cq-seg-small">
            {BIG_THRESHOLDS.map((k) => (
              <button
                key={k}
                className={min === k ? 'on' : ''}
                onClick={() => {
                  setMin(k)
                  setMore(false)
                }}
              >
                {k} 口
              </button>
            ))}
          </div>
          <span className="cq-fu-cal-note">{live ? '盤中每 15 秒更新' : `到 ${cut}`}</span>
        </div>
        {err && <p className="cq-empty">{err}</p>}
        {!err && open && !v && <p className="cq-empty">讀取中…</p>}
        {v && (
          <>
            <div className="cq-tiles cq-fu-tiles cq-fu-tiles-3">
              <Kpi label="主動買" value={lots(v.buy)} tone="up" sub="成交在賣價＝買方追價" />
              <Kpi label="主動賣" value={lots(v.sell)} tone="down" sub="成交在買價＝賣方追價" />
              <Kpi label="買減賣" value={signedLots(v.delta)} tone={tone(v.delta)} sub={v.delta > 0 ? '買方比較急' : v.delta < 0 ? '賣方比較急' : '打平'} />
            </div>
            <DeltaChart day={day} minutes={v.minutes} />
            <div className="cq-fu-flow-cols">
              <div>
                <h4>
                  五檔 <span>{v.book ? v.book.time : '—'}</span>
                </h4>
                <BookTable book={v.book} />
              </div>
              <div>
                <h4>
                  大單 ≥{min} 口 <span>{v.big.length} 筆</span>
                </h4>
                {bigs.length === 0 ? (
                  <p className="cq-empty">還沒有 {min} 口以上的成交。</p>
                ) : (
                  <table className="cq-fu-bigs">
                    <thead>
                      <tr>
                        <th>時間</th>
                        <th>價</th>
                        <th>口</th>
                        <th>誰主動</th>
                      </tr>
                    </thead>
                    <tbody>
                      {shown.map((b, i) => (
                        <tr key={`${b[0]}-${i}`}>
                          <td>{b[0]}</td>
                          <td>{price(b[1])}</td>
                          <td className="lots">{b[2]}</td>
                          <td className={b[3] > 0 ? 'up' : b[3] < 0 ? 'down' : ''}>{b[3] > 0 ? '買方' : b[3] < 0 ? '賣方' : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {!more && bigs.length > BIG_PAGE && (
                  <button className="cq-fu-more" onClick={() => setMore(true)}>
                    再看 {bigs.length - BIG_PAGE} 筆
                  </button>
                )}
              </div>
            </div>
            <p className="cq-fu-foot">
              主動買＝成交在當時的賣價（買方追價），主動賣＝成交在買價；開盤集合競價那一筆不算。柱子是每分鐘買減賣，黃線是從開盤累計。
              五檔{live ? '是最新一份' : '是拉到那一分鐘之前最後一份'}。大單是一筆成交幾口以上，門檻自己切（小台、大台一筆 10 口約前 1%，微台 20 口）。
              只是顯示，不影響模擬單。只有 2026-10-28 之後富邦即時錄到的日子才有。
            </p>
          </>
        )}
      </div>
    </Section>
  )
}

type BodyProps = { data: FuturesDay; secs: Seconds | null; player: ReturnType<typeof usePlayer>; mode: Mode; now: Date; refresh: number }

/** 各策略、各子帳戶在那一刻：一列一個子帳戶（名字、現在在做什麼、損益）。 */
function StrategyBoard({ view }: { view: DayView }) {
  const row = (key: Sleeve, side: 'long' | 'short', st: SleeveState, sub: string) => {
    const v = st.realized + (st.open_pnl ?? 0)
    return (
      <li key={key}>
        <div>
          <b className={`cq-fu-sleeve ${side}`}>{SLEEVE_NAME[key]}</b>
          <span>{sub}</span>
        </div>
        <b className={`cq-fu-pnl ${tone(v)}`}>{yuan(v)}</b>
      </li>
    )
  }
  const head = (name: string, total: number | null) => (
    <h4>
      <span>{name}</span>
      <b className={`cq-fu-pnl ${total === null ? 'plain' : tone(total)}`}>{total === null ? '—' : yuan(total)}</b>
    </h4>
  )
  return (
    <div className="cq-fu-board">
      {head(`${STRATEGY_NAME.ms}（主）`, view.ms ? view.ms.total : null)}
      {view.ms ? (
        <ul>{row('MS_LONG', 'long', view.ms.sleeves.MS_LONG, msSub(view.ms))}</ul>
      ) : (
        <p className="cq-empty">這一天的資料沒有（10-28 晚上之後上傳的才有）</p>
      )}
      <p className="cq-fu-auxhead">以下是輔助對照</p>
      {head(STRATEGY_NAME.combo, view.combo.total)}
      <ul>
        {SLEEVES.map((s) => {
          const side = s === 'ORB_LONG' ? 1 : -1
          return row(s, side > 0 ? 'long' : 'short', view.combo.sleeves[s], sleeveSub(view.combo.sleeves[s], side))
        })}
      </ul>
    </div>
  )
}

/**
 * 模擬單的畫面（小台；大台、微台那一天有模擬單時也用它）。
 * T-460（使用者 2026-10-29：「把台指期的策略都以網友為主，金額賺賠也都以網友的算，其他策略都是輔助顯示」）：
 * 有網友原版的日子，數字卡、圖上彩色的買賣點、成交明細、日曆預設都是網友原版；固定組合是輔助（圖上灰色、收起的成交）。
 * 沒有網友原版的舊日子，數字卡改看固定組合。T-461 起沒有開高走低與帳戶。
 */
function PaperBody({ data, secs, player, mode, now, refresh }: BodyProps) {
  const cut = mode === 'replay' ? player.cut : '23:59'
  const view = useMemo(() => dayAt(data, cut, secs), [data, cut, secs])
  const stopShown = data.orb.stop !== null && data.orb.entry_time !== null && data.orb.entry_time.slice(0, cut.length) <= cut
  const msStop = data.ms && view.ms ? msStopAt(data.ms, view.ms) : null
  const levels = useMemo(() => {
    const orb = stopShown && data.orb.stop !== null ? [{ price: data.orb.stop, title: 'ORB 停損' }] : []
    const ms = msStop !== null ? [{ price: msStop, title: '動能 停損' }] : []
    return [...orb, ...ms]
  }, [stopShown, data, msStop])
  const jumps = useMemo(() => allFillTimes(data), [data])
  // 復盤拉到 cut：只列那之前已經平倉的交易（不劇透後面）
  const done = comboBook(data).trades
    .filter((t) => t.exit_time.slice(0, cut.length) <= cut)
    .sort((a, b) => (a.entry_time < b.entry_time ? -1 : a.entry_time > b.entry_time ? 1 : 0))
  const msDone = (data.ms?.trades ?? []).filter((t) => t.exit_time.slice(0, cut.length) <= cut)
  const long = sideSummary(view.combo, 1)
  const short = sideSummary(view.combo, -1)
  const cost = costOf(data)
  const name = PRODUCT_NAME[data.product ?? 'MTX']
  const ms = data.ms && view.ms ? view.ms : null
  const mk = ms ? msKpis(ms) : null
  return (
    <>
      <StatusBadges data={data} mode={mode} now={now} secs={secs} />
      {ms && mk ? (
        <div className="cq-tiles cq-fu-tiles">
          <Kpi
            label="動能順勢合計"
            value={yuan(ms.total)}
            tone={tone(ms.total)}
            sub={mode === 'replay' ? `只做多 1 口・到 ${cut}` : '只做多 1 口・已實現＋現在平倉的話'}
          />
          <Kpi label="持倉" value={mk.hold.value} tone={tone(mk.hold.tone)} sub={mk.hold.text} />
          <Kpi label="已平倉" value={`${mk.exits} 筆`} tone={tone(mk.realized)} sub={`已實現 ${yuan(mk.realized)}`} />
          <PriceKpi p={view.price} prevClose={data.prev_close} />
        </div>
      ) : (
        <div className="cq-tiles cq-fu-tiles">
          <Kpi
            label="晨光突破合計"
            value={yuan(view.combo.total)}
            tone={tone(view.combo.total)}
            sub={mode === 'replay' ? `這天沒有動能順勢・到 ${cut}` : '這天沒有動能順勢・已實現＋現在平倉的話'}
          />
          <Kpi label="多單" value={yuan(long.value)} tone={tone(long.value)} sub={long.text} />
          <Kpi label="空單" value={yuan(short.value)} tone={tone(short.value)} sub={short.text} />
          <PriceKpi p={view.price} prevClose={data.prev_close} />
        </div>
      )}
      <Section
        icon={Activity}
        title={`${name}江波圖`}
        meta={
          <span className="cq-fu-legend">
            <i style={{ background: PRICE }} />價 <i style={{ background: VWAP }} />VWAP
          </span>
        }
      >
        {mode === 'replay' && <NowBar view={view} />}
        <FuturesChart
          day={data.day}
          bars={view.bars}
          secs={secs}
          pos={player.pos}
          levels={levels}
          fills={view.fills}
          primary={ms ? 'MS_LONG' : null}
        />
        {mode === 'replay' && player.lastPos > 0 && <Scrubber player={player} secs={secs} jumpTimes={jumps} />}
      </Section>
      <FlowSection product={data.product ?? 'MTX'} day={data.day} mode={mode} cut={cut} refresh={refresh} />
      <Section icon={Layers} title="各策略" meta={ms ? '動能順勢為主・其他每條各 1 口的輔助對照' : '每條各 1 口的對照'}>
        <StrategyBoard view={view} />
      </Section>
      <Section icon={ListOrdered} title="時間軸" meta={`${view.events.length} 件`}>
        <Timeline events={view.events} newestFirst={mode === 'live'} />
      </Section>
      {ms && (
        <Section icon={ReceiptText} title="成交明細（動能順勢）" meta={`${msDone.length} 筆・${yuan(msDone.reduce((a, t) => a + t.net_pnl, 0))} 元`}>
          <Trades trades={msDone} />
        </Section>
      )}
      <Section
        icon={ReceiptText}
        title={ms ? '晨光突破成交（輔助）' : '成交明細（晨光突破）'}
        fold
        summary={`${done.length} 筆・${yuan(done.reduce((a, t) => a + t.net_pnl, 0))} 元`}
      >
        <Trades trades={done} />
      </Section>
      <Section icon={BookOpen} title="規則與成本" fold summary="只記帳、不下單">
        <p className="cq-fu-foot">
          <b>以動能順勢為主</b>：上面的數字、圖上彩色的買賣點、成交明細、日曆預設都是動能順勢（只做多 1 口）。
          晨光突破是輔助對照：圖上灰色的買賣點、「各策略」下半段、收起的「晨光突破成交」、日曆可切。還沒有動能順勢資料的舊日子，上面的數字看晨光突破。
        </p>
        <p className="cq-fu-foot">
          <b>晨光突破</b>（小台、大台、微台同一套）：開盤 5 分鐘收紅才做多，停損在那 5 分鐘低點、停利 10 倍。噪音區放空 2026-10-30 起不再顯示也不再記。
        </p>
        <p className="cq-fu-foot">
          <b>動能順勢</b>（PTT 網友分享的 Momentum_Scalping 原版）：程式與參數一個都不改，只做多 1 口；每一筆進場的時間軸都寫出為什麼進（均價線、兩條均線、一底比一底高、突破基準、量與振幅）；每秒看一次自己畫的 1 分 K，
          動能或趨勢突破才進場，停損放在結構低點，EMA 死叉、跌破前低、動能衰退就出場，13:00 清倉。指數期貨沒有「比大盤強」可比，這條固定當作通過；大盤濾網全開。
          它和晨光突破各算各的。過去兩年多的一分 K 回測：微台每筆 −11.8 點、小台九年每年都是虧的，但一分 K 對它偏悲觀（每筆少算約 10 點），加回去大約不賺不賠，只能當參考。
        </p>
        <p className="cq-fu-foot">
          13:44 全部平倉；{name}每點 {cost.point_value} 元、每邊手續費 {cost.commission} 元
          {data.product && data.product !== 'MTX' ? '（假設）' : ''}、滑價 1 點、期交稅十萬分之二。只記帳、沒有送任何委託。
          秒級復盤只有逐筆的日子才有（富邦即時錄到的、期交所近 30 個交易日）；更早只有 1 分 K。富邦行情只供本人使用，不對外顯示。
        </p>
      </Section>
    </>
  )
}

/** 大台、微台：那一天有模擬單就跟小台同一個畫面，前面不足 14 天就只有行情。換商品時由呼叫端用 key 整個重建。 */
function QuoteView({ product, mode, refresh }: { product: QuoteProduct; mode: Mode; refresh: number }) {
  const [days, setDays] = useState<QuoteDayRow[] | null>(null)
  const [pick, setPick] = useState<string | null>(null)
  const [replay, setReplay] = useState<{ q: QuotePayload; s: Seconds | null } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const live = useLive(mode === 'live', () => fetchQuoteDay(product), refresh)

  useEffect(() => {
    fetchQuoteDays(product)
      .then(setDays)
      .catch((e) => setErr(errText(e)))
  }, [product, refresh])

  const replayDay = pick ?? days?.[0]?.day ?? null
  const estDay = mode === 'replay' && isEstimate(days?.find((r) => r.day === replayDay))
  const data = mode === 'live' ? live.data : estDay ? null : (replay?.q ?? null)
  const secs = mode === 'replay' ? (replay?.s ?? null) : null
  const minutes = useMemo(() => (data ? data.bars.map((b) => b[0]) : []), [data])
  const player = usePlayer(secs, minutes)
  const { setPlaying, setPos } = player

  useEffect(() => {
    if (mode !== 'replay' || replayDay === null || estDay) return
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
  }, [mode, replayDay, estDay, product, setPlaying, setPos])

  const cut = mode === 'replay' && data ? player.cut : '23:59'
  const at = useMemo(() => (data ? barsAt(data.bars, cut, secs) : null), [data, cut, secs])
  const error = err ?? (mode === 'live' ? live.err : null)
  const high = at && at.bars.length ? Math.max(...at.bars.map((b) => b[2])) : null
  const low = at && at.bars.length ? Math.min(...at.bars.map((b) => b[3])) : null
  const vsPrev = (v: number | null) => tone(v === null || data === null || data.prev_close === null ? null : v - data.prev_close)

  return (
    <>
      {mode === 'replay' && days !== null && <PnlCalendar days={days} value={replayDay} onPick={setPick} />}
      {estDay && replayDay !== null && <EstimateDay product={product} day={replayDay} />}
      {!estDay && error && <p className="cq-empty">{error}</p>}
      {data && isPaper(data) && <PaperBody data={data} secs={secs} player={player} mode={mode} now={live.now} refresh={refresh} />}
      {data && !isPaper(data) && at && (
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
            <FuturesChart day={data.day} bars={at.bars} secs={secs} pos={player.pos} levels={NO_LEVELS} fills={NO_FILLS} />
            {mode === 'replay' && player.lastPos > 0 && <Scrubber player={player} secs={secs} jumpTimes={null} />}
          </Section>
          <FlowSection product={product} day={data.day} mode={mode} cut={cut} refresh={refresh} />
          <p className="cq-fu-foot">
            這一天前面沒有逐筆（算不出昨收），只有行情、沒有模擬單。秒級復盤只有逐筆的日子才有。富邦行情只供本人使用，不對外顯示。
          </p>
        </>
      )}
      {!estDay && !error && !data && <p className="cq-empty">讀取中…</p>}
    </>
  )
}

/** 大台、微台的圖沒有噪音區與買賣點：用固定的空陣列，免得每次重畫都當成新資料。 */
const NO_FILLS: readonly Fill[] = []
const NO_LEVELS: { price: number; title: string }[] = []

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
