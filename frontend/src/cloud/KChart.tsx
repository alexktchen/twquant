// 雲端版（T-332）個股 K 線：富途式深色底、紅漲綠跌、MA5/20/60、下方成交量、被推薦日標記。
import { useEffect, useRef } from 'react'
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
} from 'lightweight-charts'
import { pickMarkers, sma, type Bar } from './format'

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
  onHover,
}: {
  bars: Bar[]
  pickDays: Record<string, string[]>
  lineNames: Record<string, string>
  /** 長按（觸控）或滑鼠移到 K 棒上時回報第幾根；離開時回報 null */
  onHover?: (index: number | null) => void
}) {
  const el = useRef<HTMLDivElement>(null)
  // 回呼放 ref：換函式不必重畫整張圖
  const hoverRef = useRef(onHover)
  hoverRef.current = onHover
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
    // 長按（觸控會進入十字線追蹤模式）或滑鼠移動：回報第幾根 K 棒，報價列跟著換成那一根
    chart.subscribeCrosshairMove((param) => {
      const i = param.point && param.logical !== undefined ? Math.round(param.logical) : null
      hoverRef.current?.(i !== null && i >= 0 && i < bars.length ? i : null)
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
      hoverRef.current?.(null)
      chart.remove()
    }
  }, [bars, pickDays, lineNames])
  return <div ref={el} className="cq-chart" />
}

export const MA_LEGEND = MA
export const LINE_LEGEND = LINE_COLOR
