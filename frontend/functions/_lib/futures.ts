// 小台模擬（T-450／D-220）：serving.futures_paper 一個交易日一列，payload 是網站要的整份狀態。
// 寫入者是使用者電腦上的 research/momentum_scalping/scripts/fubon_live.py（富邦即時行情；盤中每 15 秒覆蓋、收盤定稿）。
// 富邦行情不得轉傳 → 兩支端點都只給 admin，而且不進 60 秒暫存（盤中每 15 秒就變，暫存會讓畫面落後）。

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/

/** ?day= 是不是 YYYY-MM-DD（只檢查格式，有沒有那一天交給資料庫）。 */
export const dayOk = (day: string): boolean => DAY_RE.test(day)

/** 還沒有任何紀錄：表還沒建，或電腦上的程式還沒寫過任何一天。 */
export const NOT_READY = '還沒有小台模擬紀錄：電腦上的錄製程式（fubon_live.py --upload）開盤後就會寫進來。'

/** 這天沒有秒級資料：只有逐筆的日子才有（富邦即時錄到的、期交所近 30 日逐筆重算的），而且收盤後才寫。 */
export const NO_SECONDS = '這天沒有秒級資料（只有 1 分 K）'

/**
 * 網友原版的 1 分 K 估算（T-462／D-220 修訂九，使用者 2026-10-29：「可以把台指期的復盤往前半年的賺賠都標出來嗎？我只要網友的策略」）：
 * serving.futures_ms_estimate 一個商品、一天一列（total、n、trades）。只有沒有真實資料（逐筆模擬單）的日子才用它。
 */
export type EstimateRow = { day: string; total: number | null }
export type DayRow = { day: string; source: string; status: string; total: number | null; match: boolean | null; ms_total: number | null; ms_est?: boolean }

/** 日期清單併進估算：真實資料的日子優先（估算不蓋過真實的），新到舊，最多 limit 天。 */
export function withEstimates(real: readonly DayRow[], est: readonly EstimateRow[], limit: number): DayRow[] {
  const have = new Set(real.map((r) => r.day))
  const extra: DayRow[] = est
    .filter((e) => !have.has(e.day))
    .map((e) => ({ day: e.day, source: 'minute_estimate', status: 'closed', total: null, match: null, ms_total: e.total, ms_est: true }))
  return [...real, ...extra].sort((a, b) => (a.day < b.day ? 1 : a.day > b.day ? -1 : 0)).slice(0, limit)
}

/** 日期清單一次最多給幾天（約一年的交易日）。 */
export const DAYS_LIMIT = 250

/** 大台、微台行情（T-451）：期交所代號。網站只放這兩個（小台在模擬單那邊）。 */
export const QUOTE_PRODUCTS = ['TX', 'TMF'] as const
export type QuoteProduct = (typeof QUOTE_PRODUCTS)[number]

/** ?product= 是不是大台或微台。 */
export const productOk = (p: string | null): p is QuoteProduct => p !== null && (QUOTE_PRODUCTS as readonly string[]).includes(p)

/** 盤口與大單（T-455，/api/futures/flow）：三個商品都有；小台在 futures_paper，大台、微台在 futures_quotes。 */
export const FLOW_PRODUCTS = ['MTX', 'TX', 'TMF'] as const
export type FlowProduct = (typeof FLOW_PRODUCTS)[number]
export const flowProductOk = (p: string | null): p is FlowProduct => p !== null && (FLOW_PRODUCTS as readonly string[]).includes(p)

/** 大單門檻（一筆幾口以上）：網站可切；錄製程式記的是 5 口以上，所以最低 5。 */
export const BIG_MIN_FLOOR = 5

/** 這一天沒有盤口資料：只有富邦即時錄到的日子才有（期交所重算的沒有買賣價與五檔），而且 2026-10-28 之後的錄製程式才記。 */
export const NO_FLOW = '這一天沒有盤口資料：只有 2026-10-28 之後、富邦即時錄到的日子才有（期交所重算的沒有買賣價與五檔）。'

/** 還沒有大台／微台行情：電腦上的錄製程式開盤後、或 --replay-quotes 補過去的日子後才會有。 */
export const QUOTES_NOT_READY = '還沒有這個商品的行情：電腦上的錄製程式（fubon_live.py --upload）開盤後就會寫進來。'
