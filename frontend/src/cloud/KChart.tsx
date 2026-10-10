// 雲端版（T-332）個股 K 線：富途式深色底、紅漲綠跌、MA5/20/60、下方成交量、被推薦日標記。
import { useEffect, useRef, useState } from 'react'
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
} from 'lightweight-charts'
import { pct, pickMarkers, price, quoteBar, signedPrice, sma, trend, type Bar } from './format'

const UP = '#F6465D'
const DOWN = '#0ECB81'
const MA = [
  { n: 5, color: '#F0B90B' },
  { n: 20, color: '#E040FB' },
  { n: 60, color: '#3D9BFF' },
]
const LINE_COLOR: Record<string, string> = { CORE: '#FF7A00', QUIET: '#3D9BFF' }

export function KChart({
  bars,
  pickDays,
  lineNames,
}: {
  bars: Bar[]
  pickDays: Record<string, string[]>
  lineNames: Record<string, string>
}) {
  const el = useRef<HTMLDivElement>(null)
  // 長按（觸控進入十字線追蹤）或滑鼠指到的那根；left＝資訊框放左邊（手指在右半邊）
  const [tip, setTip] = useState<{ i: number; left: boolean } | null>(null)
  useEffect(() => {
    const box = el.current
    if (!box || bars.length === 0) return
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
    candle.setData(bars.map((b) => ({ time: b.date, open: b.open, high: b.high, low: b.low, close: b.close })))
    for (const m of MA) {
      const s = chart.addSeries(LineSeries, {
        color: m.color,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      })
      s.setData(sma(bars, m.n))
    }
    const vol = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' } }, 1)
    vol.setData(
      bars.map((b) => ({ time: b.date, value: b.volume, color: b.close >= b.open ? `${UP}99` : `${DOWN}99` })),
    )
    chart.panes()[1]?.setHeight(Math.round(box.clientHeight * 0.22))
    createSeriesMarkers(
      candle,
      pickMarkers(pickDays, lineNames, bars).map((m) => ({
        time: m.time,
        position: 'aboveBar' as const,
        shape: 'arrowDown' as const,
        size: 0.5,
        color: LINE_COLOR[m.line] ?? '#FF7A00',
      })),
    )
    // 長按（觸控會進入十字線追蹤模式）或滑鼠移動：在圖上浮出指到那天的量價
    chart.subscribeCrosshairMove((param) => {
      const i = param.point && param.logical !== undefined ? Math.round(param.logical) : -1
      if (!param.point || i < 0 || i >= bars.length) return setTip(null)
      const left = param.point.x > box.clientWidth / 2
      setTip((t) => (t && t.i === i && t.left === left ? t : { i, left }))
    })
    // 先看最近約半年（120 根）；要等版面算好才設，否則會被自動縮放蓋掉
    const frame = requestAnimationFrame(() =>
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, bars.length - 120), to: bars.length + 1 }),
    )
    const ro = new ResizeObserver(() => chart.applyOptions({ width: box.clientWidth, height: box.clientHeight }))
    ro.observe(box)
    return () => {
      cancelAnimationFrame(frame)
      ro.disconnect()
      setTip(null)
      chart.remove()
    }
  }, [bars, pickDays, lineNames])
  const q = tip ? quoteBar(bars, undefined, tip.i) : null
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
