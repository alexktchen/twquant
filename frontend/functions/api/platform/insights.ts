// GET /api/platform/insights?stock=2330&limit=10：洞悉證據包（serving.insight_packs，每日 pipeline 寫入；T-373）。
// 回應形狀與本機 Python API 相同（{ packs, issues }），個股頁「洞悉」分頁與研究頁共用。
import { fail, guarded, json, type Env } from '../../_lib/db'
import { cached, cacheKey } from '../../_lib/cache'
import { parseInsightQuery } from '../../_lib/insights'

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const q = parseInsightQuery(new URL(request.url))
  if (!q.ok) return fail(400, q.detail)
  return guarded(env, async (db) => {
    const rows = q.stock
      ? await db<{ pack: unknown }[]>`
          SELECT pack FROM serving.insight_packs WHERE stock_id = ${q.stock}
          ORDER BY as_of DESC, insight_run_id LIMIT ${q.limit}`
      : await db<{ pack: unknown }[]>`
          SELECT pack FROM serving.insight_packs ORDER BY as_of DESC, insight_run_id LIMIT ${q.limit}`
    // pack 是 jsonb，驅動程式已解成物件；證據文字是被引用的資料，原樣交給前端呈現。
    return json({ packs: rows.map((r) => r.pack), issues: [] })
  })
}

// 每個人看到都一樣、一天只更新兩次：同一執行個體 60 秒暫存（_lib/cache.ts）
export const onRequestGet: PagesFunction<Env> = (ctx) => cached(cacheKey(ctx.request), async () => handle(ctx))
