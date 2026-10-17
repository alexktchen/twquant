// 名單績效 API（D-176）的純函數：線別檢查、把資料庫回來的 JSON 文字原樣包成回應。
// 名單績效每天由排程整份算好存在 serving.scorecard（每條線一列 jsonb），網站只讀不算。
// 回應直接用資料庫給的文字、不在這裡 JSON.parse 再 stringify：逐筆明細一條線可達上千列，免費方案每次請求只有 10ms CPU。
import { LINES } from './picks.ts'

/** 只接受登記過的線別（configs/pick_board.yaml）；其他一律 400，也擋掉奇怪字元。 */
export function lineOk(code: string | null): code is string {
  return code !== null && LINES.some((l) => l.code === code)
}

const HEADERS = { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'private, no-store' }

/** 資料庫回來的 JSON 文字 → 回應；包一層時（例：{"rows": …}）用 key。 */
export function rawJson(text: string, key?: string): Response {
  return new Response(key ? `{${JSON.stringify(key)}:${text}}` : text, { headers: HEADERS })
}

/** 還沒算出來時給使用者看的話：表還沒建（新功能上線後第一次排程跑完之前），或這條線還沒有那一列。 */
export const NOT_READY = '績效還沒算出來：每個交易日收盤後的排程（約 17:40）跑完就會出現。'

/** PostgreSQL「表不存在」（42P01）：第一次排程跑完之前就是這樣，不是壞掉。 */
export function missingTable(e: unknown): boolean {
  return typeof e === 'object' && e !== null && (e as { code?: unknown }).code === '42P01'
}

/** PostgreSQL「欄位不存在」（42703）：新欄位要等下一次排程才補上（例：mops_announcements.content，2026-10-17）。 */
export function missingColumn(e: unknown): boolean {
  return typeof e === 'object' && e !== null && (e as { code?: unknown }).code === '42703'
}

/** 讀「新加的欄位」用：表或欄位還沒建都當成沒有資料（回空）；其他錯誤照樣丟出去。 */
export async function readNewColumn<T>(query: () => Promise<T[]>): Promise<T[]> {
  try {
    return await query()
  } catch (e) {
    if (missingTable(e) || missingColumn(e)) return []
    throw e
  }
}

/** 查成績那張表；表還沒建當成「還沒有資料」（回空），其他錯誤照樣丟出去（503）。 */
export async function readRows<T>(query: () => Promise<T[]>): Promise<T[]> {
  try {
    return await query()
  } catch (e) {
    if (missingTable(e)) return []
    throw e
  }
}
