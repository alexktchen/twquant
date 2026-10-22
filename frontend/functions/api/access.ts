// GET /api/access：登入者能用哪些功能（T-423）。前端拿來決定要不要顯示「LINE 群組」分頁；
// 每個功能的端點自己再檢查一次（見 _lib/roles.ts）。
import { fail, json } from '../_lib/db'
import { userEmail, type IdentityEnv } from '../_lib/identity'
import { featuresOf } from '../_lib/roles'

export const onRequestGet: PagesFunction<IdentityEnv> = async ({ request, env }) => {
  const email = await userEmail(request, env)
  if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
  return json({ features: featuresOf(email) })
}
