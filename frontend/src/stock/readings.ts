// 儀表板面板的純衍生（SPEC-014 §9）：把 API 回來的真實序列整理成面板要顯示的列。
// 原則：輸入缺席就說缺席。null＝沒載到（讀取失敗／尚未載入）、[]＝合法零筆，兩者措辭不同；
// 任何一格都不以 0、示範數字或推估補位。全部是規則式描述，非預測訊號、非投資建議。
import type { BrokerFlowPoint, FactorHistGroup, FactorOption, InstRow, MarginRow, ScorecardFactor, ScreenerPool } from '../api'
import { crosses, kd, macd, sma, upDownVolume, volumeProfile, type Bar } from './indicators.ts'

/** 摘要規則用到的窗長與門檻；面板文字一律引用這裡的數字。 */
export const READING = {
  trendFast: 20, trendSlow: 60, kdHigh: 80, kdLow: 20,
  brokerDays: 5, instDays: 10, marginDays: 5, energyDays: 20, profileDays: 120, crossLookback: 60,
} as const

export type Tone = 'up' | 'down' | 'warn' | 'muted'
export type Reading = { key: string; label: string; value: string; tone: Tone }

const LOT = 1000 // 1 張＝1000 股；顯示層才換算，資料一律是股（SPEC-000）。
const signed = (v: number, unit: string) => `${v > 0 ? '+' : ''}${v.toLocaleString('en-US')} ${unit}`
const toneOf = (v: number): Tone => (v > 0 ? 'up' : v < 0 ? 'down' : 'muted')
const absent = (rows: unknown[] | null): string | null => (rows === null ? '未載入' : rows.length === 0 ? '零筆' : null)

export type ReadingInput = {
  bars: Bar[]
  brokerFlow: BrokerFlowPoint[] | null
  institutional: InstRow[] | null
  margin: MarginRow[] | null
}

