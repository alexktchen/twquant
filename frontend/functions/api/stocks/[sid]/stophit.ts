// GET /api/stocks/:sid/stophit：這一檔到今天為止的波動度落在哪一組、那組 20 日內碰 −15%／+20% 的歷史比例（D-191 §三 選項 C）。
// 收盤不足 21 筆 → hit: null。每個人看到都一樣，同一執行個體 60 秒暫存。
import { fail, guarded, json, type Env } from '../../../_lib/db'
import { cached, cacheKey } from '../../../_lib/cache'
import { SID_RE } from '../../../_lib/picks'
import { readStopHit } from '../../../_lib/stop-hit'
import { taipeiDay } from '../../../_lib/warroom'

const handle: PagesFunction<Env, 'sid'> = async ({ params, env }) => {
  const sid = String(params.sid)
  if (!SID_RE.test(sid)) return fail(400, `非法股票代號：${sid}`)
  const today = taipeiDay(Date.now())
  return guarded(env, async (db) => json({ sid, today, hit: (await readStopHit(db, today, [sid])).get(sid) ?? null }))
}

export const onRequestGet: PagesFunction<Env, 'sid'> = (ctx) => cached(cacheKey(ctx.request), async () => handle(ctx))
