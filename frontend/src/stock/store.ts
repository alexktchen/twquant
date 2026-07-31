// 個股頁的 localStorage 持久化（單機儀表板免帳號；鍵名不可變更，跨版本沿用）。
import type { FactorOption } from '../api'

/* ---------- 追蹤股 ---------- */
const WATCH_KEY = 'twquant.watchlist'
export function loadWatch(): string[] {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(WATCH_KEY) ?? '[]')
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
  } catch {
    return []
  }
}
export function saveWatch(watch: string[]): void {
  try {
    localStorage.setItem(WATCH_KEY, JSON.stringify(watch))
  } catch {
    /* 隱私模式等寫入失敗：追蹤僅本次會話有效 */
  }
}

/* ---------- 副圖因子選擇（跨股票、跨重整維持使用者最後選擇）---------- */
export const DEFAULT_SCREEN: FactorOption = { factor: 'price.reversal_5d', zh: '5日短期反轉', direction: -1 } // 實證 IC 最高
export const MAX_FACTOR_PANES = 4 // 副圖因子上限：再多主圖會被壓縮到難以閱讀

const SUBFACTORS_KEY = 'twquant.subFactors'
function isFactorOption(x: unknown): x is FactorOption {
  const o = x as Record<string, unknown> | null
  return !!o && typeof o.factor === 'string' && typeof o.zh === 'string' && typeof o.direction === 'number'
}
export function loadSubFactors(): FactorOption[] {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(SUBFACTORS_KEY) ?? 'null')
    if (Array.isArray(v) && v.length && v.every(isFactorOption)) return v
  } catch {
    /* 壞資料：退回預設 */
  }
  return [DEFAULT_SCREEN]
}
export function saveSubFactors(subs: FactorOption[]): void {
  try {
    localStorage.setItem(SUBFACTORS_KEY, JSON.stringify(subs))
  } catch {
    /* 隱私模式等寫入失敗：設定僅本次會話有效 */
  }
}
