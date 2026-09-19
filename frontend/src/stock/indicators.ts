// 技術指標純函數（SPEC-014 §9）：只吃傳入的 K 棒、只用 ≤ 當日的資料，故 f(data[:t]) === f(data)[:t]。
// 這一層是「看盤用的描述」，不是因子：不進回測、不進 ML，也不宣稱任何預測力。
// 本檔刻意不 import 任何執行期模組，Node 的 strip-types 測試才能直接載入。

/** One daily bar; volume is in shares (股), never lots (張). */
export type Bar = { d: string; o: number; h: number; l: number; c: number; v?: number; amt?: number }

/** 指標參數（台股看盤慣例）。集中於此，元件不得另寫數字。 */
export const MA_WINDOWS = [5, 10, 20, 60] as const
export const KD_PARAMS = { n: 9, smooth: 3 } as const
export const MACD_PARAMS = { fast: 12, slow: 26, signal: 9 } as const
/** 量價分布：價格切成幾格、價值區涵蓋多少成交量、至少幾根 K 棒才算。 */
export const PROFILE = { bins: 24, valueArea: 0.7, minBars: 10 } as const

/** Simple moving average; null until the window is full. */
export function sma(values: number[], window: number): (number | null)[] {
  const out: (number | null)[] = []
  let sum = 0
  for (let i = 0; i < values.length; i++) {
    sum += values[i]
    if (i >= window) sum -= values[i - window]
    out.push(i >= window - 1 ? sum / window : null)
  }
  return out
}

export type KdPoint = { k: number; d: number }

/**
 * 隨機指標 KD：RSV＝(收−n日低)/(n日高−n日低)×100，K、D 為 RSV 的 1/smooth 指數平滑，起始值 50。
 * n 日高低相等（一字線）時 RSV 取 50，不除以零。窗未滿回 null。
 */
export function kd(bars: Bar[], n: number = KD_PARAMS.n, smooth: number = KD_PARAMS.smooth): (KdPoint | null)[] {
  const out: (KdPoint | null)[] = []
  let k = 50
  let d = 50
  for (let i = 0; i < bars.length; i++) {
    if (i < n - 1) {
      out.push(null)
      continue
    }
    let hi = -Infinity
    let lo = Infinity
    for (let j = i - n + 1; j <= i; j++) {
      hi = Math.max(hi, bars[j].h)
      lo = Math.min(lo, bars[j].l)
    }
    const rsv = hi > lo ? ((bars[i].c - lo) / (hi - lo)) * 100 : 50
    k = ((smooth - 1) / smooth) * k + rsv / smooth
    d = ((smooth - 1) / smooth) * d + k / smooth
    out.push({ k, d })
  }
  return out
}

export type MacdPoint = { dif: number; dea: number | null; osc: number | null }

/**
 * MACD（以收盤價計）：DIF＝EMA(fast)−EMA(slow)，MACD 訊號線＝DIF 的 EMA(signal)，OSC＝DIF−訊號線。
 * EMA 以第一筆為種子；slow 窗未滿前 DIF 為暖機值，一律回 null 而不是畫出不穩定的線。
 */
export function macd(
  closes: number[],
  fast: number = MACD_PARAMS.fast,
  slow: number = MACD_PARAMS.slow,
  signal: number = MACD_PARAMS.signal,
): (MacdPoint | null)[] {
  const out: (MacdPoint | null)[] = []
  const aF = 2 / (fast + 1)
  const aS = 2 / (slow + 1)
  const aSig = 2 / (signal + 1)
  let eF = 0
  let eS = 0
  let dea: number | null = null
  let seen = 0
  for (let i = 0; i < closes.length; i++) {
    eF = i === 0 ? closes[i] : eF + aF * (closes[i] - eF)
    eS = i === 0 ? closes[i] : eS + aS * (closes[i] - eS)
    if (i < slow - 1) {
      out.push(null)
      continue
    }
    const dif = eF - eS
    dea = dea === null ? dif : dea + aSig * (dif - dea)
    seen++
    const ready = seen >= signal
    out.push({ dif, dea: ready ? dea : null, osc: ready ? dif - dea : null })
  }
  return out
}

export type Cross = { i: number; kind: 'golden' | 'death' }

