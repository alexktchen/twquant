// 共用圖表：lightweight-charts 包裝（K 線＋副圖 pane、範圍/均線/關鍵價位開關）。
import { useEffect, useMemo, useRef, useState } from 'react'
import { CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, createChart, createSeriesMarkers, createTextWatermark } from 'lightweight-charts'
import type { IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesPrimitive, SeriesAttachedParameter, Time } from 'lightweight-charts'
import { DEFAULT_DRAW_ON, DRAW_KINDS, buildDrawings, type DrawKind, type DrawNote, type DrawRect, type Drawings } from './stock/drawLayer.ts'
import type { BrokerFlowPoint, Candle, CompositePoint, FactorHistPoint } from './api'
import { kd, macd, volumeProfile } from './stock/indicators.ts'
import { ACC, DOWN, LINE, MUT, UP, WARN, num, priceFmt } from './theme'
import { chipCls } from './ui'

const baseChart = (el: HTMLDivElement, height: number) =>
  createChart(el, {
    width: el.clientWidth,
    height,
    layout: {
      background: { type: ColorType.Solid, color: 'transparent' },
      textColor: MUT,
      fontFamily: 'ui-monospace, "Cascadia Code", Consolas, monospace',
      fontSize: 11,
    },
    grid: { vertLines: { color: 'rgba(31,42,60,0.5)' }, horzLines: { color: LINE } },
    rightPriceScale: { borderColor: LINE },
    timeScale: { borderColor: LINE, rightOffset: 2, fixLeftEdge: true, fixRightEdge: true },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: { color: MUT, width: 1, style: 3, labelBackgroundColor: '#172031' },
      horzLine: { color: MUT, width: 1, style: 3, labelBackgroundColor: '#172031' },
    },
    handleScale: { axisPressedMouseMove: { time: true, price: false } },
  })

/* ---------- candlestick + 選配副圖（v5 原生 pane：量 / 綜合因子 / 主力買賣超 / 因子z）---------- */
const VOL_UP = 'rgba(240,72,78,0.45)', VOL_DOWN = 'rgba(34,181,115,0.45)'
// 因子副圖：一因子一 pane，畫方向化 z（z×direction，正=順風）
export type FactorPane = { label: string; direction: number; series: FactorHistPoint[] }
// 主圖疊加均線：由呼叫端以「完整史料」計算再裁切，視窗首日的均值才正確
export type MaLine = { label: string; color: string; points: { time: string; value: number }[] }

// 技術指標副圖的輸入：由 RangeCandle 以「完整史料」算好再裁切，視窗首日的值才正確。
export type KdPanePoint = { time: string; k: number; d: number }
export type MacdPanePoint = { time: string; dif: number; dea: number | null; osc: number | null }

export type ChartMarker = {
  /** Trading session the marker sits on; already mapped by the caller, never by the library. */
  time: string
  position: 'aboveBar' | 'belowBar'
  shape: 'circle' | 'arrowUp' | 'square'
  color: string
  text: string
}

// 自動畫線（T-321）的方框：箱型、缺口。lightweight-charts 沒有矩形，用 series primitive 自己畫在 K 棒後面。
const LINE_STYLE = { solid: LineStyle.Solid, dashed: LineStyle.Dashed, dotted: LineStyle.Dotted } as const
function rectPrimitive(rects: DrawRect[]): ISeriesPrimitive<Time> {
  let host: SeriesAttachedParameter<Time> | null = null
  const renderer: IPrimitivePaneRenderer = {
    draw: () => {},
    drawBackground: (target) => {
      if (!host) return
      const { chart, series } = host
      target.useMediaCoordinateSpace(({ context: ctx }) => {
        for (const r of rects) {
          const x0 = chart.timeScale().timeToCoordinate(r.t0)
          const x1 = chart.timeScale().timeToCoordinate(r.t1)
          const y0 = series.priceToCoordinate(r.top)
          const y1 = series.priceToCoordinate(r.bottom)
          if (x0 === null || x1 === null || y0 === null || y1 === null) continue
          ctx.fillStyle = r.fill
          ctx.fillRect(x0, y0, x1 - x0, y1 - y0)
          ctx.strokeStyle = r.border
          ctx.lineWidth = 1
          ctx.strokeRect(x0 + 0.5, y0 + 0.5, x1 - x0, y1 - y0)
          ctx.fillStyle = r.border
          ctx.font = '10px ui-monospace, Consolas, monospace'
          ctx.fillText(r.label, x0 + 3, y0 - 3)
        }
      })
    },
  }
  const view: IPrimitivePaneView = { zOrder: () => 'bottom', renderer: () => renderer }
  return {
    attached: (p) => { host = p },
    detached: () => { host = null },
    paneViews: () => [view],
  }
}

