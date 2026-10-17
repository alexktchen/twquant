// 這台裝置的瀏覽器記住的東西（隱私權政策有列）：K 線看幾根、上次看的策略、首頁各卡的展開／收起、是否同意過投資風險警語（risk.ts）。
// 只有這些，不含個人資料。
export const LINE_KEY = 'twquant.line'
/** 首頁「族群資金與熱力圖」展開或收起（使用者 2026-10-17：預設打開，點過要記住） */
export const HEAT_KEY = 'twquant.heat'
/** 「市場氛圍」卡展開或收起 */
export const MOOD_KEY = 'twquant.mood'
/** 「昨日消息」展開（列表）或收起（輪播一則） */
export const NEWS_KEY = 'twquant.news'
/** 「昨晚國際大事・新聞頭條」展開（列表）或收起（輪播一則；預設收起） */
export const HEADS_KEY = 'twquant.heads'
/** 「行事曆」展開（全部）或收起（摘要＋名單股、追蹤股；預設收起） */
export const CAL_KEY = 'twquant.cal'

/** 存起來的展開狀態：'1' 展開、'0' 收起；沒存過或看不懂 → 預設值。 */
export function openPref(stored: string | null, fallback: boolean): boolean {
  if (stored === '1') return true
  if (stored === '0') return false
  return fallback
}

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
