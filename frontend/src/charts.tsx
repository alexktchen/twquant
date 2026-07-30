// 共用圖表：lightweight-charts 包裝（K 線＋副圖 pane、範圍/均線/關鍵價位開關）。
import { useEffect, useMemo, useRef, useState } from 'react'
import { CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, createChart, createTextWatermark } from 'lightweight-charts'
import type { BrokerFlowPoint, Candle, CompositePoint, FactorHistPoint } from './api'
import { ACC, DOWN, LINE, MUT, UP, num, priceFmt } from './theme'
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
    grid: { vertLines: { color: 'rgba(35,42,51,0.45)' }, horzLines: { color: LINE } },
    rightPriceScale: { borderColor: LINE },
    timeScale: { borderColor: LINE, rightOffset: 2, fixLeftEdge: true, fixRightEdge: true },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: { color: MUT, width: 1, style: 3, labelBackgroundColor: '#1b212a' },
      horzLine: { color: MUT, width: 1, style: 3, labelBackgroundColor: '#1b212a' },
    },
    handleScale: { axisPressedMouseMove: { time: true, price: false } },
  })

/* ---------- candlestick + 選配副圖（v5 原生 pane：量 / 綜合因子 / 主力買賣超 / 因子z）---------- */
const VOL_UP = 'rgba(229,72,77,0.45)', VOL_DOWN = 'rgba(46,163,107,0.45)'
// 因子副圖：一因子一 pane，畫方向化 z（z×direction，正=順風）
export type FactorPane = { label: string; direction: number; series: FactorHistPoint[] }
// 主圖疊加均線：由呼叫端以「完整史料」計算再裁切，視窗首日的均值才正確
export type MaLine = { label: string; color: string; points: { time: string; value: number }[] }

/* 量價分布關鍵價位（volume profile）：把視窗內每日成交量按當日 H–L 區間比例攤進價格
   bins，取 POC（最大量價位）與 70% 價值區頂/底。純視覺化確認層，非預測因子
   （依據 docs/research/PAPERS-KEYLEVELS.md §C）。 */
