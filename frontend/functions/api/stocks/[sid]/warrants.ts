// GET /api/stocks/:sid/warrants：這一檔最近幾天的權證摘要（T-385）。表還沒建或這檔沒有權證 → 空陣列。
// 每個人看到都一樣、一天更新一次：同一執行個體 60 秒暫存。
import { fail, guarded, json, type Env } from '../../../_lib/db'
import { cached, cacheKey } from '../../../_lib/cache'
import { SID_RE } from '../../../_lib/picks'
import { readWarrantDays } from '../../../_lib/warrants'
import { taipeiDay } from '../../../_lib/warroom'

const handle: PagesFunction<Env, 'sid'> = async ({ params, env }) => {
  const sid = String(params.sid)
  if (!SID_RE.test(sid)) return fail(400, `非法股票代號：${sid}`)
  const today = taipeiDay(Date.now())
  return guarded(env, async (db) => json({ sid, today, days: await readWarrantDays(db, sid, today) }))
}

export const onRequestGet: PagesFunction<Env, 'sid'> = (ctx) => cached(cacheKey(ctx.request), async () => handle(ctx))