export function CandleChart({
  data,
  height = 320,
  volume = false,
  composite,
  brokerFlow,
  factorPanes,
  mas,
  keyLevels = false,
  markers,
  focusTime,
  kdPane,
  macdPane,
  drawings,
  onReady,
}: {
  data: Candle[]
  height?: number
  volume?: boolean
  composite?: CompositePoint[]
  brokerFlow?: BrokerFlowPoint[]
  factorPanes?: FactorPane[]
  mas?: MaLine[]
  keyLevels?: boolean
  markers?: ChartMarker[]
  focusTime?: string | null
  kdPane?: KdPanePoint[]
  macdPane?: MacdPanePoint[]
  drawings?: Drawings
  /** 圖建立後交出截圖函數（下載 PNG 用），圖銷毀時交出 null；不列入 effect deps，換 callback 不重建圖。 */
  onReady?: (shot: (() => HTMLCanvasElement) | null) => void
}) {
  const box = useRef<HTMLDivElement>(null)
  const legend = useRef<HTMLDivElement>(null)
  const ready = useRef(onReady)
  ready.current = onReady
  useEffect(() => {
    const el = box.current
    if (!el) return
    const chart = baseChart(el, height)
    const candle = chart.addSeries(
      CandlestickSeries,
      { upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN },
      0,
    )
    candle.setData(data.map((k) => ({ time: k.d, open: k.o, high: k.h, low: k.l, close: k.c })))

    // 主圖疊加均線（pane 0）；legend 隨 crosshair 顯示各均線值
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const maRefs: { label: string; color: string; s: any }[] = []
    for (const m of mas ?? []) {
      if (m.points.length < 2) continue
      const s = chart.addSeries(
        LineSeries,
        { color: m.color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false },
        0,
      )
      s.setData(m.points.map((p) => ({ time: p.time, value: p.value })))
      maRefs.push({ label: m.label, color: m.color, s })
    }

    // 關鍵價位（視窗內量價分布）：POC 實線 + 70% 價值區頂/底虛線，標在主圖價格軸
    if (keyLevels) {
      const lv = volumeProfile(data)
      if (lv) {
        candle.createPriceLine({ price: lv.poc, color: WARN, lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: `POC ${priceFmt(lv.poc)}` })
        candle.createPriceLine({ price: lv.vah, color: MUT, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: `VA頂 ${priceFmt(lv.vah)}` })
        candle.createPriceLine({ price: lv.val, color: MUT, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: `VA底 ${priceFmt(lv.val)}` })
      }
    }

    // 自動畫線：斜線用只有端點的 LineSeries（lightweight-charts 以 K 棒序數等距，兩點即直線），水平價位用 price line。
    for (const sg of drawings?.segs ?? []) {
      if (sg.points.length < 2) continue
      const s = chart.addSeries(
        LineSeries,
        {
          color: sg.color,
          lineWidth: sg.width,
          lineStyle: LINE_STYLE[sg.style],
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          pointMarkersVisible: false,
          // 畫線不參與自動縮放：陡的趨勢線延伸到今天可能遠離 K 棒，會把主圖壓扁
          autoscaleInfoProvider: () => null,
        },
        0,
      )
      s.setData(sg.points)
    }
    for (const lv of drawings?.levels ?? []) {
      candle.createPriceLine({ price: lv.price, color: lv.color, lineWidth: 1, lineStyle: LINE_STYLE[lv.style], axisLabelVisible: true, title: lv.label })
    }
    if (drawings?.rects.length) candle.attachPrimitive(rectPrimitive(drawings.rects))

    // 每個副圖各佔一個 pane；stretch factor 讓主圖佔大部分高度。
    // titles 與 pane 序號對齊——每個副圖左上角以 watermark 標示指標名稱。
    const stretch: number[] = [6]
    const titles: string[] = ['']
    let pane = 1
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let pctS: any = null
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let cumS: any = null
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let nbarS: any = null
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let kS: any = null, dS: any = null, difS: any = null, deaS: any = null, oscS: any = null

    if (volume) {
      const vol = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'right' }, pane)
      vol.setData(data.map((k) => ({ time: k.d, value: k.v ?? k.amt ?? 0, color: k.c >= k.o ? VOL_UP : VOL_DOWN })))
      stretch.push(1.6)
      titles.push('成交量（股）')
      pane++
    }

    if (composite && composite.length) {
      const zbar = chart.addSeries(
        HistogramSeries,
        { priceFormat: { type: 'price', precision: 2, minMove: 0.01 }, priceScaleId: 'right', base: 0 },
        pane,
      )
      zbar.setData(composite.map((p) => ({ time: p.date, value: p.signed_z_mean, color: p.signed_z_mean >= 0 ? VOL_UP : VOL_DOWN })))
      pctS = chart.addSeries(LineSeries, { priceScaleId: 'pct', color: ACC, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }, pane)
      pctS.setData(composite.map((p) => ({ time: p.date, value: p.pct * 100 })))
      stretch.push(2)
      titles.push('綜合因子分位（棒＝signed-z ｜ 線＝宇宙分位%）')
      pane++
    }

    if (brokerFlow && brokerFlow.length) {
      const nbar = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'right', base: 0 }, pane)
      nbar.setData(brokerFlow.map((p) => ({ time: p.date, value: Math.round(p.net / 1000), color: p.net >= 0 ? VOL_UP : VOL_DOWN })))
      nbarS = nbar
      let cum = 0
      cumS = chart.addSeries(LineSeries, { priceScaleId: 'cum', color: WARN, lineWidth: 2, priceLineVisible: false, lastValueVisible: false }, pane)
      cumS.setData(brokerFlow.map((p) => { cum += Math.round(p.net / 1000); return { time: p.date, value: cum } }))
      stretch.push(2.4)
      titles.push('主力買賣超（張 ｜ 線＝累計）')
      pane++
    }

    for (const fp of factorPanes ?? []) {
      const pts = fp.series.filter((p) => p.z != null)
      if (pts.length < 2) continue
      const fbar = chart.addSeries(
        HistogramSeries,
        { priceFormat: { type: 'price', precision: 2, minMove: 0.01 }, priceScaleId: 'right', base: 0 },
        pane,
      )
      fbar.setData(pts.map((p) => {
        const v = (p.z as number) * fp.direction
        return { time: p.date, value: v, color: v >= 0 ? VOL_UP : VOL_DOWN }
      }))
      stretch.push(1.8)
      titles.push(`${fp.label}（方向化z ｜ 正=順風）`)
      pane++
    }

    // KD 副圖：K／D 兩線＋高低檔參考線（數值由 indicators.ts 算好傳入）
    if (kdPane && kdPane.length > 1) {
      const fmt = { type: 'price' as const, precision: 1, minMove: 0.1 }
      kS = chart.addSeries(LineSeries, { color: WARN, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, priceFormat: fmt }, pane)
      dS = chart.addSeries(LineSeries, { color: ACC, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, priceFormat: fmt }, pane)
      kS.setData(kdPane.map((p) => ({ time: p.time, value: p.k })))
      dS.setData(kdPane.map((p) => ({ time: p.time, value: p.d })))
      for (const level of KD_GUIDES) kS.createPriceLine({ price: level, color: MUT, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: '' })
      stretch.push(1.8)
      titles.push(`KD（橙＝K ｜ 藍＝D ｜ 虛線 ${KD_GUIDES.join('／')}）`)
      pane++
    }

    // MACD 副圖：OSC 柱＋DIF／訊號線
    if (macdPane && macdPane.length > 1) {
      const fmt = { type: 'price' as const, precision: 2, minMove: 0.01 }
      oscS = chart.addSeries(HistogramSeries, { priceFormat: fmt, priceScaleId: 'right', base: 0, priceLineVisible: false, lastValueVisible: false }, pane)
      oscS.setData(macdPane.filter((p) => p.osc !== null).map((p) => ({ time: p.time, value: p.osc as number, color: (p.osc as number) >= 0 ? VOL_UP : VOL_DOWN })))
      difS = chart.addSeries(LineSeries, { color: ACC, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, priceFormat: fmt }, pane)
      difS.setData(macdPane.map((p) => ({ time: p.time, value: p.dif })))
      deaS = chart.addSeries(LineSeries, { color: WARN, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, priceFormat: fmt }, pane)
      deaS.setData(macdPane.filter((p) => p.dea !== null).map((p) => ({ time: p.time, value: p.dea as number })))
      stretch.push(1.8)
      titles.push('MACD（柱＝OSC ｜ 藍＝DIF ｜ 橙＝訊號線）')
      pane++
    }

    const panes = chart.panes()
    stretch.forEach((s, i) => panes[i]?.setStretchFactor(s))
    titles.forEach((t, i) => {
      if (t && panes[i])
        createTextWatermark(panes[i], {
          horzAlign: 'left',
          vertAlign: 'top',
          lines: [{ text: t, color: 'rgba(139,152,165,0.85)', fontSize: 11 }],
        })
    })
    chart.timeScale().fitContent()
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    chart.subscribeCrosshairMove((param: any) => {
      const lg = legend.current
      if (!lg) return
      const d = param?.time ? param.seriesData?.get(candle) : null
      if (!d) {
        lg.style.opacity = '0'
        return
      }
      const up = d.close >= d.open
      let html = `<span style="color:${MUT}">${param.time}</span>　開 ${d.open}　高 ${d.high}　低 ${d.low}　收 <b style="color:${up ? UP : DOWN}">${d.close}</b>`
      for (const m of maRefs) {
        const mv = param.seriesData?.get(m.s)
        if (mv) html += `　<span style="color:${m.color}">${m.label}</span> ${Number(mv.value).toFixed(2)}`
      }
      const pc = pctS && param.seriesData?.get(pctS)
      if (pc) html += `　<span style="color:${MUT}">綜合分位</span> ${Math.round(pc.value)}%`
      const nb = nbarS && param.seriesData?.get(nbarS)
      if (nb) html += `　<span style="color:${MUT}">主力當日</span> <b style="color:${nb.value >= 0 ? UP : DOWN}">${nb.value >= 0 ? '+' : ''}${num(nb.value)}</b> 張`
      const cc = cumS && param.seriesData?.get(cumS)
      if (cc) html += `　<span style="color:${MUT}">主力累計</span> <b style="color:${cc.value >= 0 ? UP : DOWN}">${cc.value >= 0 ? '+' : ''}${num(cc.value)}</b> 張`
      const kv = kS && param.seriesData?.get(kS)
      const dv = dS && param.seriesData?.get(dS)
      if (kv && dv) html += `　<span style="color:${MUT}">KD</span> ${Number(kv.value).toFixed(1)}／${Number(dv.value).toFixed(1)}`
      const df = difS && param.seriesData?.get(difS)
      const de = deaS && param.seriesData?.get(deaS)
      const os = oscS && param.seriesData?.get(oscS)
      if (df) html += `　<span style="color:${MUT}">DIF</span> ${Number(df.value).toFixed(2)}`
      if (de) html += `　<span style="color:${MUT}">MACD</span> ${Number(de.value).toFixed(2)}`
      if (os) html += `　<span style="color:${MUT}">OSC</span> <b style="color:${os.value >= 0 ? UP : DOWN}">${Number(os.value).toFixed(2)}</b>`
      lg.style.opacity = '1'
      lg.innerHTML = html
    })
    // 推薦標記用 v5 的 createSeriesMarkers（不用已移除的 series.setMarkers）。
    // 標記由呼叫端算好交易日映射；圖表不自行把事件貼到相鄰 K 棒。
    if (markers?.length) {
      createSeriesMarkers(
        candle,
        markers.map((m) => ({ time: m.time, position: m.position, shape: m.shape, color: m.color, text: m.text })),
      )
    }
    // 定位推薦日：把該日置於可視區中央；找不到該日就維持原視窗，不亂捲。
    if (focusTime) {
      const idx = data.findIndex((k) => k.d === focusTime)
      if (idx >= 0) chart.timeScale().setVisibleLogicalRange({ from: idx - 25, to: idx + 25 })
    }
    const ro = new ResizeObserver(() => box.current && chart.applyOptions({ width: box.current.clientWidth }))
    ro.observe(el)
    ready.current?.(() => chart.takeScreenshot())
    return () => {
      ready.current?.(null)
      ro.disconnect()
      chart.remove()
    }
  }, [data, height, volume, composite, brokerFlow, factorPanes, mas, keyLevels, markers, focusTime, kdPane, macdPane, drawings])
  return (
    <div style={{ position: 'relative' }}>
      <div
        ref={legend}
        className="mono tnum"
        style={{ position: 'absolute', top: 4, left: 8, zIndex: 3, fontSize: 11, pointerEvents: 'none', opacity: 0 }}
      />
      <div ref={box} style={{ width: '100%' }} />
    </div>
  )
}

