// GET /api/daytrade?day=YYYY-MM-DD：個股當沖那一天的整份狀態（沒給 day＝最新一天）。權限同台指期（只給 admin，T-458／D-221）。
// 權限一定在讀資料庫之前擋；盤中每 15 秒會變，不進暫存。
import { fail, guarded, type Env } from '../../_lib/db'
import { DAYTRADE_NOT_READY } from '../../_lib/daytrade'
import { dayOk } from '../../_lib/futures'
import { denied } from '../../_lib/gate'
import { rawJson, readRows } from '../../_lib/scorecard'

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const day = new URL(request.url).searchParams.get('day')
  if (day !== null && !dayOk(day)) return fail(400, `日期格式不對：${day}`)
  return guarded(env, async (db) => {
    const rows = await readRows(() =>
      day === null
        ? db<{ body: string }[]>`SELECT payload::text AS body FROM serving.stock_paper ORDER BY day DESC LIMIT 1`
        : db<{ body: string }[]>`SELECT payload::text AS body FROM serving.stock_paper WHERE day = ${day}`,
    )
    if (rows.length === 0) return fail(404, day === null ? DAYTRADE_NOT_READY : `${day} 沒有當沖紀錄`)
    return rawJson(rows[0].body)
  })
}

export const onRequestGet: PagesFunction<Env> = async (ctx) => (await denied(ctx.request, 'futures', ctx.env)) ?? handle(ctx)
