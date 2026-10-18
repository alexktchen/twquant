// 個股頁「籌碼與風險」卡的摘要文字（純函式，供 node 單元測試；畫面在 StockFacts.tsx）。
import { ratioText as pctText, weekChange, type HoldersWeek } from './holders.ts'
import type { StopHit } from './stophit.ts'
import { ivText, moneyText, type WarrantDay } from './warrants.ts'

export type FactKey = 'hit' | 'big' | 'wr'
export type FactLine = { key: FactKey; label: string; text: string }

const pc = (x: number) => `${Math.round(x * 100)}%`

/** 三行摘要：停損、大戶、權證；沒資料的那行不出現。 */
export function factLines(d: { hit: StopHit | null; weeks: HoldersWeek[]; days: WarrantDay[] }): FactLine[] {
  const out: FactLine[] = []
  if (d.hit) out.push({ key: 'hit', label: '同波動股', text: `20 日內碰 −15%：${pc(d.hit.down15)}，漲 +20%：${pc(d.hit.up20)}` })
  const last = d.weeks[d.weeks.length - 1]
  if (last && last.pct !== null) {
    const chg = d.weeks.length > 1 ? weekChange(d.weeks, d.weeks.length - 1) : ''
    out.push({ key: 'big', label: '千張大戶', text: `${pctText(last.pct)}${chg ? `（${chg}）` : ''}` })
  }
  const w = d.days[0]
  if (w) {
    const parts = [`認購 ${moneyText(w.call_value)}／認售 ${moneyText(w.put_value)}`]
    if (w.iv_call !== null) parts.push(`隱含波動 ${ivText(w.iv_call)}`)
    out.push({ key: 'wr', label: '權證', text: parts.join('，') })
  }
  return out
}
