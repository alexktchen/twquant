// GET /api/scorecard/trades?line=CORE：這條線每一筆新進名單的結果（新到舊）。
import { fail, guarded, type Env } from '../../_lib/db'
import { cached, cacheKey } from '../../_lib/cache'
import { lineOk, rawJson } from '../../_lib/scorecard'

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const line = new URL(request.url).searchParams.get('line')
  if (!lineOk(line)) return fail(400, `沒有這條線：${line ?? '（未指定）'}`)
  return guarded(env, async (db) => {
    const rows = await db<{ body: string | null }[]>`
      SELECT (payload->'trades'->'rows')::text AS body FROM serving.scorecard WHERE line = ${line}`
    if (rows.length === 0 || rows[0].body === null) return fail(404, '這條線的名單績效還沒算出來')
    return rawJson(rows[0].body, 'rows')
  })
}

export const onRequestGet: PagesFunction<Env> = (ctx) => cached(cacheKey(ctx.request), async () => handle(ctx))
