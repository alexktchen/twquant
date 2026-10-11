// Cloudflare Pages Functions 版的每日名單 API（T-332）：與 Python 的 monitoring/picks_api.py 同一套規則。
// 純函數放這裡供 node 單元測試；資料庫存取在 db.ts。

/** 線別順序與顯示名稱——以 configs/pick_board.yaml 為準，改那邊時要同步改這裡。 */
export const LINES: { code: string; name: string; note?: string }[] = [
  { code: 'CORE', name: '大盤精選' },
  { code: 'QUIET', name: '潛伏飆股' },
  // D-174（2026-10-11）：第三條線，驗證中。大多數日子沒有符合的股票，所以當天沒名單也照樣列出
  {
    code: 'QUIETV',
    name: '高分爆量',
    note: '驗證中：潛伏飆股裡模型分數 0.35 以上、而且當天爆量（成交量是前 20 天平均的 1.5 倍以上）才列入，每檔抱 20 個交易日。2027 年 3 月用新資料檢驗前只供觀察。',
  },
]
/** 連續上榜最多回看幾個有名單的日子（同 picks_api.STREAK_LOOKBACK）。 */
export const STREAK_LOOKBACK = 60
/** 「近 6 日上榜」方格與近 6 日漲幅的天數（同 picks_api.RECENT_WINDOW）。 */
export const RECENT_WINDOW = 6

export const SID_RE = /^[0-9A-Z]{4,6}$/
export const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

/** 每檔的 [連續上榜天數, 是否新進]。previous 由近到遠，每項是那天同一條線的名單。 */
export function annotate(today: string[], previous: string[][]): Record<string, [number, boolean]> {
  const out: Record<string, [number, boolean]> = {}
  for (const sid of today) {
    let streak = 1
    for (const day of previous) {
      if (!day.includes(sid)) break
      streak += 1
    }
    out[sid] = [streak, previous.length === 0 || !previous[0].includes(sid)]
  }
  return out
}

/** 近 N 日每天是否在名單，舊到新。window 由近到遠；null＝那天這條線沒有名單（模型沒打分）。 */
export function recentFlags(sid: string, window: (string[] | null)[]): (boolean | null)[] {
  return [...window].reverse().map((day) => (day === null ? null : day.includes(sid)))
}

/** 當日漲跌；缺任一價或前收 ≤ 0 → null（不補 0）。 */
export function change(close: number | null, prev: number | null): number | null {
  if (close === null || prev === null || prev <= 0) return null
  return close / prev - 1
}

export type PickRow = { date: string; line: string; rank: number; stock_id: string; score: number | null }

/** 依 LINES 順序排列今天出現的線別；未登記的線排最後。 */
export function lineOrder(codes: string[]): string[] {
  const idx = (c: string) => {
    const i = LINES.findIndex((l) => l.code === c)
    return i < 0 ? LINES.length : i
  }
  return [...new Set(codes)].sort((a, b) => idx(a) - idx(b) || a.localeCompare(b))
}

/** 當天要顯示的線：登記過的線一律列出（沒有股票＝名單為空），再加上未登記但當天有資料的線。 */
export function linesForDay(todayCodes: string[]): string[] {
  return lineOrder([...LINES.map((l) => l.code), ...todayCodes])
}

/** 日期選單的檔數：把 (日期, 線別, 檔數) 列整理成 counts[日期][線別]，把每條線的第一天整理成 since[線別]。 */
export function groupCounts(
  rows: { d: string; line: string; n: number }[],
  first: { line: string; d: string }[],
): { counts: Record<string, Record<string, number>>; since: Record<string, string> } {
  const counts: Record<string, Record<string, number>> = {}
  for (const r of rows) (counts[r.d] ??= {})[r.line] = r.n
  return { counts, since: Object.fromEntries(first.map((f) => [f.line, f.d])) }
}
