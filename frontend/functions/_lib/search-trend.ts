// 星雲圖「大家都在搜」（T-446／D-217；使用者 2026-10-23：「再做一個大家都在搜尋什麼」）。
// 記的是搜尋後「選了哪個亮點」（股票、主題的 id），不記打的字；同一個分頁同一天搜同一個只算一次（分頁亂數 id 同「幾個人在看」）。
// 排行＝近 TREND_DAYS 天每個亮點被幾個分頁搜過。前端只顯示目前星雲圖裡有的亮點，亂送的 id 不會出現在畫面上。

/** 排行看近幾天（跟星雲圖的「近 7 天」一致） */
export const TREND_DAYS = 7
/** 排行最多回幾個（前端再挑星雲圖裡有的） */
export const TREND_TOP = 20
/** 超過幾天的紀錄刪掉 */
export const TREND_PURGE_DAYS = 8
/** 一次最多記幾個亮點（兩個詞的路徑＝兩個） */
export const TREND_MAX_HUBS = 2
/** 請求內容上限（位元組） */
export const TREND_BODY_MAX = 300

/** 建表（第一次有人搜才建）；day 是台北日期 */
export const TREND_DDL = [
  'CREATE TABLE IF NOT EXISTS web.galaxy_searches (day date NOT NULL, viewer text NOT NULL, hub text NOT NULL, PRIMARY KEY (day, viewer, hub))',
]

const ID_RE = /^[0-9a-f]{16,32}$/
/** 亮點 id：tw:2330、us:NVDA、th:…、grp:散熱（族群名是中文）；前綴小寫英文、冒號後不含空白、引號、角括號、反斜線 */
const HUB_RE = /^[a-z]{2,8}:[^\s'"<>\\]{1,40}$/u

/** POST /pub/galaxy-search 的內容：{ id, hubs: [1～2 個亮點 id] }，重複的只留一個。其他一律不收 → null。 */
export function parseSearch(text: string): { id: string; hubs: string[] } | null {
  if (text.length > TREND_BODY_MAX) return null
  let v: unknown
  try {
    v = JSON.parse(text)
  } catch {
    return null
  }
  if (typeof v !== 'object' || v === null || Array.isArray(v)) return null
  const { id, hubs, ...rest } = v as { id?: unknown; hubs?: unknown }
  if (Object.keys(rest).length || typeof id !== 'string' || !ID_RE.test(id)) return null
  if (!Array.isArray(hubs) || hubs.length < 1 || hubs.length > TREND_MAX_HUBS) return null
  if (!hubs.every((h): h is string => typeof h === 'string' && HUB_RE.test(h))) return null
  return { id, hubs: [...new Set(hubs)] }
}
