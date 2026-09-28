// 自動畫線純函數（T-321）：轉折點 → 趨勢線／軌道線／水平支撐壓力／箱型／回歸通道／黃金切割／缺口／大量K棒。
// 規則與預設值出處見 docs/reports/auto-drawing-methods.md。
// 只描述「畫面上看得到的結構」讓使用者自己判讀：不進回測、不進 ML、不宣稱預測力
// （Zapranis & Tsinaslanidis 2012：自動偵測的 S/R 有趨勢中斷效果，但扣成本後無超額報酬）。
// 轉折點需要右側 span 根 K 棒確認，最後 span 根永遠不會是轉折點——這是看盤工具的確認延遲，
// 不是前視：pivots(data[:t]) 等於 pivots(data) 中「已確認」（i + span < t）的那些。
// 本檔刻意不 import 任何執行期模組，Node 的 strip-types 測試才能直接載入。
import type { Bar } from './indicators.ts'

/** 畫線參數集中於此，元件不得另寫數字。 */
export const DRAW = {
  /** 轉折點：左右各 span 根 K 棒內的最高／最低。短線 3、長線 10（日線常見的 fractal 寬度）。 */
  pivotSpan: { short: 3, long: 10 },
  /** 趨勢線只從最近幾個轉折點中挑兩點相連（太舊的點畫出的線對目前行情沒意義）。 */
  trendPivots: 6,
  /** 兩錨點至少相隔幾根 K 棒；太近的兩點斜率極不穩定。 */
  trendMinGap: 5,
  /** 延伸上限：b 之後延伸超過「兩錨點距離 × 此倍數」就視為過時不畫（TradingView Auto Trendlines 的規則）。 */
  trendMaxExtend: 2,
  /** 觸碰容忍度：價格距線在此比例內算「碰到」；兩錨點之間的 K 棒也不得越線超過此比例。 */
  touchTol: 0.01,
  /** 破線：收盤越過線超過此比例才算突破／跌破（台股慣例 3%）。 */
  breakTol: 0.03,
  /** 水平支撐壓力：轉折點價格相距此比例內歸為同一價位帶。 */
  levelClusterTol: 0.02,
  /** 同一價位帶至少被碰到幾次才畫。 */
  levelMinTouches: 2,
  /** 收盤上下方各畫幾條最近的支撐／壓力。 */
  levelsPerSide: 2,
  /** 回歸通道：中線 ± k 倍殘差標準差。 */
  channelSigma: 2,
  /** 回歸通道至少幾根 K 棒才畫。 */
  channelMinBars: 20,
  /** 箱型整理：連續 K 棒的（最高−最低）／最低 不超過此比例。 */
  boxMaxRange: 0.12,
  /** 箱型至少持續幾根 K 棒。 */
  boxMinBars: 15,
  /** 最多畫最近幾個箱型。 */
  boxMax: 2,
  /** 箱頂、箱底各至少被碰幾次才算箱型（Darvas 式；只碰一次的是單純的區間）。 */
  boxMinTouches: 2,
  /** 黃金切割：取最近 fibLookback 根 K 棒內的最高與最低為波段；台股軟體常用的比例。 */
  fibLookback: 120,
  fibRatios: [0.191, 0.382, 0.5, 0.618, 0.809],
  /** 跳空缺口：缺口至少佔價格此比例；最多列最近幾個未回補的。 */
  gapMinPct: 0.01,
  gapMax: 3,
  /** 大量K棒：量 ≥ volMult × 前 volAvg 日均量，且實體 ≥ bodyMinPct；最多列最近幾根。 */
  keyBar: { volAvg: 20, volMult: 2, bodyMinPct: 0.03, max: 3 },
} as const

export type Pivot = { i: number; kind: 'high' | 'low'; price: number }

/**
 * 轉折點（fractal）：高點＝左側 span 根的 high 都嚴格較低、右側 span 根都不高於它；低點對稱。
 * 左嚴格右寬鬆，平台（連續同價）只會標一次。依 i 由小到大回傳。
 */
export function pivots(bars: Bar[], span: number): Pivot[] {
  const out: Pivot[] = []
  for (let i = span; i + span < bars.length; i++) {
    let isHi = true
    let isLo = true
    for (let j = i - span; j <= i + span && (isHi || isLo); j++) {
      if (j === i) continue
      if (j < i ? bars[j].h >= bars[i].h : bars[j].h > bars[i].h) isHi = false
      if (j < i ? bars[j].l <= bars[i].l : bars[j].l < bars[i].l) isLo = false
    }
    if (isHi) out.push({ i, kind: 'high', price: bars[i].h })
    if (isLo) out.push({ i, kind: 'low', price: bars[i].l })
  }
  return out
}

