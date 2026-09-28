// 自動畫線 → 圖層（T-321）：把 drawings.ts 的偵測結果（K 棒序數）轉成圖表座標、配色與判讀清單。
import type { Candle } from '../api'
import {
  DRAW, boxes, channelOffset, fibLevels, keyBars, lineAt, openGaps, priorHighLow, regressionChannel, srLevels, trendLines, type TrendLine,
} from './drawings.ts'
import { priceFmt } from '../theme'

export type DrawKind = 'trend' | 'levels' | 'box' | 'gap' | 'fib' | 'reg'
export const DRAW_KINDS: { kind: DrawKind; label: string; title: string }[] = [
  { kind: 'trend', label: '趨勢線', title: '連最近的轉折低點（支撐）／高點（壓力），並畫過另一側極值的平行軌道線；粗線＝長波段、細線＝短波段；收盤越線 3% 算破線（改虛線）' },
  { kind: 'levels', label: '支撐壓力', title: '轉折點價位聚集處（碰 2 次以上）＋前高前低＋大量K棒高低點；收盤上方＝壓力、下方＝支撐' },
  { kind: 'box', label: '箱型', title: '連續 15 根以上 K 棒擠在 12% 區間內、上下緣各碰 2 次以上的盤整區' },
  { kind: 'gap', label: '缺口', title: '尚未回補的跳空缺口（≥1%）：上跳缺口＝支撐區、下跳缺口＝壓力區' },
  { kind: 'fib', label: '黃金切割', title: '視窗內最高與最低點構成的波段，0.191／0.382／0.5／0.618／0.809 回檔或反彈價位' },
  { kind: 'reg', label: '回歸通道', title: '收盤價線性回歸中線 ±2 倍標準差' },
]
export const DEFAULT_DRAW_ON: DrawKind[] = ['trend', 'levels', 'box']

const SUP = '#38bdf8'
const RES = '#fb7185'
const BOX = '#facc15'
const FIB = '#94a3b8'
const REG = '#a78bfa'
const KEY = '#c084fc'
const rgba = (hex: string, a: number) => `rgba(${parseInt(hex.slice(1, 3), 16)},${parseInt(hex.slice(3, 5), 16)},${parseInt(hex.slice(5, 7), 16)},${a})`

export type LineStyleName = 'solid' | 'dashed' | 'dotted'
export type DrawSeg = { key: string; color: string; width: 1 | 2; style: LineStyleName; points: { time: string; value: number }[] }
export type DrawLevel = { price: number; label: string; color: string; style: LineStyleName }
export type DrawRect = { t0: string; t1: string; top: number; bottom: number; fill: string; border: string; label: string }
export type Drawings = { segs: DrawSeg[]; levels: DrawLevel[]; rects: DrawRect[] }
/** 判讀清單的一列：價位＝該線在最後一根 K 棒的價格，dist＝相對最後收盤的距離。 */
export type DrawNote = { color: string; label: string; price: number; dist: number; detail: string }

