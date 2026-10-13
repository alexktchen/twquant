// 這台裝置的瀏覽器記住的東西（隱私權政策有列）：K 線看幾根、上次看的策略、是否同意過投資風險警語（risk.ts）。
// 只有這些，不含個人資料。
export const LINE_KEY = 'twquant.line'

/** 讀回偏好；瀏覽器禁用儲存（部分無痕模式）時當作沒記過。 */
export function loadPref(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

/** 存偏好；存不了（無痕或空間滿）時這次開著仍有效，只是重新整理後回預設。 */
export function savePref(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // 見上：不影響看名單
  }
}
