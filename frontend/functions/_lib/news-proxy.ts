// 網站 → 新聞服務（T-400／D-200）：/pub/news/* 與 /api/news/* 只是把請求轉給新聞服務的 API v1（news-service/supabase/functions/api），
// 服務金鑰放 Pages 祕密（NEWS_API_URL、NEWS_API_KEY），不給瀏覽器。
// T-438／D-210：只讀的端點公開在 /pub/news/*（Cloudflare Access 不保護 /pub，沒登入也看得到，是放廣告與搜尋引擎的前提）；
// 核可（擁有者）與語意搜尋（每次都花 AI 額度，不讓匿名流量燒）留在 /api/news/* 要登入。
// 新聞服務掛了 → 503，網站只有新聞分頁顯示暫停，其他分頁照常（SPEC-017 §10）。純函式＋注入 fetch，供 node 單元測試。
import { sameOrigin } from './watchlist.ts'

export type NewsEnv = { NEWS_API_URL?: string; NEWS_API_KEY?: string }

/** 每個端點准許轉過去的查詢參數（其餘丟掉，不讓瀏覽器亂帶）。 */
export const ALLOWED: Record<'/v1/items' | '/v1/health' | '/v1/graph' | '/v1/stock-news' | '/v1/stock-news-counts' | '/v1/similar' | '/v1/search' | '/v1/review' | '/v1/digest' | '/v1/galaxy' | '/v1/stock-news-days' | '/v1/thread' | '/v1/summary' | '/v1/evidence', string[]> = {
  '/v1/items': ['since', 'until', 'kind', 'source', 'limit', 'type'],
  '/v1/health': [],
  /** 一則新聞的關係圖（T-402／T-404）：id＝新聞 id；找不到 → 404 照轉 */
  '/v1/graph': ['id'],
  /** 反向看圖：某一檔近 7 天連得到的新聞（id＝tw:代號） */
  '/v1/stock-news': ['id'],
  /** 名單小標：一批股票各有幾則（ids＝tw:代號,…） */
  '/v1/stock-news-counts': ['ids'],
  /** 一則新聞的相似新聞（T-410）：id＝新聞 id */
  '/v1/similar': ['id'],
  /** 語意搜尋（T-410）：q＝要找的字 */
  '/v1/search': ['q'],
  /** 核可頁（T-403）：GET 列草稿、POST 送決定；登入者身分另外放 x-news-user */
  '/v1/review': [],
  /** 新聞三張卡（T-417）：熱門主題、多家同報、股票熱度 */
  '/v1/digest': [],
  /** 全景星雲圖（T-418）：排程算好的最新一筆 */
  '/v1/galaxy': [],
  /** 個股頁 K 線上的新聞標記（T-419）：id＝tw:代號 */
  '/v1/stock-news-days': ['id'],
  /** 這件事的來龍去脈（T-420）：id＝新聞 id */
  '/v1/thread': ['id'],
  /** 點開才翻的摘要（D-207）：id＝新聞 id */
  '/v1/summary': ['id'],
  /** 證據句（T-437）：ids＝新聞 id、entities＝實體 id，都逗號分開 */
  '/v1/evidence': ['ids', 'entities'],
}
/**
 * 指定新聞服務在哪個區域執行（Supabase 的 x-region）：資料庫在新加坡，不指定的話從台灣打過去會在東京跑，
 * 每個查詢都要跨區來回——2026-10-20 實測相似新聞 2.3 秒 → 0.6 秒、健康檢查 1.1 → 0.45 秒。
 */
export const UPSTREAM_REGION = 'ap-southeast-1'
/** 等新聞服務多久（毫秒）：Edge Function 冷啟動約 1 秒，查詢通常 1 秒內 */
export const UPSTREAM_TIMEOUT_MS = 8_000
/** 點開才翻摘要要等模型（一次約 2～5 秒，沒過檢查會重翻一次）：給長一點 */
export const SUMMARY_TIMEOUT_MS = 25_000

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

