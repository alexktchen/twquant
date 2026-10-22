// GET /api/stocks/:sid/picks：某檔被推薦過的日子（K 線標記）。只給 admin（T-429／D-207）。
import { fail, guarded, json, type Env } from '../../../_lib/db'
import { denied } from '../../../_lib/gate'
import { SID_RE } from '../../../_lib/picks'
import { cached, cacheKey } from '../../../_lib/cache'

const handle: PagesFunction<Env, 'sid'> = async ({ params, env }) => {
  const sid = String(params.sid)
  if (!SID_RE.test(sid)) return fail(400, `非法股票代號：${sid}`)
  return guarded(env, async (db) => {
    const rows = await db<{ line: string; d: string }[]>`
      SELECT line, date::text AS d FROM serving.daily_picks WHERE stock_id = ${sid} ORDER BY d`
    const days: Record<string, string[]> = {}
    for (const r of rows) (days[r.line] ??= []).push(r.d)
    return json({ sid, days })
  })
}

// 有權限的人看到都一樣、一天只更新兩次：同一執行個體 60 秒暫存，省下連資料庫的 CPU（_lib/cache.ts）；先擋權限再讀暫存
export const onRequestGet: PagesFunction<Env, 'sid'> = async (ctx) =>
  (await denied(ctx.request, 'picks')) ?? cached(cacheKey(ctx.request), async () => handle(ctx))
