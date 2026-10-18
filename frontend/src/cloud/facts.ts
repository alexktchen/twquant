// 個股頁「籌碼與風險」的數字卡內容（純函式，供 node 單元測試；畫面在 StockFacts.tsx）。
// 2026-10-18 改版：四張數字卡——碰 −15%、漲 +20%（同波動股歷史）、千張大戶（週變化）、權證（認售占比、隱含波動）。
import { ratioText as pctText, type HoldersWeek } from './holders.ts'
import type { StopHit } from './stophit.ts'
import { ivText, moneyText, type WarrantDay } from './warrants.ts'

export type FactKey = 'hit' | 'big' | 'wr'
export type FactTile = {
  /** 點了展開哪一塊細節 */
  key: FactKey
  id: string
  label: string
  value: string
  sub: string
  tone: 'plain' | 'warn' | 'up' | 'down' | 'na'
}

const pc = (x: number) => `${Math.round(x * 100)}%`

/** 數字卡；沒資料的卡不出現。 */
export function factTiles(d: { hit: StopHit | null; weeks: HoldersWeek[]; days: WarrantDay[] }): FactTile[] {
  const out: FactTile[] = []
  if (d.hit) {
    out.push({ key: 'hit', id: 'down15', label: '碰停損 −15%', value: pc(d.hit.down15), sub: '同波動股・20 日', tone: d.hit.down15 >= 0.15 ? 'warn' : 'plain' })
    out.push({ key: 'hit', id: 'up20', label: '漲兩成 +20%', value: pc(d.hit.up20), sub: '同波動股・20 日', tone: 'plain' })
  }
  const last = d.weeks[d.weeks.length - 1]
  const prev = d.weeks.length > 1 ? d.weeks[d.weeks.length - 2] : null
  if (last && last.pct !== null) {
    const chg = prev && prev.pct !== null ? last.pct - prev.pct : null
    const pp = chg === null ? '' : `${Math.abs(chg * 100).toFixed(1)}pp`
    out.push({
      key: 'big',
      id: 'big',
      label: '千張大戶',
      value: pctText(last.pct),
      sub: chg === null ? '每週六更新' : Math.abs(chg) < 0.0005 ? '週 持平' : `週 ${chg > 0 ? '▲' : '▼'}${pp}`,
      tone: chg === null || Math.abs(chg) < 0.0005 ? 'plain' : chg > 0 ? 'up' : 'down',
    })
  }
  const w = d.days[0]
  if (w) {
    const total = w.call_value + w.put_value
    out.push({
      key: 'wr',
      id: 'wr',
      label: '權證認售占比',
      value: total > 0 ? pc(w.put_value / total) : '—',
      sub: `成交 ${moneyText(total)}${w.iv_call !== null ? `・IV ${ivText(w.iv_call)}` : ''}`,
      tone: total > 0 && w.put_value / total >= 0.3 ? 'warn' : 'plain',
    })
  }
  return out
}
