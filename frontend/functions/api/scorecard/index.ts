// GET /api/scorecard?line=CORE：這條線的名單績效——模擬帳戶的淨值曲線、與 0050 對照、逐筆統計（不含逐筆明細）。只給 admin（T-430／D-208）。
import { fail, guarded, type Env } from '../../_lib/db'
import { cached, cacheKey } from '../../_lib/cache'
import { denied } from '../../_lib/gate'
import { NOT_READY, lineOk, rawJson, readRows } from '../../_lib/scorecard'

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const line = new URL(request.url).searchParams.get('line')
  if (!lineOk(line)) return fail(400, `沒有這條線：${line ?? '（未指定）'}`)
  return guarded(env, async (db) => {
    const rows = await readRows(
      () => db<{ body: string }[]>`
        SELECT (payload #- '{trades,rows}')::text AS body FROM serving.scorecard WHERE line = ${line}`,
    )
    if (rows.length === 0) return fail(404, NOT_READY)
    return rawJson(rows[0].body)
  })
}

// 有權限的人看到都一樣、一天更新兩次：60 秒暫存（_lib/cache.ts）；先擋權限再讀暫存
export const onRequestGet: PagesFunction<Env> = async (ctx) =>
  (await denied(ctx.request, 'score')) ?? cached(cacheKey(ctx.request), async () => handle(ctx))
