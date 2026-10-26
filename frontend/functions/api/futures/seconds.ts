// GET /api/futures/seconds?day=YYYY-MM-DD：那一天的秒級復盤資料（每秒最後成交價與 VWAP，差分整數）。只給 admin（T-450／D-220）。
// 收盤後才寫、只有逐筆的日子才有；沒有 → 404，網站改用 1 分 K 復盤。權限一定在讀資料庫之前擋。
import { fail, guarded, type Env } from '../../_lib/db'
import { NO_SECONDS, dayOk } from '../../_lib/futures'
import { denied } from '../../_lib/gate'
import { rawJson, readRows } from '../../_lib/scorecard'

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const day = new URL(request.url).searchParams.get('day')
  if (day === null || !dayOk(day)) return fail(400, `日期格式不對：${day ?? '（未指定）'}`)
  return guarded(env, async (db) => {
    const rows = await readRows(
      () => db<{ body: string }[]>`SELECT payload::text AS body FROM serving.futures_paper_seconds WHERE day = ${day}`,
    )
    if (rows.length === 0) return fail(404, NO_SECONDS)
    return rawJson(rows[0].body)
  })
}

export const onRequestGet: PagesFunction<Env> = async (ctx) => (await denied(ctx.request, 'futures', ctx.env)) ?? handle(ctx)