export function buildDrawings(bars: Candle[], on: DrawKind[]): { drawings: Drawings; notes: DrawNote[] } {
  const segs: DrawSeg[] = []
  const levels: DrawLevel[] = []
  const rects: DrawRect[] = []
  const notes: DrawNote[] = []
  const n = bars.length
  if (n < 2) return { drawings: { segs, levels, rects }, notes }
  const last = n - 1
  const close = bars[last].c
  const t = (i: number) => bars[i].d
  const note = (color: string, label: string, price: number, detail: string) =>
    notes.push({ color, label, price, dist: price / close - 1, detail })

  if (on.includes('trend')) {
    for (const [tier, span, width] of [['短', DRAW.pivotSpan.short, 1], ['長', DRAW.pivotSpan.long, 2]] as const) {
      for (const tl of trendLines(bars, span)) {
        // 長短波段挑到同一條線就只畫一次
        if (tier === '長' && segs.some((s) => s.key === segKey(tl))) continue
        const color = tl.kind === 'support' ? SUP : RES
        const style: LineStyleName = tl.brokenAt === null ? 'solid' : 'dashed'
        const name = `${tier}線${tl.slope >= 0 ? '上升' : '下降'}${tl.kind === 'support' ? '支撐' : '壓力'}線`
        segs.push({ key: segKey(tl), color, width, style, points: [{ time: t(tl.a.i), value: tl.a.price }, { time: t(last), value: lineAt(tl, last) }] })
        const off = channelOffset(bars, tl)
        if (off !== 0) {
          segs.push({ key: `${segKey(tl)}-ch`, color: rgba(color, 0.55), width: 1, style: 'dashed', points: [{ time: t(tl.a.i), value: tl.a.price + off }, { time: t(last), value: lineAt(tl, last) + off }] })
        }
        const wrongSide = tl.kind === 'support' ? close < lineAt(tl, last) : close > lineAt(tl, last)
        const broke = tl.brokenAt !== null
          ? `，${t(tl.brokenAt)} 已${tl.kind === 'support' ? '跌破' : '突破'}`
          : wrongSide ? `，收盤已${tl.kind === 'support' ? '在線下' : '在線上'}但未達 ${DRAW.breakTol * 100}% 破線` : ''
        note(color, name, lineAt(tl, last), `${t(tl.a.i)}→${t(tl.b.i)}，碰 ${tl.touches} 次${broke}`)
        if (off !== 0) note(rgba(color, 0.55), `${name}的軌道線`, lineAt(tl, last) + off, '過另一側最遠點的平行線')
      }
    }
  }

  if (on.includes('levels')) {
    for (const lv of srLevels(bars, DRAW.pivotSpan.short)) {
      const color = lv.kind === 'support' ? SUP : RES
      const name = lv.kind === 'support' ? '支撐' : '壓力'
      levels.push({ price: lv.price, label: `${name} ×${lv.touches}`, color, style: 'dashed' })
      note(color, `水平${name}`, lv.price, `碰 ${lv.touches} 次，最後一次 ${t(lv.lastI)}`)
    }
    const ph = priorHighLow(bars, DRAW.pivotSpan.short)
    if (ph.high) {
      levels.push({ price: ph.high.price, label: '前高', color: RES, style: 'dotted' })
      note(RES, '前高', ph.high.price, t(ph.high.i))
    }
    if (ph.low) {
      levels.push({ price: ph.low.price, label: '前低', color: SUP, style: 'dotted' })
      note(SUP, '前低', ph.low.price, t(ph.low.i))
    }
    for (const kb of keyBars(bars)) {
      if (kb.i >= last) continue // 最後一根本身是大量K：線段只有一個時間點（LineSeries 不收同時間兩點），明天起才畫
      const tag = `大量K ${t(kb.i).slice(5)}`
      segs.push({ key: `kb-h-${kb.i}`, color: KEY, width: 1, style: 'dotted', points: [{ time: t(kb.i), value: kb.high }, { time: t(last), value: kb.high }] })
      segs.push({ key: `kb-l-${kb.i}`, color: KEY, width: 1, style: 'dotted', points: [{ time: t(kb.i), value: kb.low }, { time: t(last), value: kb.low }] })
      const state = close > kb.high ? '已站上高點（換手成功）' : close < kb.low ? '已跌破低點（換手失敗）' : '仍在高低點之間'
      note(KEY, `${tag} 高點`, kb.high, `${kb.up ? '紅' : '黑'}K，量 ${kb.volRatio.toFixed(1)} 倍均量，${state}`)
      note(KEY, `${tag} 低點`, kb.low, '')
    }
  }

  if (on.includes('box')) {
    for (const bx of boxes(bars)) {
      const live = bx.i1 === last
      const where = close > bx.top ? '已突破箱頂' : close < bx.bottom ? '已跌破箱底' : live ? '仍在箱內' : ''
      rects.push({ t0: t(bx.i0), t1: t(bx.i1), top: bx.top, bottom: bx.bottom, fill: rgba(BOX, 0.07), border: rgba(BOX, 0.8), label: `箱 ${priceFmt(bx.bottom)}–${priceFmt(bx.top)}` })
      note(BOX, '箱頂', bx.top, `${t(bx.i0)}–${t(bx.i1)}，碰頂 ${bx.touchesTop} 次${where ? '，' + where : ''}`)
      note(BOX, '箱底', bx.bottom, `碰底 ${bx.touchesBottom} 次`)
    }
  }

  if (on.includes('gap')) {
    for (const g of openGaps(bars)) {
      const color = g.up ? SUP : RES
      rects.push({ t0: t(g.i - 1), t1: t(last), top: g.top, bottom: g.bottom, fill: rgba(color, 0.12), border: rgba(color, 0.5), label: g.up ? '上跳缺口' : '下跳缺口' })
      note(color, g.up ? '上跳缺口（支撐區）' : '下跳缺口（壓力區）', g.up ? g.top : g.bottom, `${t(g.i)}，${priceFmt(g.bottom)}–${priceFmt(g.top)} 未回補`)
    }
  }

  if (on.includes('fib')) {
    const f = fibLevels(bars)
    if (f) {
      for (const lv of f.levels) {
        levels.push({ price: lv.price, label: `${lv.ratio}`, color: FIB, style: 'dotted' })
        note(FIB, `黃金切割 ${lv.ratio}`, lv.price, f.up ? `上漲波段 ${t(f.loI)}→${t(f.hiI)} 的回檔` : `下跌波段 ${t(f.hiI)}→${t(f.loI)} 的反彈`)
      }
    }
  }

  if (on.includes('reg')) {
    const ch = regressionChannel(bars)
    if (ch) {
      for (const [k, d, style] of [['mid', 0, 'dashed'], ['top', ch.width, 'solid'], ['bot', -ch.width, 'solid']] as const) {
        segs.push({ key: `reg-${k}`, color: REG, width: 1, style, points: [{ time: t(ch.i0), value: ch.mid0 + d }, { time: t(ch.i1), value: ch.mid1 + d }] })
      }
      note(REG, '回歸通道上軌', ch.mid1 + ch.width, `視窗 ${n} 根，±${DRAW.channelSigma}σ`)
      note(REG, '回歸通道中線', ch.mid1, '')
      note(REG, '回歸通道下軌', ch.mid1 - ch.width, '')
    }
  }

  notes.sort((a, b) => b.price - a.price)
  return { drawings: { segs, levels, rects }, notes }
}

const segKey = (tl: TrendLine) => `tl-${tl.kind}-${tl.a.i}-${tl.b.i}`
