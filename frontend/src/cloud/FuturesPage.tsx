// 小台模擬（T-450／D-220，使用者 2026-10-26：「這個可以在我網站即時模擬出來嗎？」「畫面可以是復盤模式跟盤中模式」
// 「我想要秒級的復盤」「買進賣出的點位要在復盤過程標出來」）
// 盤中：最新一天，盤中每 15 秒重抓（1 分 K）。復盤：選一天；有逐筆的日子一秒一秒播（10／60／300 倍速），
// 買賣點在成交的那一秒、成交價出現在圖上；只有 1 分 K 的日子一分鐘一分鐘播。
// 只記帳、不送委託；資料來自使用者電腦上的富邦即時行情（fubon_live.py），富邦行情不得轉傳 → 只給 admin。
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
import { fetchFuturesDay, fetchFuturesDays, fetchFuturesSeconds } from './api'
import { price } from './format'
import {
  REASON_TEXT,
  SLEEVES,
  SLEEVE_NAME,
  STALE_AFTER_S,
  addSeconds,
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

function FuturesChart({ data, view, secs, pos }: { data: FuturesDay; view: ReplayView; secs: Seconds | null; pos: number }) {
  const box = useRef<HTMLDivElement>(null)
  const parts = useRef<{
    chart: IChartApi
    px: ISeriesApi<'Line'>
    vw: ISeriesApi<'Line'>
    lb: ISeriesApi<'Line'>
    marks: ISeriesMarkersPluginApi<Time>
    lines: IPriceLine[]
  } | null>(null)
  // 秒級：每一點的圖表時間先算好（一天約 1.6 萬點），播放時只切片
  const secTimes = useMemo(() => (secs ? secs.label.map((l) => chartTime(data.day, l) as UTCTimestamp) : null), [secs, data.day])

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
    const px = chart.addSeries(LineSeries, { color: PRICE, lineWidth: secs ? 1 : 2, priceLineVisible: false })
    parts.current = { chart, px, vw, lb, marks: createSeriesMarkers(px, []), lines: [] }
    return () => {
      chart.remove()
      parts.current = null
    }
  }, [data.day, secs])

  useEffect(() => {
    const p = parts.current
    if (!p) return
    const t = (time: string) => chartTime(data.day, time) as UTCTimestamp
    if (secs && secTimes) {
      // 秒級：拉到的那一秒以前畫線，後面留空白（圖的寬度固定）
      const n = pos + 1
      p.px.setData(secTimes.map((time, i) => (i < n ? { time, value: secs.price[i] } : { time })))
      p.vw.setData(secTimes.slice(0, n).map((time, i) => ({ time, value: secs.vwap[i] })))
    } else {
      const shown = new Map(view.bars.map((b) => [b[0], b]))
      p.px.setData(SESSION.map((m) => (shown.has(m) ? { time: t(m), value: shown.get(m)![4] } : { time: t(m) })))
      p.vw.setData(view.bars.map((b) => ({ time: t(b[0]), value: b[5] })))
    }
    // 噪音區下緣：開盤就算得出今天九個檢查點的值（昨收、開盤、前 14 天波動都已知），不是偷看
    p.lb.setData(view.price !== null ? data.noise_lb.map(([m, v]) => ({ time: t(m), value: v })) : [])
    for (const l of p.lines) p.px.removePriceLine(l)
    p.lines = []
    const orb = data.orb
    if (orb.stop !== null && orb.entry_time !== null && orb.entry_time.slice(0, view.cut.length) <= view.cut)
      p.lines.push(p.px.createPriceLine({ price: orb.stop, color: MUTE, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'ORB 停損' }))
    // 買賣點：成交的那一秒（1 分 K 時是那一分鐘）、成交價的位置
    const marks: SeriesMarker<Time>[] = view.fills.map((f) => {
      const long = f.side > 0
      const time = t(secs ? f.time.slice(0, 8) : f.time.slice(0, 5))
      if (f.kind === 'entry')
        return { time, position: 'atPriceBottom', price: f.price, shape: long ? 'arrowUp' : 'arrowDown', color: long ? UP : DOWN, text: `${long ? '買' : '空'} ${f.price.toFixed(0)}` }
      return { time, position: 'atPriceTop', price: f.price, shape: 'circle', color: MUTE, text: `${long ? '賣' : '補'} ${f.price.toFixed(0)}` }
    })
    p.marks.setMarkers(marks)
  }, [data, view, secs, secTimes, pos])

  useEffect(() => {
    parts.current?.chart.timeScale().fitContent()
  }, [data.day, secs])

  return <div className="cq-fu-chart" ref={box} />
}

