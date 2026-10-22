// PUT /api/line/groups/:id：勾／取消要發的群組（T-423）。只收本站送出的請求、只有 line 功能的角色能改。
import { fail, guarded, json, type Env } from '../../../_lib/db'
import { userEmail } from '../../../_lib/identity'
import { HOME_ID_RE, parseToggle, setGroupEnabled } from '../../../_lib/line-admin'
import { can } from '../../../_lib/roles'
import { sameOrigin } from '../../../_lib/watchlist'

export const onRequestPut: PagesFunction<Env> = async ({ request, env, params }) => {
  if (!sameOrigin(request)) return fail(403, '只接受本站送出的請求')
  const email = await userEmail(request, env)
  if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
  if (!can(email, 'line')) return fail(403, '沒有權限')
  const id = String(params.id ?? '')
  if (!HOME_ID_RE.test(id)) return fail(400, '群組 id 格式不對')
  const body = parseToggle(await request.text())
  if (body === null) return fail(400, '內容要是 {"enabled": true|false}')
  return guarded(env, async (db) => {
    const row = await setGroupEnabled(db, id, body.enabled, email)
    return row ? json(row) : fail(404, '找不到這個群組（先跑 twquant line-group groups 同步）')
  })
}