/* ---------- range chips (slice history) + 主圖均線/關鍵價位開關 ---------- */
/* 開關設定 localStorage 持久化：跨股票、跨重整維持使用者最後選擇（不隨換股/換頁重置）。 */
const CHART_SETTINGS_KEY = 'twquant.chartSettings'
// 設定版本：v2 起預設視窗由「全部」改為 120 日（半寬的儀表板面板塞十年 K 棒看不出近況）。
// 舊版會在首次掛載就把預設值寫進 localStorage，無法分辨是不是使用者自己選的，故升版時只重設 range 一次。
const CHART_SETTINGS_VERSION = 2
const DEFAULT_RANGE = 120
type ChartSettings = { range: number; maOn: number[]; levelsOn: boolean; kdOn: boolean; macdOn: boolean; drawOn: DrawKind[] }
const isDrawKind = (x: unknown): x is DrawKind => DRAW_KINDS.some((k) => k.kind === x)
function loadChartSettings(): ChartSettings {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(CHART_SETTINGS_KEY) ?? 'null')
    if (v && typeof v === 'object') {
      const o = v as Record<string, unknown>
      return {
        range: o.v === CHART_SETTINGS_VERSION && typeof o.range === 'number' ? o.range : DEFAULT_RANGE,
        maOn: Array.isArray(o.maOn) ? o.maOn.filter((x): x is number => typeof x === 'number') : [5, 20],
        levelsOn: typeof o.levelsOn === 'boolean' ? o.levelsOn : true,
        kdOn: o.kdOn === true,
        macdOn: o.macdOn === true,
        drawOn: Array.isArray(o.drawOn) ? o.drawOn.filter(isDrawKind) : DEFAULT_DRAW_ON,
      }
    }
  } catch {
    /* 壞資料：退回預設 */
  }
  return { range: DEFAULT_RANGE, maOn: [5, 20], levelsOn: true, kdOn: false, macdOn: false, drawOn: DEFAULT_DRAW_ON }
}
const MA_DEFS: [number, string][] = [[5, '#f2c94c'], [10, '#5aa2f7'], [20, '#e578d1'], [60, '#2dd4bf']]
const KD_GUIDES = [80, 20] // KD 高／低檔參考線（與 readings.ts 的 kdHigh／kdLow 同值）
const INDICATOR_PANE_H = 90 // 每開一個 KD／MACD 副圖加高多少，主圖才不會被壓扁
export function RangeCandle({
  data,
  height,
  volume,
  composite,
  brokerFlow,
  factorPanes,
  markers,
  focusTime,
  onResetView,
  onReady,
}: {
  data: Candle[]
  height?: number
  volume?: boolean
  composite?: CompositePoint[]
  brokerFlow?: BrokerFlowPoint[]
  factorPanes?: FactorPane[]
  markers?: ChartMarker[]
  focusTime?: string | null
  onResetView?: () => void
  onReady?: (shot: (() => HTMLCanvasElement) | null) => void
}) {
  const [markersOn, setMarkersOn] = useState(true)
  const [range, setRange] = useState(() => loadChartSettings().range)
  const [maOn, setMaOn] = useState<number[]>(() => loadChartSettings().maOn)
  const [levelsOn, setLevelsOn] = useState(() => loadChartSettings().levelsOn)
  const [kdOn, setKdOn] = useState(() => loadChartSettings().kdOn)
  const [macdOn, setMacdOn] = useState(() => loadChartSettings().macdOn)
  const [drawOn, setDrawOn] = useState<DrawKind[]>(() => loadChartSettings().drawOn)
  useEffect(() => {
    try {
      localStorage.setItem(CHART_SETTINGS_KEY, JSON.stringify({ v: CHART_SETTINGS_VERSION, range, maOn, levelsOn, kdOn, macdOn, drawOn }))
    } catch {
      /* 隱私模式等寫入失敗：設定僅本次會話有效 */
    }
  }, [range, maOn, levelsOn, kdOn, macdOn, drawOn])
  const toggleDraw = (k: DrawKind) => setDrawOn((cur) => (cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k]))
  const toggleMa = (w: number) => setMaOn((cur) => (cur.includes(w) ? cur.filter((x) => x !== w) : [...cur, w]))
  // useMemo 維持陣列 identity 穩定——CandleChart 的 effect 以這些為 deps，
  // 每次 render 產生新陣列會導致父層任何 state 變動（如搜尋框打字）都整座圖砍掉重建。
  // 要定位的推薦日若早於目前視窗，就暫時顯示完整區間——否則「點日期定位」在短視窗下會默默失效。
  const shown = useMemo(() => {
    const windowed = range > 0 ? data.slice(-range) : data
    return focusTime && windowed.length > 0 && focusTime < windowed[0].d ? data : windowed
  }, [data, range, focusTime])
  // 副圖切到與主圖同一時間窗（日期字串可字典序比較）
  const cutoff = shown.length ? shown[0].d : ''
  // 均線以「完整史料」滾動平均後才裁切到視窗——視窗首日的均值才正確
  const mas = useMemo(() => {
    const out: MaLine[] = []
    for (const [w, color] of MA_DEFS) {
      if (!maOn.includes(w) || data.length < w) continue
      const pts: { time: string; value: number }[] = []
      let s = 0
      for (let i = 0; i < data.length; i++) {
        s += data[i].c
        if (i >= w) s -= data[i - w].c
        if (i >= w - 1) pts.push({ time: data[i].d, value: +(s / w).toFixed(2) })
      }
      out.push({ label: `MA${w}`, color, points: pts })
    }
    return out
  }, [data, maOn])
  const masShown = useMemo(
    () => mas.map((m) => ({ ...m, points: m.points.filter((p) => p.time >= cutoff) })).filter((m) => m.points.length > 1),
    [mas, cutoff],
  )
  const compShown = useMemo(
    () => (composite ? composite.filter((p) => p.date >= cutoff) : undefined),
    [composite, cutoff],
  )
  const flowShown = useMemo(
    () => (brokerFlow ? brokerFlow.filter((p) => p.date >= cutoff) : undefined),
    [brokerFlow, cutoff],
  )
  const fpShown = useMemo(
    () =>
      factorPanes
        ? factorPanes.map((fp) => ({ ...fp, series: fp.series.filter((p) => p.date >= cutoff) }))
        : undefined,
    [factorPanes, cutoff],
  )
  // KD／MACD 同均線：以完整史料遞推後才裁到視窗（指數平滑有記憶，從視窗首日起算會算錯）。
  const kdShown = useMemo(() => {
    if (!kdOn) return undefined
    const pts = kd(data)
    return data.flatMap((k, i) => (pts[i] && k.d >= cutoff ? [{ time: k.d, k: pts[i]!.k, d: pts[i]!.d }] : []))
  }, [data, kdOn, cutoff])
  const macdShown = useMemo(() => {
    if (!macdOn) return undefined
    const pts = macd(data.map((k) => k.c))
    return data.flatMap((k, i) => (pts[i] && k.d >= cutoff ? [{ time: k.d, ...pts[i]! }] : []))
  }, [data, macdOn, cutoff])
  // 自動畫線以「目前視窗」偵測：換 60／120 日視窗，線跟著畫面上看得到的波段走（同量價分布）。
  const auto = useMemo(() => buildDrawings(shown, drawOn), [shown, drawOn])
  const extraH = ((kdShown?.length ?? 0) > 1 ? INDICATOR_PANE_H : 0) + ((macdShown?.length ?? 0) > 1 ? INDICATOR_PANE_H : 0)
  // 標記同樣裁到目前視窗；超出視窗的事件仍留在事件清單，不會被靜默丟掉。
  const shownMarkers = useMemo(
    () => (markers ? markers.filter((m) => m.time >= cutoff) : undefined),
    [markers, cutoff],
  )
  return (
    <div>
      <div className="flex flex-wrap items-center gap-1 mb-1">
        <span className="text-[11px] text-[var(--muted)] mr-1" title="依轉折點自動畫出，只描述圖形結構供判讀，不是買賣訊號（文獻：自動支撐壓力扣成本後無超額報酬）">
          自動畫線
        </span>
        {DRAW_KINDS.map((k) => (
          <button key={k.kind} onClick={() => toggleDraw(k.kind)} className={chipCls(drawOn.includes(k.kind))} title={k.title}>
            {k.label}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-1 justify-between mb-1">
        <div className="flex flex-wrap items-center gap-1">
          {MA_DEFS.map(([w, color]) => (
            <button key={w} onClick={() => toggleMa(w)} className={chipCls(maOn.includes(w))} title={`${w} 日收盤均線`}>
              <span style={maOn.includes(w) ? { color } : undefined}>MA{w}</span>
            </button>
          ))}
          <button
            onClick={() => setLevelsOn((v) => !v)}
            className={chipCls(levelsOn)}
            title="視窗內量價分布的關鍵價位：POC＝最大量價位（實線）、VA頂/VA底＝70% 價值區（虛線）"
          >
            關鍵價位
          </button>
          <button onClick={() => setKdOn((v) => !v)} className={chipCls(kdOn)} title="KD 隨機指標副圖（9 日 RSV、1/3 平滑）">
            KD
          </button>
          <button onClick={() => setMacdOn((v) => !v)} className={chipCls(macdOn)} title="MACD 副圖（12／26／9，以收盤價計）">
            MACD
          </button>
          {markers && markers.length > 0 && (
            <button
              onClick={() => setMarkersOn((v) => !v)}
              className={chipCls(markersOn)}
              title="顯示或隱藏推薦日標記；標記依實際發布日對應交易日，非交易日發布會標到下一個交易日"
            >
              推薦標記
            </button>
          )}
          {focusTime && onResetView && (
            <button onClick={onResetView} className={chipCls(false)} title="重設為完整可視區間">
              查看全區間
            </button>
          )}
        </div>
        <div className="flex gap-1">
          {([[0, '全部'], [120, '120日'], [60, '60日']] as [number, string][]).map(([r, label]) => (
            <button
              key={label}
              onClick={() => setRange(r)}
              className={`px-2 py-0.5 text-[11px] rounded border transition-colors ${
                range === r ? 'border-[var(--accent)] text-[var(--accent)]' : 'border-[var(--line)] text-[var(--muted)] hover:text-[var(--fg)]'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <CandleChart
        data={shown}
        height={height === undefined ? undefined : height + extraH}
        volume={volume}
        composite={compShown}
        brokerFlow={flowShown}
        factorPanes={fpShown}
        mas={masShown.length ? masShown : undefined}
        keyLevels={levelsOn}
        markers={markersOn && markers?.length ? shownMarkers : undefined}
        focusTime={focusTime}
        kdPane={kdShown}
        macdPane={macdShown}
        drawings={drawOn.length ? auto.drawings : undefined}
        onReady={onReady}
      />
      {auto.notes.length > 0 && <DrawReadout notes={auto.notes} close={shown[shown.length - 1].c} />}
    </div>
  )
}

/** 判讀清單：所有自動畫線在最後一根 K 棒的價位，由高到低，並把最後收盤插在中間。 */
function DrawReadout({ notes, close }: { notes: DrawNote[]; close: number }) {
  const above = notes.filter((x) => x.price > close)
  const below = notes.filter((x) => x.price <= close)
  const row = (x: DrawNote, i: number) => (
    <div key={i} className="flex items-baseline gap-2 py-0.5">
      <span className="inline-block w-2 h-2 rounded-sm shrink-0" style={{ background: x.color }} />
      <span className="w-40 shrink-0">{x.label}</span>
      <span className="mono tnum w-16 text-right shrink-0">{priceFmt(x.price)}</span>
      <span className="mono tnum w-16 text-right shrink-0" style={{ color: x.dist >= 0 ? UP : DOWN }}>
        {x.dist >= 0 ? '+' : ''}{(x.dist * 100).toFixed(1)}%
      </span>
      <span className="text-[var(--muted)] truncate">{x.detail}</span>
    </div>
  )
  return (
    <details className="mt-2 text-[11px]" open>
      <summary className="cursor-pointer text-[var(--muted)]">判讀清單（各線今日價位，距收盤 %）</summary>
      <div className="mt-1">
        {above.map(row)}
        <div className="flex items-baseline gap-2 py-0.5 border-y border-[var(--line)] font-semibold">
          <span className="inline-block w-2 h-2 shrink-0" />
          <span className="w-40 shrink-0">最後收盤</span>
          <span className="mono tnum w-16 text-right shrink-0">{priceFmt(close)}</span>
        </div>
        {below.map(row)}
      </div>
    </details>
  )
}
