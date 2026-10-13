// 同一個執行個體裡暫存「每個人看到都一樣」的回應（名單、日期、K 線、被推薦日、評分、搜尋）。
// 資料一天只更新兩次（17:33／22:43 的排程），60 秒內重複的請求不用再連資料庫。
// 為什麼：免費方案每次請求 10ms CPU；連一次資料庫就要約 8ms（經 Hyperdrive）～11ms（直連），不連只要約 2ms
// （2026-10-13 實測）。流量一多（例：連結在 LINE 傳開）Cloudflare 就開始砍超時的請求（error 1102）。
// 只放不分使用者的資料：追蹤股、登入身分這類每個人不同的回應絕對不能進來。
export const CACHE_TTL_MS = 60_000
export const CACHE_MAX_KEYS = 300

type Entry = { at: number; body: string; headers: [string, string][] }
const store = new Map<string, Entry>()

/** 鍵＝路徑＋查詢字串（不含網域：正式與預覽各自是不同的執行個體）。 */
export const cacheKey = (request: Request): string => {
  const u = new URL(request.url)
  return u.pathname + u.search
}

/** 有 60 秒內的同一份回應就直接回；否則照常產生，成功（200）才存。錯誤不存，下次照樣重試。 */
export async function cached(key: string, make: () => Promise<Response>, now: number = Date.now()): Promise<Response> {
  const hit = store.get(key)
  if (hit && now - hit.at < CACHE_TTL_MS) return new Response(hit.body, { headers: hit.headers })
  const res = await make()
  if (res.status !== 200) return res
  const body = await res.text()
  const headers = [...res.headers.entries()]
  if (store.size >= CACHE_MAX_KEYS) {
    for (const [k, v] of store) if (now - v.at >= CACHE_TTL_MS) store.delete(k)
  }
  if (store.size < CACHE_MAX_KEYS) store.set(key, { at: now, body, headers })
  return new Response(body, { status: 200, headers })
}

/** 測試用：清空暫存。 */
export const clearCache = (): void => store.clear()
