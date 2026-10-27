// 台指期分頁（T-450／T-451／D-220）：小台模擬單＋大台、微台行情；每個商品都有盤中與復盤。
// 使用者 2026-10-26：「這個可以在我網站即時模擬出來嗎？」「畫面可以是復盤模式跟盤中模式」「我想要秒級的復盤」
// 「買進賣出的點位要在復盤過程標出來」；10-27：「我也要微台跟大台的資料」「放到網站」。
// 三個商品都跑同一套固定組合「ORB 只做多 + 噪音區只放空」的模擬單（只記帳、不送委託；10-27「也要有模擬單」），成本照各自的合約。
// T-452（10-27「abc 也新增成一個策略加到前端」）：第二個策略「開高走低」＝A 開高失守、B 跌破開盤區間、C 補缺口，各 1 口只放空。
// T-453（10-27「都整合在一起…再呈現各策略賺賠…用成日曆一次可以看完當天賺多少」）：不切換，兩個策略同一個畫面；復盤上面是每日損益日曆。
// T-454（10-27「多空都持有一口就好」）：上面的數字、圖上買賣點、成交明細是實際帳戶（多空各最多 1 口，先到先做、同時要空 A 優先）；各策略與日曆可看每條各 1 口的對照。
// 資料來自使用者電腦上的富邦即時行情（fubon_live.py），富邦行情不得轉傳 → 只給 admin。
import {
  Activity,
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
  CAL_VIEWS,
  CAL_VIEW_NAME,
  GAP_SLEEVES,
  PRODUCT_NAME,
  REASON_TEXT,
  SLEEVES,
  SLEEVE_NAME,
  STRATEGY_NAME,
  STALE_AFTER_S,
  addSeconds,
  accountSide,
  allFillTimes,
  barsAt,
  change,
  chartMarks,
  chartTime,
  compactYuan,
  dayAt,
  dayPnl,
  decodeSeconds,
  gapLevels,
  gapSub,
  gapSummary,
  lastAtOrBefore,
  monthSummary,
  monthWeeks,
  monthsOf,
  costOf,
  isPaper,
  signedPct,
  shouldPoll,
  staleSeconds,
  tone,
  yuan,
  type CalView,
  type DayView,
  type FuturesDay,
  type FuturesDayRow,
  type FuturesEvent,
  type Fill,
  type FuturesTrade,
  type GapFail,
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
  /** 水平線：固定組合是 ORB 停損（進場後才畫）；開高走低是開盤、昨收、區間低與持倉的停損 */
  levels: { price: number; title: string }[]
  /** 買賣點（有模擬單才有） */
  fills: readonly Fill[]
}

function FuturesChart({ day, bars, secs, pos, noiseLb, levels, fills }: ChartProps) {
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
    for (const l of levels)
      p.lines.push(p.px.createPriceLine({ price: l.price, color: MUTE, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: l.title }))
    // 買賣點：成交的那一秒（1 分 K 時是那一分鐘）、成交價的位置；同一刻同價的合成一個（開高走低 A、B、C 常同時進場）
    const marks: SeriesMarker<Time>[] = chartMarks(fills, secs !== null).map((m) => {
      const long = m.side > 0
      const time = t(m.time)
      if (m.kind === 'entry')
        return { time, position: 'atPriceBottom', price: m.price, shape: long ? 'arrowUp' : 'arrowDown', color: long ? UP : DOWN, text: m.text }
      return { time, position: 'atPriceTop', price: m.price, shape: 'circle', color: MUTE, text: m.text }
    })
    p.marks.setMarkers(marks)
  }, [day, bars, secs, secTimes, pos, noiseLb, levels, fills])

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