function volumeProfileLevels(data: Candle[]): { poc: number; vah: number; val: number } | null {
  const pts = data.filter((k) => (k.v ?? k.amt ?? 0) > 0)
  if (pts.length < 10) return null
  let lo = Infinity, hi = -Infinity
  for (const k of pts) { lo = Math.min(lo, k.l); hi = Math.max(hi, k.h) }
  if (!(hi > lo)) return null
  const N = 24, bw = (hi - lo) / N
  const vol = Array.from({ length: N }, () => 0)
  for (const k of pts) {
    const v = k.v ?? k.amt ?? 0
    const span = k.h - k.l
    const a = Math.max(0, Math.min(N - 1, Math.floor((k.l - lo) / bw)))
    if (span <= 0) { vol[a] += v; continue }
    const b = Math.max(a, Math.min(N - 1, Math.floor((k.h - lo) / bw - 1e-9)))
    for (let i = a; i <= b; i++) {
      const bLo = lo + i * bw
      const ov = Math.min(bLo + bw, k.h) - Math.max(bLo, k.l)
      if (ov > 0) vol[i] += v * (ov / span)
    }
  }
  const total = vol.reduce((s, x) => s + x, 0)
  if (total <= 0) return null
  let pocI = 0
  for (let i = 1; i < N; i++) if (vol[i] > vol[pocI]) pocI = i
  // 由 POC 向兩側擴張（每步取量較大側）直到涵蓋 70% 成交量
  let acc = vol[pocI], loI = pocI, hiI = pocI
  while (acc < total * 0.7 && (loI > 0 || hiI < N - 1)) {
    const dn = loI > 0 ? vol[loI - 1] : -1
    const up = hiI < N - 1 ? vol[hiI + 1] : -1
    if (up >= dn) { hiI++; acc += vol[hiI] } else { loI--; acc += vol[loI] }
  }
  return { poc: lo + (pocI + 0.5) * bw, vah: lo + (hiI + 1) * bw, val: lo + loI * bw }
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
}: {
  data: Candle[]
  height?: number
  volume?: boolean
  composite?: CompositePoint[]
  brokerFlow?: BrokerFlowPoint[]
  factorPanes?: FactorPane[]
  mas?: MaLine[]
  keyLevels?: boolean
}) {
  const box = useRef<HTMLDivElement>(null)
  const legend = useRef<HTMLDivElement>(null)
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
      const lv = volumeProfileLevels(data)
      if (lv) {
        candle.createPriceLine({ price: lv.poc, color: ACC, lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: `POC ${priceFmt(lv.poc)}` })
        candle.createPriceLine({ price: lv.vah, color: MUT, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: `VA頂 ${priceFmt(lv.vah)}` })
        candle.createPriceLine({ price: lv.val, color: MUT, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: `VA底 ${priceFmt(lv.val)}` })
      }
    }

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
      cumS = chart.addSeries(LineSeries, { priceScaleId: 'cum', color: ACC, lineWidth: 2, priceLineVisible: false, lastValueVisible: false }, pane)
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
      lg.style.opacity = '1'
      lg.innerHTML = html
    })
    const ro = new ResizeObserver(() => box.current && chart.applyOptions({ width: box.current.clientWidth }))
    ro.observe(el)
    return () => {
      ro.disconnect()
      chart.remove()
    }
  }, [data, height, volume, composite, brokerFlow, factorPanes, mas, keyLevels])
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
type ChartSettings = { range: number; maOn: number[]; levelsOn: boolean }
function loadChartSettings(): ChartSettings {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(CHART_SETTINGS_KEY) ?? 'null')
    if (v && typeof v === 'object') {
      const o = v as Record<string, unknown>
      return {
        range: typeof o.range === 'number' ? o.range : 0,
        maOn: Array.isArray(o.maOn) ? o.maOn.filter((x): x is number => typeof x === 'number') : [5, 20],
        levelsOn: typeof o.levelsOn === 'boolean' ? o.levelsOn : true,
      }
    }
  } catch {
    /* 壞資料：退回預設 */
  }
  return { range: 0, maOn: [5, 20], levelsOn: true }
}
const MA_DEFS: [number, string][] = [[5, '#f2c94c'], [10, '#5aa2f7'], [20, '#e578d1'], [60, '#9b8cff']]
export function RangeCandle({
  data,
  height,
  volume,
  composite,
  brokerFlow,
  factorPanes,
}: {
  data: Candle[]
  height?: number
  volume?: boolean
  composite?: CompositePoint[]
  brokerFlow?: BrokerFlowPoint[]
  factorPanes?: FactorPane[]
}) {
  const [range, setRange] = useState(() => loadChartSettings().range)
  const [maOn, setMaOn] = useState<number[]>(() => loadChartSettings().maOn)
  const [levelsOn, setLevelsOn] = useState(() => loadChartSettings().levelsOn)
  useEffect(() => {
    try {
      localStorage.setItem(CHART_SETTINGS_KEY, JSON.stringify({ range, maOn, levelsOn }))
    } catch {
      /* 隱私模式等寫入失敗：設定僅本次會話有效 */
    }
  }, [range, maOn, levelsOn])
  const toggleMa = (w: number) => setMaOn((cur) => (cur.includes(w) ? cur.filter((x) => x !== w) : [...cur, w]))
  // useMemo 維持陣列 identity 穩定——CandleChart 的 effect 以這些為 deps，
  // 每次 render 產生新陣列會導致父層任何 state 變動（如搜尋框打字）都整座圖砍掉重建。
  const shown = useMemo(() => (range > 0 ? data.slice(-range) : data), [data, range])
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
  return (
    <div>
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
        height={height}
        volume={volume}
        composite={compShown}
        brokerFlow={flowShown}
        factorPanes={fpShown}
        mas={masShown.length ? masShown : undefined}
        keyLevels={levelsOn}
      />
    </div>
  )
}