function sleeveSub(s: SleeveState, side: 1 | -1): string {
  if (s.position === 0) return s.realized ? `已平倉，這邊 ${yuan(s.realized)}` : '空手'
  const word = side > 0 ? '持多' : '持空'
  return `${word} 1 口 @ ${price(s.entry_price)}（現在平倉 ${yuan(s.open_pnl ?? 0)}）`
}

function StatusBadges({ data, mode, now, secs }: { data: FuturesDay; mode: Mode; now: Date; secs: Seconds | null }) {
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
      {data.official_match !== null && (
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

export function FuturesPage({ refresh }: { refresh: number }) {
  const [mode, setMode] = useState<Mode>('live')
  const [days, setDays] = useState<FuturesDayRow[] | null>(null)
  const [pick, setPick] = useState<string | null>(null)
  const [data, setData] = useState<FuturesDay | null>(null)
  const [secs, setSecs] = useState<Seconds | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [pos, setPos] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState<Speed>(60)
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    fetchFuturesDays()
      .then(setDays)
      .catch((e) => setErr(errText(e)))
  }, [refresh])

  // 盤中：最新一天；08:25～13:50（台北）每 15 秒重抓
  useEffect(() => {
    if (mode !== 'live') return
    let stop = false
    setSecs(null)
    const load = () =>
      fetchFuturesDay()
        .then((d) => {
          if (stop) return
          setData(d)
          setErr(null)
        })
        .catch((e) => !stop && setErr(errText(e)))
    load()
    const id = setInterval(() => {
      const t = new Date()
      setNow(t)
      if (shouldPoll(t, null)) load()
    }, POLL_MS)
    return () => {
      stop = true
      clearInterval(id)
    }
  }, [mode, refresh])

  // 復盤：選的那一天（沒選＝最新一天）；有秒級資料就一秒一秒播，沒有就一分鐘一分鐘播。一進來停在收盤
  const replayDay = pick ?? days?.[0]?.day ?? null
  useEffect(() => {
    if (mode !== 'replay' || replayDay === null) return
    let stop = false
    setPlaying(false)
    Promise.all([fetchFuturesDay(replayDay), fetchFuturesSeconds(replayDay).then(decodeSeconds, () => null)])
      .then(([d, s]) => {
        if (stop) return
        setData(d)
        setSecs(s)
        setPos(Math.max(0, (s ? s.label.length : d.bars.length) - 1))
        setErr(null)
      })
      .catch((e) => !stop && setErr(errText(e)))
    return () => {
      stop = true
    }
  }, [mode, replayDay])

  const lastPos = data ? Math.max(0, (secs ? secs.label.length : data.bars.length) - 1) : 0

  useEffect(() => {
    if (!playing || !data) return
    const id = setInterval(() => {
      setPos((i) => {
        if (i >= lastPos) {
          setPlaying(false)
          return i
        }
        if (!secs) return Math.min(lastPos, i + MINUTE_STEP)
        // 秒級：盤中時間往前推「倍速 × 0.1 秒」，跳到那之前最後一筆（沒成交的秒數直接跳過）
        const j = lastAtOrBefore(secs.label, addSeconds(secs.label[i], (speed * TICK_MS) / 1000))
        return Math.max(i + 1, Math.min(lastPos, j))
      })
    }, TICK_MS)
    return () => clearInterval(id)
  }, [playing, data, secs, speed, lastPos])

  const cut =
    mode !== 'replay' || !data
      ? '23:59'
      : secs
        ? secs.label[Math.min(pos, lastPos)]
        : (data.bars[Math.min(pos, lastPos)]?.[0] ?? '23:59')
  const view = useMemo(() => (data ? replayAt(data, cut, mode === 'replay' ? secs : null) : null), [data, cut, mode, secs])
  // 復盤拉到 cut：只列那之前已經平倉的交易（不劇透後面）
  const done = data ? data.trades.filter((t) => t.exit_time.slice(0, cut.length) <= cut) : []

  /** 跳到上一個／下一個買賣點（成交或平倉那一秒；1 分 K 時是那一分鐘）。 */
  const jump = (dir: 1 | -1) => {
    if (!data) return
    setPlaying(false)
    const times = fillTimes(data).map((t) => (secs ? t : t.slice(0, 5)))
    const target = dir > 0 ? times.find((t) => t > cut) : [...times].reverse().find((t) => t < cut)
    if (target === undefined) return
    const labels = secs ? secs.label : data.bars.map((b) => b[0])
    setPos(Math.max(0, lastAtOrBefore(labels, target)))
  }

  return (
    <main className="cq-body cq-fu">
      <div className="cq-seg cq-fu-mode">
        <button className={mode === 'live' ? 'on' : ''} onClick={() => setMode('live')}>
          <Radio size={15} aria-hidden="true" /> 盤中
        </button>
        <button className={mode === 'replay' ? 'on' : ''} onClick={() => setMode('replay')}>
          <History size={15} aria-hidden="true" /> 復盤
        </button>
      </div>
      {mode === 'replay' && days !== null && days.length > 0 && (
        <label className="cq-fu-pick">
          <span>交易日</span>
          <select value={replayDay ?? ''} onChange={(e) => setPick(e.target.value)}>
            {days.map((d) => (
              <option key={d.day} value={d.day}>
                {d.day}
                {d.total === null ? '' : `（${yuan(d.total)}）`}
                {d.source === 'fubon_live' ? '' : '・期交所重算'}
              </option>
            ))}
          </select>
        </label>
      )}
      {err && <p className="cq-empty">{err}</p>}
      {data && view && (
        <>
          <StatusBadges data={data} mode={mode} now={now} secs={mode === 'replay' ? secs : null} />
          <div className="cq-tiles cq-fu-tiles">
            <Kpi label="今天合計" value={yuan(view.total)} tone={tone(view.total)} sub={mode === 'replay' ? `到 ${cut}` : '已實現＋現在平倉的話'} />
            {SLEEVES.map((s) => {
              const st = view.sleeves[s]
              const v = st.realized + (st.open_pnl ?? 0)
              return <Kpi key={s} label={SLEEVE_NAME[s]} value={yuan(v)} tone={tone(v)} sub={sleeveSub(st, s === 'ORB_LONG' ? 1 : -1)} />
            })}
            <Kpi
              label="小台最新"
              value={price(view.price)}
              tone={tone(view.price === null ? null : view.price - data.prev_close)}
              sub={view.price === null ? '還沒開盤' : `比昨收 ${yuan(view.price - data.prev_close)} 點`}
            />
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
            <FuturesChart data={data} view={view} secs={mode === 'replay' ? secs : null} pos={pos} />
            {mode === 'replay' && lastPos > 0 && (
              <div className="cq-fu-scrub">
                <button className="cq-fu-btn" onClick={() => jump(-1)} aria-label="上一個買賣點" title="上一個買賣點">
                  <SkipBack size={16} />
                </button>
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
                <button className="cq-fu-btn" onClick={() => jump(1)} aria-label="下一個買賣點" title="下一個買賣點">
                  <SkipForward size={16} />
                </button>
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
            )}
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
      {!err && !data && <p className="cq-empty">讀取中…</p>}
    </main>
  )
}