export type TrendLine = {
  /** support＝連低點（價格在線上方）；resistance＝連高點（價格在線下方）。 */
  kind: 'support' | 'resistance'
  a: Pivot
  b: Pivot
  /** 每根 K 棒的價格變化（以 K 棒序數計，非日曆日）。 */
  slope: number
  /** 錨點 a 之後碰到線的轉折點數（含兩錨點）。 */
  touches: number
  /** 錨點 b 之後第一根收盤越線超過 breakTol 的 K 棒；null＝至今未破。 */
  brokenAt: number | null
}

/** The line's price at bar index i. */
export const lineAt = (t: Pick<TrendLine, 'a' | 'slope'>, i: number): number => t.a.price + t.slope * (i - t.a.i)

/**
 * 趨勢線：在最近 trendPivots 個同類轉折點中兩兩相連（延伸到今天不得超過錨點距離的 trendMaxExtend 倍），兩錨點之間不得有 K 棒實質穿越
 * （support：low 低於線超過容忍度；resistance：high 高於線超過容忍度）。
 * 排序：至今未破優先 → 觸碰次數多 → 錨點 b 較新 → 錨點 a 較新。每側回傳最佳一條，沒有合格就不回。
 */
export function trendLines(bars: Bar[], span: number, p: typeof DRAW = DRAW): TrendLine[] {
  const pv = pivots(bars, span)
  const out: TrendLine[] = []
  for (const kind of ['support', 'resistance'] as const) {
    const side = pv.filter((x) => x.kind === (kind === 'support' ? 'low' : 'high')).slice(-p.trendPivots)
    let best: TrendLine | null = null
    for (let x = 0; x < side.length; x++) {
      for (let y = x + 1; y < side.length; y++) {
        const a = side[x]
        const b = side[y]
        if (b.i - a.i < p.trendMinGap || bars.length - 1 - b.i > p.trendMaxExtend * (b.i - a.i)) continue
        const slope = (b.price - a.price) / (b.i - a.i)
        const t = { a, slope }
        let ok = true
        for (let i = a.i + 1; i < b.i && ok; i++) {
          const ln = lineAt(t, i)
          ok = kind === 'support' ? bars[i].l >= ln * (1 - p.touchTol) : bars[i].h <= ln * (1 + p.touchTol)
        }
        if (!ok) continue
        let brokenAt: number | null = null
        for (let i = b.i + 1; i < bars.length; i++) {
          const ln = lineAt(t, i)
          if (kind === 'support' ? bars[i].c < ln * (1 - p.breakTol) : bars[i].c > ln * (1 + p.breakTol)) {
            brokenAt = i
            break
          }
        }
        const end = brokenAt ?? bars.length
        const touches = side.filter((q) => q.i >= a.i && q.i < end && Math.abs(q.price - lineAt(t, q.i)) <= q.price * p.touchTol).length
        const cand: TrendLine = { kind, a, b, slope, touches, brokenAt }
        if (best === null || better(cand, best)) best = cand
      }
    }
    if (best) out.push(best)
  }
  return out
}

function better(c: TrendLine, d: TrendLine): boolean {
  if ((c.brokenAt === null) !== (d.brokenAt === null)) return c.brokenAt === null
  if (c.touches !== d.touches) return c.touches > d.touches
  if (c.b.i !== d.b.i) return c.b.i > d.b.i
  return c.a.i > d.a.i
}

export type Level = {
  kind: 'support' | 'resistance'
  /** 價位帶內轉折點價格的平均。 */
  price: number
  touches: number
  /** 價位帶內最後一次被碰到的 K 棒序數。 */
  lastI: number
}

/**
 * 水平支撐壓力：所有轉折點（高低點都算——壓力突破後常轉支撐）依價格排序，
 * 與目前價位帶平均相距 levelClusterTol 內就併入。碰到 levelMinTouches 次以上的帶，
 * 在最後收盤下方的是支撐、上方的是壓力，各取離收盤最近的 levelsPerSide 條。
 */
