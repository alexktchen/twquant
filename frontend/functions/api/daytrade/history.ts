// GET /api/daytrade/history?day=YYYY-MM-DD：那一天的復盤資料（榜單每 30 秒一份、有成交的股票的 1 分 K 與秒級）。權限同台指期（T-458／D-221）。
// 收盤後才有、不會再變，但量大且只給 admin，一樣不進暫存。
import { fail, guarded, type Env } from '../../_lib/db'
import { NO_HISTORY } from '../../_lib/daytrade'
import { dayOk } from '../../_lib/futures'
import { denied } from '../../_lib/gate'
import { rawJson, readRows } from '../../_lib/scorecard'

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const day = new URL(request.url).searchParams.get('day')
  if (day === null || !dayOk(day)) return fail(400, `要給日期 ?day=YYYY-MM-DD：${day ?? '（未指定）'}`)
  return guarded(env, async (db) => {
    const rows = await readRows(() => db<{ body: string }[]>`SELECT payload::text AS body FROM serving.stock_paper_history WHERE day = ${day}`)
    if (rows.length === 0) return fail(404, NO_HISTORY)
    return rawJson(rows[0].body)
  })
}

export const onRequestGet: PagesFunction<Env> = async (ctx) => (await denied(ctx.request, 'futures', ctx.env)) ?? handle(ctx)