function StatusBadges({ data, mode, now, secs, gap }: { data: Badged; mode: Mode; now: Date; secs: Seconds | null; gap?: GapFail }) {
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
      {gap && gap.active !== null && (
        <span className={`cq-cat ${gap.active ? 'cq-cat-event' : 'cq-cat-mute'}`}>
          {gap.active ? `開高 ${signedPct(gap.gap_pct)}` : `沒有開高 ${signedPct(gap.gap_pct)}`}
        </span>
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
function NowBar({ view }: { view: { events: FuturesEvent[]; cut: string } }) {
  const last = view.events[view.events.length - 1]
  if (!last) return <div className="cq-fu-now">08:50 開盤 5 分鐘收完才有第一個訊號</div>
  const trade = last.kind === 'fill' || last.kind === 'exit'
  const fresh = trade && view.cut.length > 5 && secOf(view.cut) - secOf(last.time) <= FRESH_S
  const side = last.sleeve === 'ORB_LONG' ? 'long' : last.sleeve ? 'short' : ''
  return (
    <div className={`cq-fu-now${fresh ? ` fresh ${side}` : ''}`}>
      <time>{last.time}</time>
      <span className={`cq-cat ${KIND[last.kind].cls}`}>{KIND[last.kind].text}</span>
      <span>{last.text}</span>
    </div>
  )
}

/** 時間軸上的小標：固定組合寫子帳戶；開高走低寫策略名（文字裡已經有 A、B、C）。 */
function eventChip(e: FuturesEvent & { strategy?: Strategy | 'account' }): { text: string; cls: string } | null {
  if (e.strategy === 'account') return { text: '帳戶', cls: 'acct' }
  if (e.strategy === 'gap') return { text: STRATEGY_NAME.gap, cls: 'gap' }
  if (e.sleeve) return { text: SLEEVE_NAME[e.sleeve], cls: e.sleeve === 'ORB_LONG' ? 'long' : 'short' }
  return null
}

function Timeline({ events, newestFirst }: { events: (FuturesEvent & { strategy?: Strategy | 'account' })[]; newestFirst: boolean }) {
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
              {chip && <span className={`cq-fu-sleeve ${chip.cls}`}>{chip.text}</span>}
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

/** 每日損益日曆（T-453）：一個月一張、週一到週五；格子是那天的損益（帳戶／固定組合／開高走低可切，T-454 起預設看帳戶），點一下打開那天的復盤。 */
function PnlCalendar({ days, value, onPick }: { days: PnlRow[]; value: string | null; onPick: (d: string) => void }) {
  const months = useMemo(() => monthsOf(days), [days])
  const byDay = useMemo(() => new Map(days.map((d) => [d.day, d])), [days])
  const [month, setMonth] = useState<string | null>(null)
  const [view, setView] = useState<CalView>('account')
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
              <button key={d} className={`cq-fu-cal-cell ${row ? t : 'none'}${d === value ? ' on' : ''}`} disabled={!row} onClick={() => onPick(d)} aria-label={d}>
                <i>{Number(d.slice(8))}</i>
                <b>{row ? (v === null ? '—' : compactYuan(v)) : ''}</b>
              </button>
            )
          })}
      </div>
    </div>
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

  const error = err ?? (mode === 'live' ? live.err : null)

  return (
    <>
      {mode === 'replay' && days !== null && <PnlCalendar days={days} value={replayDay} onPick={setPick} />}
      {error && <p className="cq-empty">{error}</p>}
      {data && <PaperBody data={data} secs={secs} player={player} mode={mode} now={live.now} />}
      {!error && !data && <p className="cq-empty">讀取中…</p>}
    </>
  )
}

type BodyProps = { data: FuturesDay; secs: Seconds | null; player: ReturnType<typeof usePlayer>; mode: Mode; now: Date }

