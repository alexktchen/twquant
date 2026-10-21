// 雲端版（T-332）個股 K 線：富途式深色底、紅漲綠跌、MA5/20/60、下方成交量、被推薦日標記；K 棒下方藍點＝那天有新聞（T-419），點了列出來。
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type ISeriesApi,
} from 'lightweight-charts'
import { loadPref, savePref } from './prefs'
import { KEPT_BARS_KEY, latestRange, parseKeptBars, pct, pickMarkers, price, quoteBar, signedPrice, sma, trend, type Bar } from './format'

const UP = '#F6465D'
const DOWN = '#0ECB81'
const MA = [
  { n: 5, color: '#F0B90B' },
  { n: 20, color: '#E040FB' },
  { n: 60, color: '#3D9BFF' },
]
const LINE_COLOR: Record<string, string> = { CORE: '#FF7A00', QUIET: '#3D9BFF' }
const NEWS_COLOR = '#79B8FF'

// 使用者縮放後看幾根 K 棒：換股票、重新整理都沿用（存在這台裝置的瀏覽器，隱私權政策有寫）
let keptBars = parseKeptBars(loadPref(KEPT_BARS_KEY))
function keepBars(n: number) {
  if (n === keptBars) return
  keptBars = n
  savePref(KEPT_BARS_KEY, String(n))
}