/** Where series a crosses series b; a bar next to a warm-up gap is never reported as a cross. */
export function crosses(a: (number | null)[], b: (number | null)[]): Cross[] {
  const out: Cross[] = []
  for (let i = 1; i < Math.min(a.length, b.length); i++) {
    const [a0, b0, a1, b1] = [a[i - 1], b[i - 1], a[i], b[i]]
    if (a0 == null || b0 == null || a1 == null || b1 == null) continue
    if (a0 <= b0 && a1 > b1) out.push({ i, kind: 'golden' })
    else if (a0 >= b0 && a1 < b1) out.push({ i, kind: 'death' })
  }
  return out
}

export type VolumeProfile = {
  lo: number
  hi: number
  binWidth: number
  /** Volume per price bin, lowest price first. */
  vol: number[]
  pocIndex: number
  vaLoIndex: number
  vaHiIndex: number
  /** POC＝最大量價位；VAH／VAL＝涵蓋 valueArea 成交量的價值區頂與底。 */
  poc: number
  vah: number
  val: number
}

/**
 * 量價分布（volume profile）：每日成交量依當日 H–L 區間比例攤進價格格子。
 * 純視覺化確認層，非預測因子（依據 docs/research/PAPERS-KEYLEVELS.md §C）。K 棒太少或無量回 null。
 */
export function volumeProfile(bars: Bar[], bins: number = PROFILE.bins): VolumeProfile | null {
  const pts = bars.filter((k) => (k.v ?? k.amt ?? 0) > 0)
  if (pts.length < PROFILE.minBars) return null
  let lo = Infinity
  let hi = -Infinity
  for (const k of pts) {
    lo = Math.min(lo, k.l)
    hi = Math.max(hi, k.h)
  }
  if (!(hi > lo)) return null
  const bw = (hi - lo) / bins
  const vol = Array.from({ length: bins }, () => 0)
  for (const k of pts) {
    const v = k.v ?? k.amt ?? 0
    const span = k.h - k.l
    const a = Math.max(0, Math.min(bins - 1, Math.floor((k.l - lo) / bw)))
    if (span <= 0) {
      vol[a] += v
      continue
    }
    const b = Math.max(a, Math.min(bins - 1, Math.floor((k.h - lo) / bw - 1e-9)))
    for (let i = a; i <= b; i++) {
      const bLo = lo + i * bw
      const ov = Math.min(bLo + bw, k.h) - Math.max(bLo, k.l)
      if (ov > 0) vol[i] += v * (ov / span)
    }
  }
  const total = vol.reduce((s, x) => s + x, 0)
  if (total <= 0) return null
  let pocIndex = 0
  for (let i = 1; i < bins; i++) if (vol[i] > vol[pocIndex]) pocIndex = i
  // 由 POC 向兩側擴張（每步取量較大側）直到涵蓋價值區比例的成交量
  let acc = vol[pocIndex]
  let loI = pocIndex
  let hiI = pocIndex
  while (acc < total * PROFILE.valueArea && (loI > 0 || hiI < bins - 1)) {
    const dn = loI > 0 ? vol[loI - 1] : -1
    const up = hiI < bins - 1 ? vol[hiI + 1] : -1
    if (up >= dn) {
      hiI++
      acc += vol[hiI]
    } else {
      loI--
      acc += vol[loI]
    }
  }
  return {
    lo, hi, binWidth: bw, vol, pocIndex, vaLoIndex: loI, vaHiIndex: hiI,
    poc: lo + (pocIndex + 0.5) * bw, vah: lo + (hiI + 1) * bw, val: lo + loI * bw,
  }
}

export type UpDownVolume = {
  days: number; upDays: number; downDays: number; flatDays: number
  upVol: number; downVol: number; flatVol: number
}

/** 近 n 日量能多空：每日收盤與「前一日收盤」比較後，把當日成交量歸到上漲／下跌／平盤。 */
export function upDownVolume(bars: Bar[], n: number): UpDownVolume | null {
  if (bars.length < 2) return null
  const out: UpDownVolume = { days: 0, upDays: 0, downDays: 0, flatDays: 0, upVol: 0, downVol: 0, flatVol: 0 }
  for (let i = Math.max(1, bars.length - n); i < bars.length; i++) {
    const v = bars[i].v ?? 0
    const diff = bars[i].c - bars[i - 1].c
    out.days++
    if (diff > 0) { out.upDays++; out.upVol += v }
    else if (diff < 0) { out.downDays++; out.downVol += v }
    else { out.flatDays++; out.flatVol += v }
  }
  return out
}
