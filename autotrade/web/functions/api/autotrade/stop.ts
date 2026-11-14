// PUT /api/autotrade/stop：遠端緊急停止（autotrade A-011／D-225）。
// 只給 admin，權限一定在動資料庫之前擋；只收本站送出的請求；內容一定要是 {"confirm":"stop"}。
// 這支只會新增一筆「停止」（上一筆還在等就沿用，不疊）；交易程式那邊讀到後執行並回報。
// 這條路只能叫停：沒有解除、沒有開始交易、沒有其他動作，也不要加。
import { STOP_BODY_MAX, confirmOk, requestStop } from '../../_lib/autotrade'
import { admit } from '../../_lib/access'
import { fail, guarded, type Env } from '../../_lib/db'
import { rawJson, sameOrigin } from '../../_lib/http'

type Ctx = Parameters<PagesFunction<Env>>[0]

async function body(request: Request): Promise<unknown> {
  const text = await request.text()
  if (text.length > STOP_BODY_MAX) return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

const stop = async ({ request, env }: Ctx, email: string) => {
  if (!sameOrigin(request)) return fail(403, '只接受本站送出的變更')
  if (!confirmOk(await body(request))) return fail(400, '內容不對：要 {"confirm":"stop"}')
  return guarded(env, async (db) => {
    const result = await requestStop(db, email, Date.now())
    if (!result.ok) return fail(409, result.detail)
    return rawJson(`{"ok":true,"reused":${result.reused},"stop":${result.control.stop ?? 'null'},"listeners":${result.control.listeners ?? '{}'}}`)
  })
}

export const onRequestPut: PagesFunction<Env> = async (ctx) => {
  const who = await admit(ctx.request, ctx.env)
  return who instanceof Response ? who : stop(ctx, who)
}
