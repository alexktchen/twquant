// POST /api/line/sync：同步發文帳號在哪些群組（T-423）。觸發 GitHub Actions line-login.yml 的 groups 工作，
// 約 1 分鐘後 web.line_groups 更新（順便接受群組邀請），頁面再讀 /api/line 就看得到。只有 line 功能的角色能按。
import { fail, json, type Env } from '../../_lib/db'
import { userEmail } from '../../_lib/identity'
import { dispatchRequest } from '../../_lib/line-admin'
import { can } from '../../_lib/roles'
import { sameOrigin } from '../../_lib/watchlist'

type SyncEnv = Env & { TWQUANT_DISPATCH_TOKEN?: string }

export const onRequestPost: PagesFunction<SyncEnv> = async ({ request, env }) => {
  if (!sameOrigin(request)) return fail(403, '只接受本站送出的請求')
  const email = await userEmail(request, env)
  if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
  if (!can(email, 'line')) return fail(403, '沒有權限')
  let req: ReturnType<typeof dispatchRequest>
  try {
    req = dispatchRequest(env.TWQUANT_DISPATCH_TOKEN, 'groups')
  } catch (e) {
    return fail(503, e instanceof Error ? e.message : String(e))
  }
  const res = await fetch(req.url, req.init)
  if (!res.ok) return fail(502, `觸發 GitHub Actions 失敗（HTTP ${res.status}）：${(await res.text()).slice(0, 300)}`)
  return json({ ok: true })
}
