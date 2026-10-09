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
}: {
  bars: Bar[]
  pickDays: Record<string, string[]>
  lineNames: Record<string, string>
}) {
  const el = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const box = el.current
    if (!box || bars.length === 0) return
    const chart = createChart(box, {
      width: box.clientWidth,
      height: box.clientHeight,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#8A8F98',
        fontSize: 11,
        panes: { separatorColor: '#23252D' },
      },
      grid: { vertLines: { color: '#1C1E25' }, horzLines: { color: '#1C1E25' } },
      rightPriceScale: { borderColor: '#23252D' },
      timeScale: { borderColor: '#23252D', rightOffset: 6 },
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
        position: 'belowBar' as const,
        shape: 'arrowUp' as const,
        color: LINE_COLOR[m.line] ?? '#FF7A00',
        text: m.text,
      })),
    )
    // 先看最近約半年（120 根）；要等版面算好才設，否則會被自動縮放蓋掉
    const frame = requestAnimationFrame(() =>
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, bars.length - 120), to: bars.length + 5 }),
    )
    const ro = new ResizeObserver(() => chart.applyOptions({ width: box.clientWidth, height: box.clientHeight }))
    ro.observe(box)
    return () => {
      cancelAnimationFrame(frame)
      ro.disconnect()
      chart.remove()
    }
  }, [bars, pickDays, lineNames])
  return <div ref={el} className="cq-chart" />
}

export const MA_LEGEND = MA
