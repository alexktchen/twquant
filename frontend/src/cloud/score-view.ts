// 績效頁（2026-10-18 改版，地鐵告示：顏色歸類、圖案輔佐、減少文字）的純函式：數字卡的內容、對照贏輸、統計欄位。
// 不碰 DOM，供 node 單元測試；畫面在 ScorecardPage.tsx。
import { pct } from './format.ts'
import { ratio, type TradeStats } from './scorecard.ts'

export type Tone = 'up' | 'down' | 'plain' | 'na' | 'warn'

/** 報酬的顏色：正紅、負綠、零或沒有灰。 */
export function tone(v: number | null | undefined): Tone {
  if (v === null || v === undefined || !Number.isFinite(v)) return 'na'
  return v > 0 ? 'up' : v < 0 ? 'down' : 'plain'
}

/** 帳戶對同期對照贏／輸幾個百分點：「贏 3.2 點」「輸 1.0 點」；算不出來 null。 */
export function benchDiff(acct: number | null, bench: number | null): { win: boolean; text: string } | null {
  if (acct === null || bench === null || !Number.isFinite(acct) || !Number.isFinite(bench)) return null
  const d = acct - bench
  return { win: d >= 0, text: `${d >= 0 ? '贏' : '輸'} ${(Math.abs(d) * 100).toFixed(1)} 點` }
}

export type StatTile = { key: string; label: string; value: string; tone: Tone; sub?: string }

/** 逐筆統計一欄 → 數字卡；n＝0 全部顯示「—」（不是 0）。bigLoss 是「虧超過幾 %」的門檻（小數）。 */
export function statTiles(s: TradeStats, bigLoss: number): StatTile[] {
  const loss = Math.round(-bigLoss * 100)
  return [
    { key: 'n', label: '已結算', value: s.n.toLocaleString('en-US'), tone: 'plain', sub: '筆' },
    { key: 'mean', label: '每筆平均', value: pct(s.mean, 2), tone: tone(s.mean) },
    { key: 'median', label: '中位數', value: pct(s.median, 2), tone: tone(s.median) },
    { key: 'win', label: '賺錢比例', value: ratio(s.win_rate), tone: 'plain' },
    { key: 'bigloss', label: `虧超過 ${loss}%`, value: ratio(s.big_loss_rate), tone: s.big_loss_rate !== null && s.big_loss_rate > 0 ? 'warn' : 'plain' },
    { key: 'best', label: '最好一筆', value: pct(s.best, 1), tone: tone(s.best) },
    { key: 'worst', label: '最差一筆', value: pct(s.worst, 1), tone: tone(s.worst) },
  ]
}

/** 帳戶右上角那串：現金比例（0.37 → 37%）；淨值為 0 → null。 */
export function cashRatio(cash: number, equity: number): number | null {
  return equity > 0 ? cash / equity : null
}

/** 逐筆明細每列右下的狀態短字：已賣 / 持有中 / 明天買 / 買不到，加上抱了幾天。 */
export function heldText(status: 'closed' | 'open' | 'unfilled' | 'pending', held: number): string {
  if (status === 'closed') return `${held} 天`
  if (status === 'open') return `已 ${held} 天`
  return ''
}
