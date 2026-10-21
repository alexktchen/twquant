// GET /api/line：「LINE 群組」分頁的資料（T-423）。只有能用 line 功能的角色看得到（_lib/roles.ts）。
import { fail, guarded, json, type Env } from '../../_lib/db'
import { accessEmail } from '../../_lib/identity'
import { lineStatus } from '../../_lib/line-admin'
import { can } from '../../_lib/roles'

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) => {
  const email = await accessEmail(request)
  if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
  if (!can(email, 'line')) return fail(403, '沒有權限')
  return guarded(env, async (db) => json(await lineStatus(db)))
}
