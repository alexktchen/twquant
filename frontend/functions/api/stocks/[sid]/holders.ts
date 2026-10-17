// GET /api/stocks/:sid/holders：這一檔最近幾週的千張大戶比例（T-386）。表還沒建 → 空陣列。
// 每個人看到都一樣、一週才變一次：同一執行個體 60 秒暫存。
import { fail, guarded, json, type Env } from '../../../_lib/db'
import { cached, cacheKey } from '../../../_lib/cache'
import { readHolderWeeks } from '../../../_lib/holders'
import { SID_RE } from '../../../_lib/picks'
import { taipeiDay } from '../../../_lib/warroom'

const handle: PagesFunction<Env, 'sid'> = async ({ params, env }) => {
  const sid = String(params.sid)
  if (!SID_RE.test(sid)) return fail(400, `非法股票代號：${sid}`)
  const today = taipeiDay(Date.now())
  return guarded(env, async (db) => json({ sid, today, weeks: await readHolderWeeks(db, sid, today) }))
}

export const onRequestGet: PagesFunction<Env, 'sid'> = (ctx) => cached(cacheKey(ctx.request), async () => handle(ctx))
