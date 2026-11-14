// GET /api/me：現在登入的是誰（autotrade A-018）。頁面一開就問一次：
// 放行 → 顯示 email；沒放行 → 把原因講出來（登入還沒設定好、登入過期、不在名單上）。這支不碰資料庫。
import { admit, type AccessEnv } from '../_lib/access'

export const onRequestGet: PagesFunction<AccessEnv> = async ({ request, env }) => {
  const who = await admit(request, env)
  if (who instanceof Response) return who
  return new Response(JSON.stringify({ email: who }), {
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'private, no-store' },
  })
}