/** POST 的內容與登入者（核可頁）。user＝驗過 Cloudflare Access 權杖的 email，新聞服務拿它比對擁有者名單。 */
export type ProxyOpts = { method?: 'GET' | 'POST'; body?: string; user?: string | null; timeoutMs?: number }

/** 轉一次。沒設定、連不上、逾時、回的不是 JSON → 503（不外洩上游網址）；400／404 照轉（參數錯是使用者看得懂的訊息）。 */
export async function proxy(env: NewsEnv, path: keyof typeof ALLOWED, requestUrl: string, fetchFn: typeof fetch = fetch, opts: ProxyOpts = {}): Promise<Response> {
  if (!env.NEWS_API_URL || !env.NEWS_API_KEY) return reply(JSON.stringify({ detail: '新聞服務還沒設定' }), 503)
  let res: Response
  try {
    res = await fetchFn(upstreamUrl(env.NEWS_API_URL, path, requestUrl), {
      method: opts.method ?? 'GET',
      headers: {
        'x-news-key': env.NEWS_API_KEY,
        'x-region': UPSTREAM_REGION,
        accept: 'application/json',
        ...(opts.user ? { 'x-news-user': opts.user } : {}),
        ...(opts.body !== undefined ? { 'content-type': 'application/json' } : {}),
      },
      ...(opts.body !== undefined ? { body: opts.body } : {}),
      signal: AbortSignal.timeout(opts.timeoutMs ?? UPSTREAM_TIMEOUT_MS),
    })
  } catch {
    return reply(JSON.stringify({ detail: '新聞服務暫時連不上' }), 503)
  }
  const body = await res.text()
  if (!(res.headers.get('content-type') ?? '').includes('json')) return reply(JSON.stringify({ detail: '新聞服務暫時無法取得資料' }), 503)
  if (res.status === 200 || res.status === 400 || res.status === 404) return reply(body, res.status)
  return reply(JSON.stringify({ detail: '新聞服務暫時無法取得資料' }), 503)
}

/** 公開端點讓瀏覽器快取幾秒：新聞每 30 分鐘才進一批，同一個人反覆切分頁不用再打伺服器（Pages Functions 免費每天 10 萬次）。 */
export const PUBLIC_MAX_AGE_S = 60

/** 公開（不看身分）的轉接：成功的回應帶公開快取標頭；錯誤照舊 no-store，不讓瀏覽器把 503 留 60 秒。 */
export async function pubProxy(env: NewsEnv, path: keyof typeof ALLOWED, requestUrl: string, fetchFn: typeof fetch = fetch, opts: ProxyOpts = {}): Promise<Response> {
  const res = await proxy(env, path, requestUrl, fetchFn, opts)
  if (res.status !== 200) return res
  const headers = new Headers(res.headers)
  headers.set('cache-control', `public, max-age=${PUBLIC_MAX_AGE_S}`)
  return new Response(await res.text(), { status: 200, headers })
}

/** 核可頁一次送出的內容上限（100 個決定綽綽有餘） */
export const REVIEW_BODY_MAX = 20_000

/**
 * POST /api/news/review（T-403）：別站送來的不收、沒登入不收、太長不收，其餘連同登入者轉給新聞服務。
 * 是不是擁有者由新聞服務比對祕密名單決定（擁有者 email 不進版控）。email＝驗過 Cloudflare Access 權杖的登入者（呼叫端注入）。
 */
export async function reviewPost(request: Request, env: NewsEnv, email: (r: Request) => Promise<string | null>, fetchFn: typeof fetch = fetch): Promise<Response> {
  if (!sameOrigin(request)) return reply(JSON.stringify({ detail: '只接受本站送出的變更' }), 403)
  const user = await email(request)
  if (user === null) return reply(JSON.stringify({ detail: '登入身分驗證失敗，請重新登入' }), 401)
  const text = await request.text()
  if (text.length > REVIEW_BODY_MAX) return reply(JSON.stringify({ detail: '內容太長' }), 413)
  return proxy(env, '/v1/review', request.url, fetchFn, { method: 'POST', body: text, user })
}
