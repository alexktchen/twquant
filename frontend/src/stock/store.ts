// 個股頁的 localStorage 持久化（單機儀表板免帳號；鍵名不可變更，跨版本沿用）。
import type { FactorOption } from '../api'
import { fromIds, readWatchlist, writeWatchlist } from '../platform/watchlist'

/* ---------- 追蹤股（與「我的追蹤」共用同一份儲存，見 platform/watchlist.ts）---------- */
export function loadWatch(): string[] {
  return readWatchlist().entries.map((e) => e.stock_id)
}
export function saveWatch(watch: string[]): void {
  // 以代號列表覆寫追蹤集合，但保留既有筆記與來源 run；寫入失敗僅本次會話有效。
  writeWatchlist(fromIds(readWatchlist().entries, watch))
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