/** 技術摘要：每一列都能對回一條寫在這裡的規則。 */
export function technicalReadings({ bars, brokerFlow, institutional, margin }: ReadingInput): Reading[] {
  const out: Reading[] = []
  const closes = bars.map((b) => b.c)
  const last = closes.length - 1

  const fast = sma(closes, READING.trendFast)[last] ?? null
  const slow = sma(closes, READING.trendSlow)[last] ?? null
  if (fast === null || slow === null) {
    out.push({ key: 'trend', label: '趨勢（均線）', value: `資料不足（需 ${READING.trendSlow} 日）`, tone: 'muted' })
  } else {
    const c = closes[last]
    const bull = c > fast && fast > slow
    const bear = c < fast && fast < slow
    out.push({
      key: 'trend', label: '趨勢（均線）',
      value: bull ? `多頭排列（收 > MA${READING.trendFast} > MA${READING.trendSlow}）` : bear ? `空頭排列（收 < MA${READING.trendFast} < MA${READING.trendSlow}）` : '均線糾結',
      tone: bull ? 'up' : bear ? 'down' : 'muted',
    })
  }

  const k = kd(bars)[last] ?? null
  if (k === null) out.push({ key: 'kd', label: '短線（KD）', value: '資料不足', tone: 'muted' })
  else {
    const zone = k.k >= READING.kdHigh ? '高檔區' : k.k <= READING.kdLow ? '低檔區' : k.k > k.d ? 'K 在 D 上' : 'K 在 D 下'
    out.push({
      key: 'kd', label: '短線（KD）', value: `K ${k.k.toFixed(0)}／D ${k.d.toFixed(0)} · ${zone}`,
      tone: k.k >= READING.kdHigh || k.k <= READING.kdLow ? 'warn' : k.k > k.d ? 'up' : 'down',
    })
  }

  const m = macd(closes)[last] ?? null
  if (m === null || m.osc === null) out.push({ key: 'macd', label: 'MACD', value: '資料不足', tone: 'muted' })
  else out.push({ key: 'macd', label: 'MACD', value: `OSC ${m.osc > 0 ? '+' : ''}${m.osc.toFixed(2)} · DIF 在訊號線${m.osc > 0 ? '上' : '下'}`, tone: toneOf(m.osc) })

  const bMissing = absent(brokerFlow)
  if (bMissing) out.push({ key: 'broker', label: `主力 ${READING.brokerDays} 日`, value: bMissing, tone: 'muted' })
  else {
    const net = Math.round(brokerFlow!.slice(-READING.brokerDays).reduce((s, r) => s + r.net, 0) / LOT)
    out.push({ key: 'broker', label: `主力 ${READING.brokerDays} 日`, value: signed(net, '張'), tone: toneOf(net) })
  }

  const iMissing = absent(institutional)
  if (iMissing) out.push({ key: 'inst', label: `法人 ${READING.instDays} 日`, value: iMissing, tone: 'muted' })
  else {
    const net = Math.round(institutional!.slice(-READING.instDays).reduce((s, r) => s + r.foreign_net + r.trust_net + r.dealer_net, 0) / LOT)
    out.push({ key: 'inst', label: `法人 ${READING.instDays} 日`, value: signed(net, '張'), tone: toneOf(net) })
  }

  const mMissing = absent(margin)
  if (mMissing) out.push({ key: 'margin', label: `融資 ${READING.marginDays} 日`, value: mMissing, tone: 'muted' })
  else {
    const rows = margin!
    const base = rows[Math.max(0, rows.length - 1 - READING.marginDays)]
    const chg = Math.round((rows[rows.length - 1].margin_balance - base.margin_balance) / LOT)
    out.push({ key: 'margin', label: `融資 ${READING.marginDays} 日`, value: signed(chg, '張'), tone: toneOf(chg) })
  }

  const vp = volumeProfile(bars.slice(-READING.profileDays))
  const px = (v: number) => (v >= 1000 ? v.toFixed(0) : v >= 100 ? v.toFixed(1) : v.toFixed(2))
  out.push({ key: 'support', label: '支撐（價值區底）', value: vp ? px(vp.val) : '資料不足', tone: vp ? 'down' : 'muted' })
  out.push({ key: 'resist', label: '壓力（價值區頂）', value: vp ? px(vp.vah) : '資料不足', tone: vp ? 'up' : 'muted' })
  out.push({ key: 'poc', label: '最大量價位', value: vp ? px(vp.poc) : '資料不足', tone: vp ? 'warn' : 'muted' })
  return out
}

export type CrossSummary = { golden: string[]; death: string[] }

/** 近 crossLookback 日 KD／MACD 的交叉日（日期字串來自 K 棒本身，不做位移）。 */
export function crossSummary(bars: Bar[]): { kd: CrossSummary; macd: CrossSummary } {
  const from = Math.max(0, bars.length - READING.crossLookback)
  const pick = (a: (number | null)[], b: (number | null)[]): CrossSummary => {
    const hits = crosses(a, b).filter((c) => c.i >= from)
    return { golden: hits.filter((c) => c.kind === 'golden').map((c) => bars[c.i].d), death: hits.filter((c) => c.kind === 'death').map((c) => bars[c.i].d) }
  }
  const kdPts = kd(bars)
  const macdPts = macd(bars.map((b) => b.c))
  return {
    kd: pick(kdPts.map((p) => p?.k ?? null), kdPts.map((p) => p?.d ?? null)),
    macd: pick(macdPts.map((p) => p?.dif ?? null), macdPts.map((p) => p?.dea ?? null)),
  }
}

/** 量能多空（近 energyDays 日）：上漲日成交量佔「上漲＋下跌」的比例；無量或無漲跌日回 null。 */
export function energyShare(bars: Bar[]): { up: number; upDays: number; downDays: number; days: number } | null {
  const e = upDownVolume(bars, READING.energyDays)
  if (!e || e.upVol + e.downVol <= 0) return null
  return { up: e.upVol / (e.upVol + e.downVol), upDays: e.upDays, downDays: e.downDays, days: e.days }
}

export type FactorCard = { as_of: string | null; factors: ScorecardFactor[] }