export function KChart({
  bars,
  live = null,
  pickDays,
  lineNames,
  newsDays = [],
  onDayClick,
}: {
  bars: Bar[]
  /** 盤中今天到目前為止的那根（資料庫傍晚才有今天）；每 10 秒換一次，只更新最後一根、不重畫整張圖 */
  live?: Bar | null
  pickDays: Record<string, string[]>
  lineNames: Record<string, string>
  /** 那天有幾則直接提到這檔的新聞（T-419）：K 棒下方一個藍點，2 則以上寫數字 */
  newsDays?: { time: string; n: number }[]
  /** 點了某一天（有新聞的那天才會呼叫） */
  onDayClick?: (date: string) => void
}) {
  const el = useRef<HTMLDivElement>(null)
  // 長按（觸控進入十字線追蹤）或滑鼠指到的那根；left＝資訊框放左邊（手指在右半邊）
  const [tip, setTip] = useState<{ i: number; left: boolean } | null>(null)
  const all = useMemo(() => (live ? [...bars, live] : bars), [bars, live])
  // 建圖時要用到最新的那根，但盤中那根變動不該觸發重畫（使用者拉到哪就停在哪）
  const allRef = useRef(all)
  allRef.current = all
  const onDayClickRef = useRef(onDayClick)
  onDayClickRef.current = onDayClick
  const series = useRef<{
    candle: ISeriesApi<'Candlestick'>
    vol: ISeriesApi<'Histogram'>
    mas: { n: number; s: ISeriesApi<'Line'> }[]
  } | null>(null)
  useEffect(() => {
    const box = el.current
    const data = allRef.current
    if (!box || data.length === 0) return
    const chart = createChart(box, {
      width: box.clientWidth,
      height: box.clientHeight,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#8A8F98',
        fontSize: 13,
        panes: { separatorColor: '#23252D' },
      },
      grid: { vertLines: { color: '#1C1E25' }, horzLines: { color: '#1C1E25' } },
      // 價格軸一律自動貼合；手機上上下拖動交給頁面捲動，否則一拖（或雙指縮放時手指上下動）
      // 價格軸就被平移、K 棒跑出畫面且不會回來
      rightPriceScale: { borderColor: '#23252D', autoScale: true },
      handleScroll: { vertTouchDrag: false },
      handleScale: { axisPressedMouseMove: { time: true, price: false } },
      // 不能拖出資料範圍外的空白
      timeScale: { borderColor: '#23252D', rightOffset: 2, fixLeftEdge: true, fixRightEdge: true },
      crosshair: { mode: CrosshairMode.Normal },
    })
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderUpColor: UP,
      borderDownColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
    })
    candle.setData(data.map((b) => ({ time: b.date, open: b.open, high: b.high, low: b.low, close: b.close })))
    const mas: { n: number; s: ISeriesApi<'Line'> }[] = []
    for (const m of MA) {
      const s = chart.addSeries(LineSeries, {
        color: m.color,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      })
      s.setData(sma(data, m.n))
      mas.push({ n: m.n, s })
    }
    const vol = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' } }, 1)
    vol.setData(
      data.map((b) => ({ time: b.date, value: b.volume, color: b.close >= b.open ? `${UP}99` : `${DOWN}99` })),
    )
    series.current = { candle, vol, mas }
    chart.panes()[1]?.setHeight(Math.round(box.clientHeight * 0.22))
    createSeriesMarkers(candle, [
      ...pickMarkers(pickDays, lineNames, bars).map((m) => ({
        time: m.time,
        position: 'aboveBar' as const,
        shape: 'arrowDown' as const,
        size: 0.5,
        color: LINE_COLOR[m.line] ?? '#FF7A00',
      })),
      ...newsDays.map((d) => ({ time: d.time, position: 'belowBar' as const, shape: 'circle' as const, size: 0.6, color: NEWS_COLOR, text: d.n > 1 ? String(d.n) : '' })),
    ])
    // 點一天：有新聞的那天就把那天的新聞列在圖下面
    const newsSet = new Set(newsDays.map((d) => d.time))
    chart.subscribeClick((param) => {
      // 時間可能回字串（照資料給的）或 {year, month, day}
      const tm = param.time as string | { year: number; month: number; day: number } | undefined
      const t = typeof tm === 'string' ? tm : tm ? `${tm.year}-${String(tm.month).padStart(2, '0')}-${String(tm.day).padStart(2, '0')}` : null
      if (t && newsSet.has(t)) onDayClickRef.current?.(t)
    })
    // 長按（觸控會進入十字線追蹤模式）或滑鼠移動：在圖上浮出指到那天的量價
    chart.subscribeCrosshairMove((param) => {
      const i = param.point && param.logical !== undefined ? Math.round(param.logical) : -1
      if (!param.point || i < 0 || i >= allRef.current.length) return setTip(null)
      const left = param.point.x > box.clientWidth / 2
      setTip((t) => (t && t.i === i && t.left === left ? t : { i, left }))
    })
    // 先看最近約半年（120 根）；要等版面算好才設，否則會被自動縮放蓋掉
    const frame = requestAnimationFrame(() => chart.timeScale().setVisibleLogicalRange(latestRange(data.length, keptBars)))
    // 只有使用者自己縮放（觸控、滑鼠、滾輪）後才記住根數；開圖時程式設的範圍不算
    let touched = false
    const mark = () => {
      touched = true
    }
    box.addEventListener('pointerdown', mark)
    box.addEventListener('touchstart', mark, { passive: true })
    box.addEventListener('wheel', mark, { passive: true })
    chart.timeScale().subscribeVisibleLogicalRangeChange((r) => {
      if (touched && r) keepBars(Math.max(5, Math.round(r.to - r.from)))
    })
    const ro = new ResizeObserver(() => chart.applyOptions({ width: box.clientWidth, height: box.clientHeight }))
    ro.observe(box)
    return () => {
      series.current = null
      cancelAnimationFrame(frame)
      ro.disconnect()
      box.removeEventListener('pointerdown', mark)
      box.removeEventListener('touchstart', mark)
      box.removeEventListener('wheel', mark)
      setTip(null)
      chart.remove()
    }
  }, [bars, pickDays, lineNames, newsDays])
  // 盤中那根：只更新最後一根 K 棒、量、三條均線的最後一點
  useEffect(() => {
    const s = series.current
    if (!s || !live) return
    s.candle.update({ time: live.date, open: live.open, high: live.high, low: live.low, close: live.close })
    s.vol.update({ time: live.date, value: live.volume, color: live.close >= live.open ? `${UP}99` : `${DOWN}99` })
    const data = [...bars, live]
    for (const m of s.mas) {
      const p = sma(data, m.n).at(-1)
      if (p && p.time === live.date) m.s.update(p)
    }
  }, [bars, live])
  const q = tip ? quoteBar(all, undefined, tip.i) : null
  return (
    <div className="cq-chart-wrap">
      <div ref={el} className="cq-chart" />
      {tip && q && (
        <div className={`cq-tip ${tip.left ? 'left' : 'right'}`}>
          <div className="cq-tip-date">{q.bar.date}</div>
          <dl>
            <dt>開</dt>
            <dd>{price(q.bar.open)}</dd>
            <dt>高</dt>
            <dd className="up">{price(q.bar.high)}</dd>
            <dt>低</dt>
            <dd className="down">{price(q.bar.low)}</dd>
            <dt>收</dt>
            <dd className={trend(q.chg)}>{price(q.bar.close)}</dd>
            <dt>漲跌</dt>
            <dd className={trend(q.chg)}>{signedPrice(q.diff)}</dd>
            <dt>漲幅</dt>
            <dd className={trend(q.chg)}>{pct(q.chg)}</dd>
            <dt>量</dt>
            <dd>{Math.round(q.bar.volume / 1000).toLocaleString()} 張</dd>
          </dl>
        </div>
      )}
    </div>
  )
}

export const MA_LEGEND = MA
export const LINE_LEGEND = LINE_COLOR