export function srLevels(bars: Bar[], span: number, p: typeof DRAW = DRAW): Level[] {
  if (!bars.length) return []
  const pv = [...pivots(bars, span)].sort((x, y) => x.price - y.price || x.i - y.i)
  const bands: { sum: number; n: number; lastI: number }[] = []
  for (const q of pv) {
    const cur = bands[bands.length - 1]
    if (cur && Math.abs(q.price - cur.sum / cur.n) <= (cur.sum / cur.n) * p.levelClusterTol) {
      cur.sum += q.price
      cur.n++
      cur.lastI = Math.max(cur.lastI, q.i)
    } else bands.push({ sum: q.price, n: 1, lastI: q.i })
  }
  const close = bars[bars.length - 1].c
  const lv = bands
    .filter((b) => b.n >= p.levelMinTouches)
    .map((b) => ({ price: b.sum / b.n, touches: b.n, lastI: b.lastI }))
  const sup = lv.filter((l) => l.price <= close).sort((x, y) => y.price - x.price).slice(0, p.levelsPerSide)
  const res = lv.filter((l) => l.price > close).sort((x, y) => x.price - y.price).slice(0, p.levelsPerSide)
  return [
    ...res.map((l) => ({ kind: 'resistance' as const, ...l })),
    ...sup.map((l) => ({ kind: 'support' as const, ...l })),
  ]
}

export type Channel = { i0: number; i1: number; mid0: number; mid1: number; width: number; slope: number }

/** 線性回歸通道：收盤對 K 棒序數做最小平方，上下軌＝中線 ± channelSigma × 殘差標準差。 */
export function regressionChannel(bars: Bar[], p: typeof DRAW = DRAW): Channel | null {
  const n = bars.length
  if (n < p.channelMinBars) return null
  let sx = 0, sy = 0, sxx = 0, sxy = 0
  for (let i = 0; i < n; i++) {
    sx += i
    sy += bars[i].c
    sxx += i * i
    sxy += i * bars[i].c
  }
  const slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
  const icpt = (sy - slope * sx) / n
  let ss = 0
  for (let i = 0; i < n; i++) ss += (bars[i].c - (icpt + slope * i)) ** 2
  const width = p.channelSigma * Math.sqrt(ss / n)
  return { i0: 0, i1: n - 1, mid0: icpt, mid1: icpt + slope * (n - 1), width, slope }
}

/**
 * 軌道線（平行通道）：趨勢線往反方向平移，穿過錨點 a 之後（到破線為止）離線最遠的 K 棒極值
 * ——support 取 high 最高者、resistance 取 low 最低者。回傳平移量（價格差，support 為正）。
 */
export function channelOffset(bars: Bar[], t: TrendLine): number {
  const end = t.brokenAt ?? bars.length
  let off = 0
  for (let i = t.a.i; i < end; i++) {
    const d = t.kind === 'support' ? bars[i].h - lineAt(t, i) : bars[i].l - lineAt(t, i)
    if (t.kind === 'support' ? d > off : d < off) off = d
  }
  return off
}

export type Box = { i0: number; i1: number; top: number; bottom: number; touchesTop: number; touchesBottom: number }

/**
 * 箱型整理：由左往右貪婪延伸——從 i 起只要區間（最高−最低）／最低 ≤ boxMaxRange 就繼續，
 * 長度達 boxMinBars 就記成一個箱子並從箱尾之後重新找，否則 i 往右一格。回傳最近 boxMax 個。
 * 碰頂／碰底＝high／low 落在箱頂／箱底 touchTol 內的 K 棒數，給使用者判斷箱緣有多「硬」。
 */
export function boxes(bars: Bar[], p: typeof DRAW = DRAW): Box[] {
  const out: Box[] = []
  let i = 0
  while (i < bars.length) {
    let hi = bars[i].h
    let lo = bars[i].l
    let j = i + 1
    while (j < bars.length) {
      const h = Math.max(hi, bars[j].h)
      const l = Math.min(lo, bars[j].l)
      if ((h - l) / l > p.boxMaxRange) break
      hi = h
      lo = l
      j++
    }
    let tt = 0, tb = 0
    for (let k = i; k < j; k++) {
      if (bars[k].h >= hi * (1 - p.touchTol)) tt++
      if (bars[k].l <= lo * (1 + p.touchTol)) tb++
    }
    if (j - i >= p.boxMinBars && tt >= p.boxMinTouches && tb >= p.boxMinTouches) {
      out.push({ i0: i, i1: j - 1, top: hi, bottom: lo, touchesTop: tt, touchesBottom: tb })
      i = j
    } else i++
  }
  return out.slice(-p.boxMax)
}

export type Fib = { hiI: number; loI: number; hi: number; lo: number; up: boolean; levels: { ratio: number; price: number }[] }

