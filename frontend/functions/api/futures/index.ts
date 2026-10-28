// GET /api/futures?day=YYYY-MM-DD：小台模擬那一天的整份狀態（沒給 day＝最新一天）。只給 admin（T-450／D-220）；盤口與大單（flow）另走 /api/futures/flow，這裡不給（T-455，省流量）。
// 權限一定在讀資料庫之前擋；盤中每 15 秒會變，不進暫存。
import { fail, guarded, type Env } from '../../_lib/db'
import { NOT_READY, dayOk } from '../../_lib/futures'
import { denied } from '../../_lib/gate'
import { rawJson, readRows } from '../../_lib/scorecard'

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const day = new URL(request.url).searchParams.get('day')
  if (day !== null && !dayOk(day)) return fail(400, `日期格式不對：${day}`)
  return guarded(env, async (db) => {
    const rows = await readRows(() =>
      day === null
        ? db<{ body: string }[]>`SELECT (payload - 'flow')::text AS body FROM serving.futures_paper ORDER BY day DESC LIMIT 1`
        : db<{ body: string }[]>`SELECT (payload - 'flow')::text AS body FROM serving.futures_paper WHERE day = ${day}`,
    )
    if (rows.length === 0) return fail(404, day === null ? NOT_READY : `${day} 沒有小台模擬紀錄`)
    return rawJson(rows[0].body)
  })
}

export const onRequestGet: PagesFunction<Env> = async (ctx) => (await denied(ctx.request, 'futures', ctx.env)) ?? handle(ctx)
