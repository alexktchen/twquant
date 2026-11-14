// PUT /api/autotrade/command：遙控指令（autotrade A-016、A-017／D-229）——平倉、暫停／恢復自動交易、啟動／結束交易程式。
// 使用者 2026-11-14 要網站不只能叫停；緊急停止仍然走 ./stop.ts（那一條沒有動）。
// 門檻跟停止一樣：只給 admin，權限一定在動資料庫之前擋；只收本站送出的請求；
// 內容一定要是 {"confirm": 動作, "target": "sim" 或 "live"}，多一個欄位都不收。
// 這支只會新增一列指令，由交易程式或本機後台讀到後執行並回報；30 秒內沒有程式接手就作廢。
// 這裡沒有、也不要加：手動買賣、改設定、解除緊急停止。
import { STOP_BODY_MAX } from '../../_lib/autotrade'
import { commandOk, requestAction } from '../../_lib/autotrade_remote'
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

const command = async ({ request, env }: Ctx, email: string) => {
  if (!sameOrigin(request)) return fail(403, '只接受本站送出的變更')
  const asked = commandOk(await body(request))
  if (asked === null) return fail(400, '內容不對：要 {"confirm": 動作, "target": "sim" 或 "live"}')
  return guarded(env, async (db) => {
    const result = await requestAction(db, email, Date.now(), asked.action, asked.target)
    if (!result.ok) return fail(409, result.detail)
    return rawJson(`{"ok":true,"reused":${result.reused},"requests":${result.view.requests ?? '[]'},"remote":${result.view.remote ?? 'null'}}`)
  })
}

export const onRequestPut: PagesFunction<Env> = async (ctx) => {
  const who = await admit(ctx.request, ctx.env)
  return who instanceof Response ? who : command(ctx, who)
}