/**
 * 由「各因子最新一筆物化值」組出因子卡。刻意不用 /api/stocks/{sid}/factors：那個端點以 stock_id
 * 掃整張 factor_values（主鍵是 factor 開頭），不在因子宇宙的 ETF 會掃到數分鐘；逐因子查則走主鍵，毫秒級。
 * 沒有物化值的因子直接不列，不補 0；類別取因子名稱的前綴（與後端一致）。
 */
export function factorCardFrom(history: FactorHistGroup[], options: FactorOption[]): FactorCard {
  const latest = new Map(history.map((g) => [g.factor, g.series[g.series.length - 1]]))
  const factors: ScorecardFactor[] = []
  let asOf: string | null = null
  for (const o of options) {
    const p = latest.get(o.factor)
    if (!p) continue
    if (asOf === null || p.date > asOf) asOf = p.date
    factors.push({
      factor: o.factor, zh: o.zh, category: o.factor.split('.')[0], value: p.value, z: p.z, pct: p.pct,
      direction: o.direction, signed_z: p.z === null ? null : p.z * o.direction,
    })
  }
  return { as_of: asOf, factors }
}

export const FACTOR_CATEGORIES = [
  { category: 'price', label: '價格動能' },
  { category: 'volume', label: '量能' },
  { category: 'chip', label: '籌碼' },
  { category: 'fundamental', label: '基本面' },
] as const

export type CategoryScore = { category: string; label: string; value: number | null; n: number }

/**
 * 各類因子的「方向化百分位」平均：direction<0 的因子取 1−pct，數值越高＝越站在因子方向。
 * 這是橫斷面相對定位，不是預測力（D-020）；沒有百分位的因子不計入，整類沒有就回 null。
 */
export function categoryScores(factors: ScorecardFactor[]): CategoryScore[] {
  return FACTOR_CATEGORIES.map(({ category, label }) => {
    const pcts = factors
      .filter((f) => f.category === category && f.pct !== null)
      .map((f) => (f.direction >= 0 ? (f.pct as number) : 1 - (f.pct as number)))
    return { category, label, value: pcts.length ? pcts.reduce((s, x) => s + x, 0) / pcts.length : null, n: pcts.length }
  })
}

/** 風險雷達的軸：直接取該因子的原始橫斷面百分位（越高＝該項數值在全市場越高），不加權、不合成。 */
export const RISK_AXES = [
  { factor: 'price.vol_20d', label: '波動率' },
  { factor: 'volume.turnover_z_20d', label: '量能熱度' },
  { factor: 'chip.margin_velocity_5d', label: '融資增速' },
  { factor: 'chip.short_margin_ratio', label: '券資比' },
  { factor: 'price.high_52w', label: '近 52 週高' },
] as const

export function riskAxes(factors: ScorecardFactor[]): { label: string; value: number | null }[] {
  return RISK_AXES.map(({ factor, label }) => ({ label, value: factors.find((f) => f.factor === factor)?.pct ?? null }))
}

export type PoolSignal = { key: string; name: string; state: 'in' | 'out' | 'unknown'; streak: number | null; total: number }

/**
 * 這檔是否在各選股池。API 每池只回前 N 名：名單被截斷時找不到只能說「未知」，
 * 名單完整才能說「不在池內」。代號一律字串比對。
 */
export function poolSignals(pools: ScreenerPool[], sid: string): PoolSignal[] {
  return pools.map((p) => {
    const hit = p.members.find((m) => m.stock_id === sid)
    const state = hit ? 'in' : p.total > p.members.length ? 'unknown' : 'out'
    return { key: p.key, name: p.name, state, streak: hit ? hit.streak : null, total: p.total }
  })
}

export type CoverageInput = { label: string; rows: number; target: number; asOf: string | null; live: boolean }
export type CoverageRow = CoverageInput & { state: 'ok' | 'empty' | 'failed'; ratio: number | null }

/** 資料完整度：讀取失敗、零筆、有資料三種狀態分開；失敗不算 0%。 */
export function coverageRows(inputs: CoverageInput[]): CoverageRow[] {
  return inputs.map((c) => {
    const state = !c.live ? 'failed' : c.rows === 0 ? 'empty' : 'ok'
    return { ...c, state, ratio: state === 'failed' ? null : Math.min(1, c.rows / c.target) }
  })
}
