// 盤中即時量價（T-347）：證交所「基本市況報導」的 getStockInfo，約 5 秒更新一次。
// 不是正式開放的介面：格式不對就丟錯（端點回 503），網站照樣顯示資料庫的收盤價。純函數，供 node 單元測試。
import { SID_RE } from './picks.ts'

export const MIS_URL = 'https://mis.twse.com.tw/stock/api/getStockInfo.jsp'
/** 一次問幾檔：每檔上市、上櫃各問一個頻道。2026-10-13 實測 75 檔（150 個頻道）可以、100 檔（200 個）被拒，留餘裕取 50。 */
export const MIS_BATCH = 50
/** 一次最多查幾檔（＝追蹤股上限）。 */
export const QUOTE_MAX = 100

export type Quote = {
  sid: string
  /** 這份報價是哪一天（YYYY-MM-DD）；假日問到的是上一個交易日 */
  date: string
  /** 報價時間 HH:MM:SS */
  time: string | null
  open: number | null
  high: number | null
  low: number | null
  /** 最近一筆成交價；還沒成交 → null */
  last: number | null
  /** 昨收（今天的漲跌參考價） */
  prev: number | null
  /** 今天累計成交量，單位「股」（證交所給「張」，這裡 ×1000，與資料庫一致） */
  volume: number | null
  limit_up: number | null
  limit_down: number | null
  /** 收盤前集中撮合（13:25–13:30）：證交所不給即時價，last 是撮合前最後一筆成交 */
  auction: boolean
}

/** 代號清單：逗號分隔、去重複、格式正確、1～QUOTE_MAX 檔；不合格 → null。 */
export function parseSids(raw: string | null): string[] | null {
  const sids = [...new Set((raw ?? '').split(',').map((s) => s.trim()).filter(Boolean))]
  if (sids.length === 0 || sids.length > QUOTE_MAX || !sids.every((s) => SID_RE.test(s))) return null
  return sids
}

/** 每 MIS_BATCH 檔一次請求的網址。不知道是上市還是上櫃，兩個市場都問：問錯市場的頻道只回空殼，parseMis 會略過。 */
export function misUrls(sids: string[]): string[] {
  const urls: string[] = []
  for (let i = 0; i < sids.length; i += MIS_BATCH) {
    const ch = sids.slice(i, i + MIS_BATCH).flatMap((s) => [`tse_${s}.tw`, `otc_${s}.tw`])
    urls.push(`${MIS_URL}?ex_ch=${encodeURIComponent(ch.join('|'))}&json=1&delay=0`)
  }
  return urls
}

// 價格：證交所用 "-" 表示沒有；0 或負數也當沒有（不補 0）
const price = (v: unknown): number | null => {
  if (typeof v !== 'string' || v === '-' || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) && n > 0 ? n : null
}
const lots = (v: unknown): number | null => {
  if (typeof v !== 'string' || v === '-' || v === '') return null
  const n = Number(v)
  return Number.isInteger(n) && n >= 0 ? n * 1000 : null
}

/** 解析一份 getStockInfo 回應。rtcode 不是 0000（例：一次問太多檔）或沒有 msgArray → 丟錯。 */
export function parseMis(body: unknown): Quote[] {
  const b = body as { rtcode?: unknown; msgArray?: unknown } | null
  if (!b || b.rtcode !== '0000' || !Array.isArray(b.msgArray)) {
    throw new Error(`證交所即時報價回應異常（rtcode=${String(b?.rtcode)}）`)
  }
  const out: Quote[] = []
  for (const raw of b.msgArray as Record<string, unknown>[]) {
    const d = raw.d
    // 問錯市場的頻道：回一個沒有代號、日期的空殼
    if (typeof raw.c !== 'string' || typeof d !== 'string' || !/^\d{8}$/.test(d)) continue
    const z = price(raw.z)
    const trade = raw.trade as { z?: unknown } | undefined
    const tradeZ = price(trade?.z)
    out.push({
      sid: raw.c,
      date: `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`,
      time: typeof raw.t === 'string' && /^\d{2}:\d{2}:\d{2}$/.test(raw.t) ? raw.t : null,
      open: price(raw.o),
      high: price(raw.h),
      low: price(raw.l),
      last: z ?? tradeZ,
      prev: price(raw.y),
      volume: lots(raw.v),
      limit_up: price(raw.u),
      limit_down: price(raw.w),
      auction: z === null && tradeZ !== null,
    })
  }
  return out
}