/**
 * 黃金切割率：最近 fibLookback 根內的最高、最低為波段兩端。低點在前＝上漲波段，回檔價位由高點往下量；
 * 高點在前＝下跌波段，反彈價位由低點往上量。高低相等（一字線）不畫。
 */
export function fibLevels(bars: Bar[], p: typeof DRAW = DRAW): Fib | null {
  const s = Math.max(0, bars.length - p.fibLookback)
  let hiI = -1, loI = -1
  for (let i = s; i < bars.length; i++) {
    if (hiI < 0 || bars[i].h > bars[hiI].h) hiI = i
    if (loI < 0 || bars[i].l < bars[loI].l) loI = i
  }
  if (hiI < 0 || !(bars[hiI].h > bars[loI].l)) return null
  const hi = bars[hiI].h, lo = bars[loI].l
  const up = loI < hiI
  const levels = p.fibRatios.map((r) => ({ ratio: r, price: up ? hi - r * (hi - lo) : lo + r * (hi - lo) }))
  return { hiI, loI, hi, lo, up, levels }
}

export type Gap = { i: number; up: boolean; top: number; bottom: number }

/**
 * 跳空缺口：上跳＝今日最低 > 昨日最高，區間 [昨高, 今低]；下跳對稱。之後任一根 K 棒
 * 完全穿過缺口（上跳：low ≤ 昨高；下跳：high ≥ 昨低）即為已回補、不再列出。回傳最近 gapMax 個未回補缺口。
 * 除權息造成的機械性缺口無法從 K 棒分辨，照樣列出（呼叫端若用還原價就不會出現）。
 */
export function openGaps(bars: Bar[], p: typeof DRAW = DRAW): Gap[] {
  const out: Gap[] = []
  for (let i = 1; i < bars.length; i++) {
    const prev = bars[i - 1], cur = bars[i]
    let g: Gap | null = null
    if (cur.l > prev.h && (cur.l - prev.h) / prev.h >= p.gapMinPct) g = { i, up: true, top: cur.l, bottom: prev.h }
    else if (cur.h < prev.l && (prev.l - cur.h) / prev.l >= p.gapMinPct) g = { i, up: false, top: prev.l, bottom: cur.h }
    if (!g) continue
    let filled = false
    for (let k = i + 1; k < bars.length && !filled; k++) filled = g.up ? bars[k].l <= g.bottom : bars[k].h >= g.top
    if (!filled) out.push(g)
  }
  return out.slice(-p.gapMax)
}

export type KeyBar = { i: number; high: number; low: number; up: boolean; volRatio: number }

/**
 * 大量K棒（關鍵K棒）：量 ≥ volMult × 前 volAvg 日均量（不含當日）且 |收−開|／開 ≥ bodyMinPct。
 * 其高點視為壓力、低點視為支撐（跌破低點＝換手失敗、站上高點＝換手成功）。回傳最近 max 根；無量資料回空。
 */
export function keyBars(bars: Bar[], p: typeof DRAW = DRAW): KeyBar[] {
  const kb = p.keyBar
  const vol = bars.map((b) => b.v ?? 0)
  const out: KeyBar[] = []
  let sum = 0
  for (let i = 0; i < bars.length; i++) {
    if (i >= kb.volAvg) {
      const avg = sum / kb.volAvg
      const b = bars[i]
      if (avg > 0 && vol[i] >= kb.volMult * avg && Math.abs(b.c - b.o) / b.o >= kb.bodyMinPct)
        out.push({ i, high: b.h, low: b.l, up: b.c > b.o, volRatio: vol[i] / avg })
      sum -= vol[i - kb.volAvg]
    }
    sum += vol[i]
  }
  return out.slice(-kb.max)
}

/** 前高／前低：最後收盤上方最近一個已確認轉折高點、下方最近一個已確認轉折低點（依時間由近往遠找）。 */
export function priorHighLow(bars: Bar[], span: number): { high: Pivot | null; low: Pivot | null } {
  if (!bars.length) return { high: null, low: null }
  const close = bars[bars.length - 1].c
  const pv = pivots(bars, span)
  let high: Pivot | null = null, low: Pivot | null = null
  for (let k = pv.length - 1; k >= 0 && (!high || !low); k--) {
    const q = pv[k]
    if (!high && q.kind === 'high' && q.price > close) high = q
    if (!low && q.kind === 'low' && q.price < close) low = q
  }
  return { high, low }
}
