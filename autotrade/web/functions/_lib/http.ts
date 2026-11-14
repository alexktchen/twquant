// 端點共用的小工具（autotrade A-018）。原本散在 twquant 網站的幾個檔案裡，這個網站只需要這四個。

const HEADERS = { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'private, no-store' }

/** 資料庫已經組好的 JSON 文字直接回給瀏覽器（不 parse 再 stringify）。no-store：盤中會變，也不能留在別人的快取裡。 */
export function rawJson(text: string): Response {
  return new Response(text, { headers: HEADERS })
}

/** PostgreSQL「表不存在」（42P01）：第一次使用之前就是這樣，不是壞掉。 */
export function missingTable(e: unknown): boolean {
  return typeof e === 'object' && e !== null && (e as { code?: unknown }).code === '42P01'
}

/** 讀資料；表還沒建就當成沒有資料（其他錯誤照丟）。 */
export async function readRows<T>(query: () => Promise<T[]>): Promise<T[]> {
  try {
    return await query()
  } catch (e) {
    if (missingTable(e)) return []
    throw e
  }
}

/** 會改東西的請求只收本站頁面送出的（瀏覽器會帶 Origin；沒帶的是同源的舊瀏覽器或直接呼叫，後面還有登入那一道）。 */
export function sameOrigin(request: Request): boolean {
  const origin = request.headers.get('origin')
  return origin === null || origin === new URL(request.url).origin
}

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/
/** ?day= 是不是 YYYY-MM-DD。 */
export const dayOk = (day: string): boolean => DAY_RE.test(day)
