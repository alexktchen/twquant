// 持股出場檢查（D-116）：把伺服器回的規則狀態，加上「只存在這台瀏覽器的買進價」，合成一句判斷。
// 三態分開：sell／hold／unknown。算不出來一律 unknown——「沒有賣出訊號」與「沒辦法判斷」
// 在畫面上不得長得一樣。

export type ExitRule = {
  code: string
  label: string
  kind: 'stop_loss' | 'below_ma' | 'score_below'
  triggered: boolean | null
  note: string
  pct?: number | null
  window?: number
  reference?: number
  threshold?: number | null
  value?: number
  score_date?: string
}
export type ExitStock = { stock_id: string; name: string | null; date: string | null; close: number | null; rules: ExitRule[] }
export type ExitCheckResponse = { mode: 'research'; stocks: ExitStock[] }
export type Verdict = { state: 'sell' | 'hold' | 'unknown'; detail: string }

const pct = (value: number): string => `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}%`

/** One rule's verdict. Stop loss is the only rule decided here; the rest come from the server. */
export function ruleVerdict(rule: ExitRule, close: number | null, entryPrice: number | null): Verdict {
  if (rule.kind === 'stop_loss') {
    if (entryPrice === null) return { state: 'unknown', detail: '未填買進價' }
    if (close === null || rule.pct == null) return { state: 'unknown', detail: '沒有收盤價' }
    const change = close / entryPrice - 1
    // 與回測引擎同一個不等式（<=）：恰好跌到門檻也算觸發。
    return { state: change <= -rule.pct ? 'sell' : 'hold', detail: `距買進價 ${pct(change)}（門檻 ${pct(-rule.pct)}）` }
  }
  if (rule.triggered === null) return { state: 'unknown', detail: rule.note || '無法判斷' }
  const detail = rule.kind === 'below_ma'
    ? `${rule.window} 日線 ${rule.reference?.toFixed(2) ?? '—'}`
    : `分位 ${rule.value?.toFixed(2) ?? '—'}（門檻 ${rule.threshold ?? '—'}）`
  return { state: rule.triggered ? 'sell' : 'hold', detail }
}

/** Rule codes by verdict. Unknowns are listed on their own so they are never read as "fine". */
export function summarize(rules: ExitRule[], close: number | null, entryPrice: number | null): Record<Verdict['state'], string[]> {
  const out: Record<Verdict['state'], string[]> = { sell: [], unknown: [], hold: [] }
  for (const rule of rules) out[ruleVerdict(rule, close, entryPrice).state].push(rule.code)
  return out
}
