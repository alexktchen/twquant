// 名單上的千張大戶小標與個股頁的文字（T-386）。純函式，供 node 單元測試；後端資料形狀見 functions/_lib/holders.ts。
// 只供看盤：名單照模型排名產生，不因這個標記刪減。
export type HoldersWeek = { date: string; pct: number | null; big_holders: number | null; total: number | null }
export type HoldersFlag = { latest: string; pct: number; change: number | null; streak: number; weeks: number }

/** 連增／連減幾週以上才標（同後端 STREAK_MIN）。 */
export const STREAK_MIN = 3

/** 比例（小數）→ 「42.0%」；不帶正負號（format.pct 是給漲跌用的）。null → '—'。 */
export function ratioText(x: number | null): string {
  if (x === null || !Number.isFinite(x)) return '—'
  return `${(x * 100).toFixed(1)}%`
}

/** 小數差 → 「+0.4 個百分點」「−1.2 個百分點」「持平」；null → ''。 */
export function ppText(change: number | null): string {
  if (change === null || !Number.isFinite(change)) return ''
  const pp = change * 100
  if (Math.abs(pp) < 0.05) return '持平'
  return `${pp > 0 ? '+' : '−'}${Math.abs(pp).toFixed(1)} 個百分點`
}

/** 名單一列的小標：連增／連減 STREAK_MIN 週以上才有；沒資料、舊後端沒有這欄 → null。 */
export function holdersTag(f: HoldersFlag | null | undefined): { text: string; title: string } | null {
  if (!f || Math.abs(f.streak) < STREAK_MIN) return null
  const up = f.streak > 0
  const n = Math.abs(f.streak)
  const title =
    `千張大戶（持股 1,000 張以上）占總股數的比例已連續 ${n} 週${up ? '增加' : '減少'}，` +
    `最新一週（${f.latest.slice(5).replace('-', '/')}）${ratioText(f.pct)}${f.change === null ? '' : `，對前一週 ${ppText(f.change)}`}。` +
    '集保資料每週六公布，名單只用已公布的；只是提醒，名單不因此刪減。'
  return { text: `大戶連${up ? '增' : '減'}${n}週`, title }
}

/** 個股頁每週一列的變化欄：對前一週的變化。 */
export function weekChange(weeks: HoldersWeek[], i: number): string {
  if (i === 0) return ''
  const a = weeks[i].pct
  const b = weeks[i - 1].pct
  if (a === null || b === null) return ''
  return ppText(a - b)
}
