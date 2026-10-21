// POST /api/line/login：從網站登入 LINE 發文帳號（T-423）。記下「有人按了登入」→ 觸發 GitHub Actions line-login.yml，
// 那邊掃 QR 的進度（QR 碼、PIN）寫回 web.line_login，頁面每幾秒讀 /api/line 顯示。只有 line 功能的角色能按。
import { fail, guarded, json, type Env } from '../../_lib/db'
import { accessEmail } from '../../_lib/identity'
import { dispatchRequest, loginBusy, loginState, markLoginFailed, markLoginRequested } from '../../_lib/line-admin'
import { can } from '../../_lib/roles'
import { sameOrigin } from '../../_lib/watchlist'

type LoginEnv = Env & { TWQUANT_DISPATCH_TOKEN?: string }

export const onRequestPost: PagesFunction<LoginEnv> = async ({ request, env }) => {
  if (!sameOrigin(request)) return fail(403, '只接受本站送出的請求')
  const email = await accessEmail(request)
  if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
  if (!can(email, 'line')) return fail(403, '沒有權限')
  let req: ReturnType<typeof dispatchRequest>
  try {
    req = dispatchRequest(env.TWQUANT_DISPATCH_TOKEN)
  } catch (e) {
    return fail(503, e instanceof Error ? e.message : String(e))
  }
  return guarded(env, async (db) => {
    if (loginBusy(await loginState(db))) return fail(409, '登入正在進行中，等這一輪結束（或 10 分鐘沒動靜）再按')
    await markLoginRequested(db, email)
    const res = await fetch(req.url, req.init)
    if (!res.ok) {
      const why = `觸發 GitHub Actions 失敗（HTTP ${res.status}）：${(await res.text()).slice(0, 300)}`
      await markLoginFailed(db, why)
      return fail(502, why)
    }
    return json(await loginState(db))
  })
}
