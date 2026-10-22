// 端點依角色擋人（T-430／D-208）：每日名單與績效只給 admin（_lib/roles.ts）。前端藏分頁只是方便，真正擋人的是這裡。
// 一定要在讀暫存（cached）之前判斷：暫存是不分使用者的，先讀暫存會把 admin 的回應交給別人。
import { fail } from './db.ts'
import { accessEmail } from './identity.ts'
import { can, type Feature } from './roles.ts'

type Opts = Parameters<typeof accessEmail>[1]

/** 整支端點都要權限：能用 → null（照常往下）；權杖驗不過 → 401；沒有權限 → 403。 */
export async function denied(request: Request, feature: Feature, opts?: Opts): Promise<Response | null> {
  const email = await accessEmail(request, opts)
  if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
  return can(email, feature) ? null : fail(403, '沒有權限')
}

/** 每個人都能看、只是有權限的多幾欄（首頁、個股評分）：權杖驗不過或沒權限都當成沒有，不擋。 */
export async function allowed(request: Request, feature: Feature, opts?: Opts): Promise<boolean> {
  return can(await accessEmail(request, opts), feature)
}

/** 依權限分開暫存：有權限與沒權限的回應內容不同，鍵要不一樣。 */
export const gatedKey = (key: string, full: boolean): string => `${key}#${full ? 'full' : 'base'}`
