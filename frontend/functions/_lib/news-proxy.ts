// 網站 → 新聞服務（T-400／D-200）：/api/news/* 只是把請求轉給新聞服務的 API v1（news-service/supabase/functions/api），
// 服務金鑰放 Pages 祕密（NEWS_API_URL、NEWS_API_KEY），不給瀏覽器。/api 在 Cloudflare Access 後面：沒登入讀不到。
// 新聞服務掛了 → 503，網站只有新聞分頁顯示暫停，其他分頁照常（SPEC-017 §10）。純函式＋注入 fetch，供 node 單元測試。

export type NewsEnv = { NEWS_API_URL?: string; NEWS_API_KEY?: string }

/** 每個端點准許轉過去的查詢參數（其餘丟掉，不讓瀏覽器亂帶）。 */
export const ALLOWED: Record<'/v1/items' | '/v1/health', string[]> = {
  '/v1/items': ['since', 'until', 'kind', 'source', 'limit'],
  '/v1/health': [],
}
/** 等新聞服務多久（毫秒）：Edge Function 冷啟動約 1 秒，查詢通常 1 秒內 */
export const UPSTREAM_TIMEOUT_MS = 8_000

/** 上游網址：新聞服務基底＋路徑＋准許的參數（照字母排，同一組參數＝同一個暫存鍵）。 */
export function upstreamUrl(base: string, path: keyof typeof ALLOWED, requestUrl: string): string {
  const src = new URL(requestUrl).searchParams
  const out = new URL(base.replace(/\/+$/, '') + path)
  for (const k of [...ALLOWED[path]].sort()) {
    const v = src.get(k)
    if (v !== null && v !== '') out.searchParams.set(k, v)
  }
  return out.toString()
}

const reply = (body: string, status: number) =>
  new Response(body, { status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'private, no-store' } })

/** 轉一次。沒設定、連不上、逾時、回的不是 JSON → 503（不外洩上游網址）；400／404 照轉（參數錯是使用者看得懂的訊息）。 */
export async function proxy(env: NewsEnv, path: keyof typeof ALLOWED, requestUrl: string, fetchFn: typeof fetch = fetch): Promise<Response> {
  if (!env.NEWS_API_URL || !env.NEWS_API_KEY) return reply(JSON.stringify({ detail: '新聞服務還沒設定' }), 503)
  let res: Response
  try {
    res = await fetchFn(upstreamUrl(env.NEWS_API_URL, path, requestUrl), {
      headers: { 'x-news-key': env.NEWS_API_KEY, accept: 'application/json' },
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    })
  } catch {
    return reply(JSON.stringify({ detail: '新聞服務暫時連不上' }), 503)
  }
  const body = await res.text()
  if (!(res.headers.get('content-type') ?? '').includes('json')) return reply(JSON.stringify({ detail: '新聞服務暫時無法取得資料' }), 503)
  if (res.status === 200 || res.status === 400 || res.status === 404) return reply(body, res.status)
  return reply(JSON.stringify({ detail: '新聞服務暫時無法取得資料' }), 503)
}