/** 各策略、各子帳戶在那一刻：一列一個子帳戶（名字、現在在做什麼、損益）。 */
function StrategyBoard({ data, view, cut }: { data: FuturesDay; view: DayView; cut: string }) {
  const g = data.gap_fail ?? null
  const row = (key: Sleeve, side: 'long' | 'short', st: SleeveState, sub: string) => {
    const v = st.realized + (st.open_pnl ?? 0)
    const skipped = view.skipped.filter((l) => l.sleeve === key).length
    return (
      <li key={key}>
        <div>
          <b className={`cq-fu-sleeve ${side}`}>{SLEEVE_NAME[key]}</b>
          <span>{sub}</span>
          {skipped > 0 && <em className="cq-fu-skip">帳戶沒做{skipped > 1 ? ` ${skipped} 筆` : ''}：當時已經有{side === 'long' ? '多' : '空'}單</em>}
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
      {head(STRATEGY_NAME.combo, view.combo.total)}
      <ul>
        {SLEEVES.map((s) => {
          const side = s === 'ORB_LONG' ? 1 : -1
          return row(s, side > 0 ? 'long' : 'short', view.combo.sleeves[s], sleeveSub(view.combo.sleeves[s], side))
        })}
      </ul>
      {head(STRATEGY_NAME.gap, view.gap ? view.gap.total : null)}
      {g && view.gap ? (
        <ul>{GAP_SLEEVES.map((s) => row(s, 'short', view.gap!.sleeves[s], gapSub(data, g, s, view.gap!.sleeves[s], cut)))}</ul>
      ) : (
        <p className="cq-empty">{gapSummary(null, null)}</p>
      )}
    </div>
  )
}

/** 模擬單的畫面（小台；大台、微台那一天有模擬單時也用它）：上面是實際帳戶（數字卡、圖上買賣點、成交明細），各策略是每條各 1 口的對照。 */
function PaperBody({ data, secs, player, mode, now }: BodyProps) {
  const cut = mode === 'replay' ? player.cut : '23:59'
  const view = useMemo(() => dayAt(data, cut, secs), [data, cut, secs])
  const g = data.gap_fail ?? null
  const stopShown = data.orb.stop !== null && data.orb.entry_time !== null && data.orb.entry_time.slice(0, cut.length) <= cut
  const levels = useMemo(() => {
    const orb = stopShown && data.orb.stop !== null ? [{ price: data.orb.stop, title: 'ORB 停損' }] : []
    return g ? [...orb, ...gapLevels(data, g, view.account)] : orb
  }, [stopShown, data, g, view.account])
  const jumps = useMemo(() => allFillTimes(data), [data])
  // 復盤拉到 cut：只列那之前帳戶已經平倉的交易（不劇透後面）
  const done = view.accountTrades
    .filter((t) => t.exit_time.slice(0, cut.length) <= cut)
    .sort((a, b) => (a.entry_time < b.entry_time ? -1 : a.entry_time > b.entry_time ? 1 : 0))
  const long = accountSide(view.account, 1)
  const short = accountSide(view.account, -1)
  const cost = costOf(data)
  const name = PRODUCT_NAME[data.product ?? 'MTX']
  return (
    <>
      <StatusBadges data={data} mode={mode} now={now} secs={secs} gap={g ?? undefined} />
      <div className="cq-tiles cq-fu-tiles">
        <Kpi
          label="帳戶合計"
          value={yuan(view.account.total)}
          tone={tone(view.account.total)}
          sub={mode === 'replay' ? `多空各最多 1 口・到 ${cut}` : '多空各最多 1 口・已實現＋現在平倉的話'}
        />
        <Kpi label="多單" value={yuan(long.value)} tone={tone(long.value)} sub={long.text} />
        <Kpi label="空單" value={yuan(short.value)} tone={tone(short.value)} sub={short.text} />
        <PriceKpi p={view.price} prevClose={data.prev_close} />
      </div>
      <Section
        icon={Activity}
        title={`${name}江波圖`}
        meta={
          <span className="cq-fu-legend">
            <i style={{ background: PRICE }} />價 <i style={{ background: VWAP }} />VWAP <i style={{ background: DOWN }} />噪音區下緣
          </span>
        }
      >
        {mode === 'replay' && <NowBar view={view} />}
        <FuturesChart day={data.day} bars={view.bars} secs={secs} pos={player.pos} noiseLb={data.noise_lb} levels={levels} fills={view.fills} />
        {mode === 'replay' && player.lastPos > 0 && <Scrubber player={player} secs={secs} jumpTimes={jumps} />}
      </Section>
      <Section icon={Layers} title="各策略" meta="每條各 1 口的對照">
        <StrategyBoard data={data} view={view} cut={cut} />
      </Section>
      <Section icon={ListOrdered} title="時間軸" meta={`${view.events.length} 件`}>
        <Timeline events={view.events} newestFirst={mode === 'live'} />
      </Section>
      <Section icon={ReceiptText} title="帳戶成交明細" fold summary={`${done.length} 筆・${yuan(done.reduce((a, t) => a + t.net_pnl, 0))} 元`}>
        <Trades trades={done} />
      </Section>
      <Section icon={BookOpen} title="規則與成本" fold summary="只記帳、不下單">
        <p className="cq-fu-foot">
          <b>固定組合</b>（2026-10-26 凍結，小台、大台、微台同一套）：開盤 5 分鐘收紅才做多（停損在那 5 分鐘低點、停利 10 倍）；
          噪音區每 30 分鐘檢查一次，跌破下緣又在 VWAP 之下才放空。
        </p>
        <p className="cq-fu-foot">
          <b>開高走低</b>：只在開盤比昨收高 {(g?.threshold_pct ?? 0.526).toFixed(3)}% 以上的日子做（過去 1998～2015 年開盤漲幅排前 20% 的門檻）。
          A 開高失守：08:50 起某一分鐘收在開盤價之下就放空，停損放在當天到那時的最高價；B 跌破開盤區間：09:00 起收破 08:45～08:59 的最低價就放空，停損放在那段的最高價；
          C 補缺口：進場同 A，碰到昨收就回補。九年歷史的回測還沒跑（等富邦 1 分 K），以前光是「開高就空」長期是不賺的。
        </p>
        <p className="cq-fu-foot">
          <b>帳戶</b>：多單、空單各最多 1 口{name}。誰先出訊號誰先做，同一秒照 A、B、C、噪音區的順序（多單只有開盤突破一條）；
          同方向已經有 1 口時，後來的訊號整筆不做，不會等前一口出場再補進。上面的數字、圖上的買賣點、成交明細是這個帳戶；
          「各策略」和日曆的固定組合／開高走低是每條各做 1 口的對照。
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
      {mode === 'replay' && days !== null && <PnlCalendar days={days} value={replayDay} onPick={setPick} />}
      {error && <p className="cq-empty">{error}</p>}
      {data && isPaper(data) && <PaperBody data={data} secs={secs} player={player} mode={mode} now={live.now} />}
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
            <FuturesChart day={data.day} bars={at.bars} secs={secs} pos={player.pos} noiseLb={NO_LB} levels={NO_LEVELS} fills={NO_FILLS} />
            {mode === 'replay' && player.lastPos > 0 && <Scrubber player={player} secs={secs} jumpTimes={null} />}
          </Section>
          <p className="cq-fu-foot">
            這一天前面的逐筆不足 14 天（噪音區要用前 14 天的波動），只有行情、沒有模擬單。秒級復盤只有逐筆的日子才有。富邦行情只供本人使用，不對外顯示。
          </p>
        </>
      )}
      {!error && !data && <p className="cq-empty">讀取中…</p>}
    </>
  )
}

/** 大台、微台的圖沒有噪音區與買賣點：用固定的空陣列，免得每次重畫都當成新資料。 */
const NO_LB: [string, number][] = []
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
