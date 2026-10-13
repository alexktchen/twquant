// GET /api/stocks/search?q=2330 或 q=台積：代號開頭相符或名稱含這幾個字的股票（只列雲端有股價的，最多 SEARCH_LIMIT 檔）。
import { fail, guarded, json, type Env } from '../../_lib/db'
import { SEARCH_LIMIT, searchPattern } from '../../_lib/scores'
import { cached, cacheKey } from '../../_lib/cache'

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const q = searchPattern(new URL(request.url).searchParams.get('q'))
  if (q === null) return fail(400, '請輸入 1～20 個字的股號或名稱')
  return guarded(env, async (db) => {
    const items = await db<{ sid: string; name: string | null; industry: string | null }[]>`
      SELECT m.stock_id AS sid, m.name, m.industry FROM serving.stock_meta m
      WHERE (m.stock_id LIKE ${q.like + '%'} OR m.name ILIKE ${'%' + q.like + '%'})
        AND EXISTS (SELECT 1 FROM serving.prices_daily p WHERE p.stock_id = m.stock_id)
      ORDER BY m.stock_id = ${q.text} DESC, m.stock_id LIKE ${q.like + '%'} DESC, m.stock_id
      LIMIT ${SEARCH_LIMIT}`
    return json({ q: q.text, items })
  })
}

// 每個人看到都一樣、一天只更新兩次：同一執行個體 60 秒暫存，省下連資料庫的 CPU（_lib/cache.ts）
export const onRequestGet: PagesFunction<Env> = (ctx) => cached(cacheKey(ctx.request), async () => handle(ctx))
