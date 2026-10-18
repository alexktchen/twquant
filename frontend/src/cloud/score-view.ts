// 績效頁的純函式（2026-10-18 第二版）。使用者看第一版說「看不太懂」：整頁一堆數字沒有結論。
// 第二版每張卡先回答一個問題（照名單全買賺多少？中途最多跌多少？每一檔抱 20 天結果如何？），
// 用長條與比例條取代表格，術語換成白話（中位數 → 排中間的那檔、最大回撤 → 中途最多跌掉、事後補 → 回頭補算）。
// 不碰 DOM，供 node 單元測試；畫面在 ScorecardPage.tsx。
import type { RowFilter } from './scorecard.ts'

export type Tone = 'up' | 'down' | 'plain' | 'na' | 'warn'

/** 報酬的顏色：正紅、負綠、零或沒有灰。 */
export function tone(v: number | null | undefined): Tone {
  if (v === null || v === undefined || !Number.isFinite(v)) return 'na'
  return v > 0 ? 'up' : v < 0 ? 'down' : 'plain'
}

/** 「即時／事後補」對使用者的說法。 */
export const LIVE_NAME: Record<RowFilter, string> = { all: '全部', live: '每天公布', backfill: '回頭補算' }

/** 帳戶比對照多／少幾個百分點：「比 0050 少 3.6 點」；算不出來 null。 */
export function benchDiff(acct: number | null, bench: number | null, code: string): { win: boolean; text: string } | null {
  if (acct === null || bench === null || !Number.isFinite(acct) || !Number.isFinite(bench)) return null
  const d = acct - bench
  return { win: d >= 0, text: `比 ${code} ${d >= 0 ? '多' : '少'} ${(Math.abs(d) * 100).toFixed(1)} 點` }
}

/** 中途最多跌掉的幅度是對照的幾倍：「是 0050 的 2.3 倍」；任一邊算不出來或對照沒跌 → null。 */
export function mddTimes(acct: number | null, bench: number | null, code: string): string | null {
  if (acct === null || bench === null || !Number.isFinite(acct) || !Number.isFinite(bench) || bench >= 0) return null
  return `是 ${code} 的 ${(acct / bench).toFixed(1)} 倍`
}

/** 長條寬度（0–100，%）：|v| ÷ 這一組最大的 |v|；沒有值是 0。 */
export function barWidth(v: number | null, values: (number | null)[]): number {
  const max = Math.max(0, ...values.map((x) => (x === null || !Number.isFinite(x) ? 0 : Math.abs(x))))
  if (v === null || !Number.isFinite(v) || max === 0) return 0
  return (Math.abs(v) / max) * 100
}

export type BucketKey = 'bigWin' | 'win' | 'loss' | 'bigLoss'
export type Bucket = { key: BucketKey; label: string; range: string; n: number; share: number }

/**
 * 每一檔抱滿後的結果分四格：大賺、小賺、小賠、大賠。門檻是設定檔的「大虧」門檻（例 −15%），大賺用同樣幅度。
 * 剛好 0 算小賠（沒賺到就是沒賺到，還付了手續費）。沒有任何一筆 → share 全 0。
 */
export function outcomeBuckets(rets: number[], bigLoss: number): Bucket[] {
  const cut = Math.abs(bigLoss)
  const p = Math.round(cut * 100)
  const n = { bigWin: 0, win: 0, loss: 0, bigLoss: 0 }
  for (const r of rets) {
    if (r > cut) n.bigWin++
    else if (r > 0) n.win++
    else if (r >= -cut) n.loss++
    else n.bigLoss++
  }
  const total = rets.length
  const share = (k: number) => (total > 0 ? k / total : 0)
  return [
    { key: 'bigWin', label: '大賺', range: `+${p}% 以上`, n: n.bigWin, share: share(n.bigWin) },
    { key: 'win', label: '小賺', range: `0～+${p}%`, n: n.win, share: share(n.win) },
    { key: 'loss', label: '小賠', range: `−${p}%～0`, n: n.loss, share: share(n.loss) },
    { key: 'bigLoss', label: '大賠', range: `−${p}% 以下`, n: n.bigLoss, share: share(n.bigLoss) },
  ]
}

/** 平均與排中間那檔一起看的一句話：兩個正負不同，代表少數幾檔把平均拉走了。 */
export function shapeVerdict(mean: number | null, median: number | null): string | null {
  if (mean === null || median === null) return null
  if (mean > 0 && median < 0) return '多數賠錢，靠少數大賺把平均拉上來'
  if (mean > 0) return '多數檔賺錢'
  if (median >= 0) return '多數小賺，但少數大賠把平均拉下來'
  return '多數檔賠錢'
}

/** 回頭補算與每天公布兩段在時間軸上各佔多少（交易日數）；沒有每天公布 → live 0。 */
export function segments(navDays: number, liveDays: number | null): { backfill: number; live: number } {
  const live = liveDays !== null && liveDays > 0 ? Math.min(liveDays, navDays) : 0
  const backfill = Math.max(navDays - live, 0)
  const total = backfill + live
  return total > 0 ? { backfill: backfill / total, live: live / total } : { backfill: 0, live: 0 }
}

/** 帳戶現金比例（0.37 → 37%）；淨值為 0 → null。 */
export function cashRatio(cash: number, equity: number): number | null {
  return equity > 0 ? cash / equity : null
}

/**
 * 淨值切成兩段各自的報酬：回頭補算（第一天到每天公布第一天收盤）與每天公布（從那天收盤算到最後）。
 * 每天公布第一天的名單收盤後才出來、隔天才買，所以那天的漲跌還是回頭補算那批股票的；跟伺服器算「即時公布以來」同一個起點。
 * 兩段接起來等於整段。沒有每天公布 → live null、backfill 是整段。
 */
export function splitReturn(
  nav: { d: string; v: number }[],
  liveSince: string | null,
): { backfill: number | null; live: number | null; backfillEnd: string | null } {
  const v0 = nav[0]?.v
  const last = nav.at(-1)
  if (!v0 || !last) return { backfill: null, live: null, backfillEnd: null }
  const i = liveSince === null ? -1 : nav.findIndex((p) => p.d >= liveSince)
  if (i < 0) return { backfill: last.v / v0 - 1, live: null, backfillEnd: last.d }
  const base = nav[i]
  return { backfill: base.v / v0 - 1, live: last.v / base.v - 1, backfillEnd: base.d }
}
