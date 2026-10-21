// POST /api/line/post：手動發送一張圖卡（T-423）。{ kind, date } → 觸發 GitHub Actions line-login.yml 的 post 工作：
// 畫那天那一種圖卡、排進隊、送到網站上勾了的群組（同一天同一種同一群組照樣只發一次）。只有 line 功能的角色能按。
import { fail, json, type Env } from '../../_lib/db'
import { accessEmail } from '../../_lib/identity'
import { dispatchRequest, parsePost } from '../../_lib/line-admin'
import { can } from '../../_lib/roles'
import { sameOrigin } from '../../_lib/watchlist'

type PostEnv = Env & { TWQUANT_DISPATCH_TOKEN?: string }

export const onRequestPost: PagesFunction<PostEnv> = async ({ request, env }) => {
  if (!sameOrigin(request)) return fail(403, '只接受本站送出的請求')
  const email = await accessEmail(request)
  if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
  if (!can(email, 'line')) return fail(403, '沒有權限')
  const body = parsePost(await request.text())
  if (body === null) return fail(400, '內容要是 {"kind": "picks|groups|morning|evening", "date": "YYYY-MM-DD"}')
  let req: ReturnType<typeof dispatchRequest>
  try {
    req = dispatchRequest(env.TWQUANT_DISPATCH_TOKEN, 'post', { kind: body.kind, date: body.date })
  } catch (e) {
    return fail(503, e instanceof Error ? e.message : String(e))
  }
  const res = await fetch(req.url, req.init)
  if (!res.ok) return fail(502, `觸發 GitHub Actions 失敗（HTTP ${res.status}）：${(await res.text()).slice(0, 300)}`)
  return json({ ok: true })
}
